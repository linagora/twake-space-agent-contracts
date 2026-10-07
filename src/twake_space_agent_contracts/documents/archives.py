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

from twake_space_agent_contracts.documents.reading import Encrypted, TooLarge, Unreadable

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

Events = Iterator[tuple[str, Element]]

# The record that ends a zip, and the longest comment it may end with
_END = b"PK\x05\x06"
_END_SIZE = 22
_LONGEST_COMMENT = 65_535
# How a compound file starts, and the name of the stream where Office keeps a document it encrypts
_COMPOUND_FILE = bytes.fromhex("d0cf11e0a1b11ae1")
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")


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
    listed = _directory(content)
    if listed is not None:
        files, size = listed
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
        if file.file_size > RATIO_FROM and file.file_size > HIGHEST_RATIO * file.compress_size:
            raise TooLarge("a file of the zip is compressed too many times")
        # Encrypted with a password, which the zip module asks for to unpack it
        if file.flag_bits & 1:
            raise Encrypted("a file of the zip is encrypted")
    return archive


def _directory(content: bytes) -> tuple[int, int] | None:
    """How many files the zip's directory lists, and how many bytes it takes, as the record that
    ends the zip says, found as the zip module finds it; None without such a record."""
    end = len(content) - _END_SIZE
    if end < 0:
        return None
    # Ended by its record, without a comment, or else by a comment after it
    if content[end : end + 4] != _END or content[-2:] != b"\0\0":
        end = content.rfind(_END, max(0, len(content) - _END_SIZE - _LONGEST_COMMENT))
        if end < 0 or end + _END_SIZE > len(content):
            return None
    files, size = struct.unpack_from("<HI", content, end + 10)
    return files, size


def has(archive: zipfile.ZipFile, name: str) -> bool:
    try:
        archive.getinfo(name)
    except KeyError:
        return False
    return True


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


def child(element: Element | None, name: str) -> Element | None:
    """The element's first child of that name, whatever its namespace."""
    if element is None:
        return None
    for found in element:
        if local(found.tag) == name:
            return found
    return None


def parsed(archive: zipfile.ZipFile, name: str) -> Events:
    """The start and the end of each element of an XML part, as the part unpacks: an element comes
    whole at its end, until it is cleared. A part that declares a document type is refused: no
    entity is ever declared, so none is expanded or fetched."""
    builder = TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    events: list[tuple[str, Element]] = []

    def start(tag: str, attributes: dict[str, str]) -> None:
        named = {_named(key): value for key, value in attributes.items()}
        events.append(("start", builder.start(_named(tag), named)))

    def end(tag: str) -> None:
        events.append(("end", builder.end(_named(tag))))

    def refuse(*_: object) -> None:
        raise Unreadable("a part declares a document type")

    parser.buffer_text = True
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    with archive.open(name) as part:
        while chunk := part.read(CHUNK):
            parser.Parse(chunk, False)
            yield from events
            events.clear()
        parser.Parse(b"", True)
        yield from events


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


def relationships(archive: zipfile.ZipFile, part: str) -> list[Relationship]:
    """The links from the part, or from the package itself for "", to the other parts of the
    document: those outside it, such as web addresses, left out."""
    folder, name = posixpath.split(part)
    links = posixpath.join(folder, "_rels", f"{name}.rels")
    if not has(archive, links):
        return []
    found = []
    for event, element in parsed(archive, links):
        if event != "end" or local(element.tag) != "Relationship":
            continue
        if element.get("TargetMode") != "External":
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


def main_part(archive: zipfile.ZipFile, usual: str) -> str:
    """The document's main part, as its package names it, else where Office puts it."""
    for relationship in relationships(archive, ""):
        if relationship.kind == "officeDocument" and has(archive, relationship.target):
            return relationship.target
    if not has(archive, usual):
        raise Unreadable("no main part")
    return usual


def cell_text(text: str) -> str:
    """Text that sits in a cell of a row: on one line, without the tabs that part cells."""
    return " ".join(text.split())
