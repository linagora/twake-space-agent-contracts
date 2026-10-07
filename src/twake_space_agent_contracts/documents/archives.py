"""What the documents kept in a zip share, as Office Open XML and OpenDocument keep theirs: the zip,
refused when it would unpack into more than the service reads, as a zip bomb does, and its XML
parts, read as they unpack, without a document type declaration, where XML bombs and external
entities hide."""

import io
import posixpath
import struct
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote
from xml.etree.ElementTree import Element, TreeBuilder
from xml.parsers import expat

from twake_space_agent_contracts.documents.reading import Encrypted, Output, TooLarge, Unreadable

CHUNK = 65_536
"""The bytes of a part parsed at a time."""
MOST_FILES = 10_000
"""The files a zip holds at most: a document holds a few dozen, a deck of a thousand slides a few
thousand."""
LARGEST_DIRECTORY = 4_194_304
"""The bytes of the zip's directory at most, which lists its files: 4 MiB, some 400 bytes for each
file it may hold."""
LARGEST_UNPACKED = 268_435_456
"""The bytes a zip's files take at most once uncompressed, all together: 256 MiB."""
HIGHEST_RATIO = 100
"""How many times smaller than its content a file of the zip may be compressed, once it takes more
than RATIO_FROM uncompressed: XML is some ten times smaller compressed, a zip bomb thousands of
times."""
RATIO_FROM = 1_048_576
METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
"""How the files of a zip may be compressed: stored as they are, or deflated, as Office and
LibreOffice do; the zip module unpacks the others, such as bzip2 or LZMA, without bounding what
they give."""

Events = Iterator[tuple[str, Element]]

# The record that ends a zip, and the longest comment it may end with
_END = b"PK\x05\x06"
_END_SIZE = 22
_LONGEST_COMMENT = 65_535
# What locates the ZIP64 record of a zip's directory, right before the record that ends the zip
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
# How a compound file starts, and the name of the stream where Office keeps a document it encrypts
_COMPOUND_FILE = bytes.fromhex("d0cf11e0a1b11ae1")
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")
# The copy of some content that Office writes for the readers that do not know it, beside it
_FALLBACK = "http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def open_office(content: bytes) -> zipfile.ZipFile:
    """The zip of an Office Open XML document. A document protected by a password is no zip, but
    a compound file, the container of Office's older documents, which names the zip encrypted
    EncryptedPackage."""
    if content.startswith(_COMPOUND_FILE):
        if _ENCRYPTED_PACKAGE in content:
            raise Encrypted("the document is protected by a password")
        raise Unreadable("an older Office document")
    return open_zip(content)


def open_zip(content: bytes) -> zipfile.ZipFile:
    """The zip, once found within what the service unpacks: as many files as its directory lists,
    and as many bytes as each says it holds, since the zip module never unpacks more of a file than
    it says. Each of those sizes is checked before any file is unpacked."""
    end = _end(content)
    if end is not None:
        # The zip module would take the counts of a ZIP64 record in place of those checked here:
        # the format is for zips of more files, or bytes, than any the service reads
        if end >= _ZIP64_LOCATOR_SIZE and content.startswith(
            _ZIP64_LOCATOR, end - _ZIP64_LOCATOR_SIZE
        ):
            raise Unreadable("a zip of the ZIP64 format")
        files, size = struct.unpack_from("<HI", content, end + 10)
        # Before the zip module reads the directory, which takes memory for each file it lists
        if files > MOST_FILES or size > LARGEST_DIRECTORY:
            raise TooLarge("the zip lists too many files")
    archive = zipfile.ZipFile(io.BytesIO(content))
    files_listed = archive.infolist()
    if len(files_listed) > MOST_FILES:
        raise TooLarge("the zip lists too many files")
    if sum(file.file_size for file in files_listed) > LARGEST_UNPACKED:
        raise TooLarge("the zip unpacks into too much")
    for file in files_listed:
        if file.compress_type not in METHODS:
            raise Unreadable("a file of the zip is compressed as no office application does")
        if file.file_size > RATIO_FROM and file.file_size > HIGHEST_RATIO * file.compress_size:
            raise TooLarge("a file of the zip is compressed too many times")
        # Encrypted with a password, which the zip module asks for to unpack it
        if file.flag_bits & 1:
            raise Encrypted("a file of the zip is encrypted")
    return archive


