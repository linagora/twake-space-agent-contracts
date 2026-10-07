"""What the documents kept in a zip share, as Office Open XML and OpenDocument keep theirs: the zip,
and its XML parts, read as they unpack, without a document type declaration, where XML bombs and
external entities hide."""

import io
import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote
from xml.etree.ElementTree import Element, TreeBuilder
from xml.parsers import expat

from twake_space_agent_contracts.documents.reading import Unreadable

CHUNK = 65_536
"""The bytes of a part parsed at a time."""

Events = Iterator[tuple[str, Element]]


def open_zip(content: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(content))


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