def _end(content: bytes) -> int | None:
    """Where the record that ends the zip starts, which says how many files its directory lists and
    how many bytes it takes, found as the zip module finds it; None without such a record."""
    end = len(content) - _END_SIZE
    if end < 0:
        return None
    # Ended by its record, without a comment, or else by a comment after it
    if content[end : end + 4] != _END or content[-2:] != b"\0\0":
        end = content.rfind(_END, max(0, len(content) - _END_SIZE - _LONGEST_COMMENT))
        if end < 0 or end + _END_SIZE > len(content):
            return None
    return end


def local(name: str) -> str:
    """An element's or an attribute's name without its namespace: Office writes the same names in
    the namespaces of its transitional and strict forms."""
    return name.rpartition("}")[2]


def attribute(element: Element | None, name: str) -> str | None:
    """The value of the element's attribute of that name, whatever its namespace."""
    if element is None:
        return None
    for key, value in element.attrib.items():
        if local(key) == name:
            return value
    return None


def relationship_id(element: Element) -> str | None:
    """The id of the relationship through which the element links to another part, apart from
    its own id, which has no namespace."""
    for key, value in element.attrib.items():
        if key.startswith("{") and local(key) == "id":
            return value
    return None


def child(element: Element | None, name: str) -> Element | None:
    """The element's first child of that name, whatever its namespace."""
    if element is None:
        return None
    for found in element:
        if local(found.tag) == name:
            return found
    return None


def _handed(events: list[tuple[str, Element, Element | None, bool]]) -> Events:
    """The events of a part parsed at a time, each handed over in turn; then the elements to let
    go of, cleared, leave those they sit in, each of these rebuilt once."""
    gone: dict[int, tuple[Element, set[int]]] = {}
    for event, element, holder, letting_go in events:
        yield event, element
        if letting_go:
            element.clear()
            if holder is not None:
                gone.setdefault(id(holder), (holder, set()))[1].add(id(element))
    for holder, elements in gone.values():
        holder[:] = [kept for kept in holder if id(kept) not in elements]


def _named(name: str) -> str:
    """A name as expat gives it, <namespace>}<name>, as ElementTree writes it."""
    return "{" + name if "}" in name else name


@dataclass(frozen=True)
class Relationship:
    """A link from a part of an Office Open XML document to another of its parts."""

    kind: str
    """The last segment of its type, such as styles."""
    target: str
    """The name of the part it links to, in the zip."""
    id: str


class Package:
    """The zip of a document, whose parts are read for an output, until its deadline."""

    def __init__(self, archive: zipfile.ZipFile, output: Output) -> None:
        self._archive = archive
        self._output = output

    def has(self, name: str) -> bool:
        try:
            self._archive.getinfo(name)
        except KeyError:
            return False
        return True

    def parsed(self, name: str, units: frozenset[str] = frozenset()) -> Events:
        """The start and the end of each element of an XML part, as the part unpacks. A unit, by
        its name with or without its namespace, comes whole at its end, such as a paragraph with
        its runs. Once handed over, a unit is let go of, as is any element that ends outside a
        unit, so that the part's tree holds only what is open and the unit being read, whatever
        the part holds.

        A part that declares a document type is refused: no entity is ever declared, so none is
        expanded or fetched. The copies of content that Office writes for the readers that do not
        know it are left out, which would give their text twice. Between the parts of the XML
        parsed at a time, the reading stops once its deadline passed."""
        builder = TreeBuilder()
        parser = expat.ParserCreate(namespace_separator="}")
        # Each start and end, with, for an end, the element it sits in and whether to let go of it
        events: list[tuple[str, Element, Element | None, bool]] = []
        # The elements started and not ended, the innermost last, each with whether it is a unit
        opened: list[tuple[Element, bool]] = []
        within_units = 0
        # How deep within such a copy the parser is
        within_copy = 0

        def start(tag: str, attributes: dict[str, str]) -> None:
            nonlocal within_copy, within_units
            if within_copy or tag == _FALLBACK:
                within_copy += 1
                return
            named = {_named(key): value for key, value in attributes.items()}
            element = builder.start(_named(tag), named)
            unit = _named(tag) in units or local(tag) in units
            within_units += unit
            opened.append((element, unit))
            events.append(("start", element, None, False))

        def end(tag: str) -> None:
            nonlocal within_copy, within_units
            if within_copy:
                within_copy -= 1
                return
            element = builder.end(_named(tag))
            _, unit = opened.pop()
            within_units -= unit
            holder = opened[-1][0] if opened else None
            events.append(("end", element, holder, unit or not within_units))

        def data(text: str) -> None:
            if not within_copy:
                builder.data(text)

        def refuse(*_: object) -> None:
            raise Unreadable("a part declares a document type")

        parser.buffer_text = True
        parser.StartElementHandler = start
        parser.EndElementHandler = end
        parser.CharacterDataHandler = data
        parser.StartDoctypeDeclHandler = refuse
        parser.EntityDeclHandler = refuse
        parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
        with self._archive.open(name) as part:
            while chunk := part.read(CHUNK):
                parser.Parse(chunk, False)
                yield from _handed(events)
                events.clear()
                if len(chunk) == CHUNK:
                    self._output.tick()
            parser.Parse(b"", True)
            yield from _handed(events)

    def relationships(self, part: str) -> list[Relationship]:
        """The links from the part, or from the package itself for "", to the other parts of the
        document: those outside it, such as web addresses, left out. A part links to as many parts
        at most as a zip holds files."""
        folder, name = posixpath.split(part)
        links = posixpath.join(folder, "_rels", f"{name}.rels")
        if not self.has(links):
            return []
        found: list[Relationship] = []
        for event, element in self.parsed(links):
            if event != "end" or local(element.tag) != "Relationship":
                continue
            if element.get("TargetMode") != "External":
                if len(found) == MOST_FILES:
                    raise Unreadable("a part links to more parts than a zip holds files")
                target = unquote(element.get("Target", ""))
                path = target[1:] if target.startswith("/") else posixpath.join(folder, target)
                found.append(
                    Relationship(
                        kind=element.get("Type", "").rpartition("/")[2],
                        target=posixpath.normpath(path),
                        id=element.get("Id", ""),
                    )
                )
        return found

    def main_part(self, usual: str) -> str:
        """The document's main part, as its package names it, else where Office puts it."""
        for relationship in self.relationships(""):
            if relationship.kind == "officeDocument" and self.has(relationship.target):
                return relationship.target
        if not self.has(usual):
            raise Unreadable("no main part")
        return usual


def run_text(element: Element, unseen: frozenset[str] = frozenset()) -> str:
    """The text of the runs within an element of Office Open XML, such as a paragraph, in order:
    the text of its t elements, its tabs and its breaks, but for the elements it holds that a reader
    does not see."""
    pieces = []
    found = [element]
    while found:
        inner = found.pop()
        name = local(inner.tag)
        if name in unseen:
            continue
        if name == "t":
            pieces.append(inner.text or "")
        elif name in ("tab", "ptab"):
            pieces.append("\t")
        elif name in ("br", "cr"):
            pieces.append("\n")
        elif name == "noBreakHyphen":
            pieces.append("-")
        found.extend(reversed(inner))
    return "".join(pieces)
