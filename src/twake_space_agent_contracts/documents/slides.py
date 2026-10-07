"""PowerPoint presentations (pptx, PresentationML): slide by slide, each under a heading that
numbers it and gives its title, then the text of its other shapes in their order, its tables as
rows of tab-separated cells, and its speaker notes under a heading of their own."""

from xml.etree.ElementTree import Element

from twake_space_agent_contracts.documents import layout
from twake_space_agent_contracts.documents.archives import (
    Package,
    attribute,
    child,
    local,
    open_office,
    relationship_id,
    run_text,
)
from twake_space_agent_contracts.documents.reading import Output

# What holds no text a reader sees: the properties of a paragraph or of a run
_UNSEEN = frozenset({"pPr", "rPr", "endParaRPr"})
_TITLES = frozenset({"title", "ctrTitle"})
# The elements of a slide that hold its text: its shapes, and the frames of its tables
_SHAPES = frozenset({"sp", "graphicFrame"})
# The placeholders a slide's master fills in, such as its number: not what the slide says
_FOOTERS = frozenset({"sldNum", "dt", "ftr", "hdr"})


def read(content: bytes, output: Output) -> None:
    package = Package(open_office(content), output)
    presentation = package.main_part("ppt/presentation.xml")
    links = {link.id: link for link in package.relationships(presentation)}
    slides = []
    for event, element in package.parsed(presentation):
        if event == "end" and local(element.tag) == "sldId":
            link = links.get(relationship_id(element) or "")
            if link is not None and link.kind == "slide" and package.has(link.target):
                slides.append(link.target)
    for number, slide in enumerate(slides, start=1):
        _slide(package, slide, number, output)


def _slide(package: Package, part: str, number: int, output: Output) -> None:
    hidden = False
    titles: list[str] = []
    lines: list[str] = []
    for event, element in package.parsed(part, units=_SHAPES):
        name = local(element.tag)
        if event == "start":
            # Kept out of the slide show
            hidden = hidden or (name == "sld" and attribute(element, "show") in ("0", "false"))
        elif name == "sp":
            kind = _placeholder(element)
            text = _shape_text(element)
            if kind in _TITLES:
                titles.append(" ".join(text.split()))
            elif kind not in _FOOTERS and text:
                lines.append(text)
        elif name == "graphicFrame":
            lines.extend(layout.rows(_cells(element)))
    layout.slide(
        output,
        number,
        hidden=hidden,
        title=" ".join(title for title in titles if title),
        lines=lines,
        notes=_notes(package, part),
    )


def _placeholder(shape: Element) -> str | None:
    """The kind of placeholder the shape is, such as title, or None for a shape of its own."""
    properties = child(child(shape, "nvSpPr"), "nvPr")
    placeholder = child(properties, "ph")
    if placeholder is None:
        return None
    return attribute(placeholder, "type") or "body"


def _shape_text(shape: Element) -> str:
    """The text of the shape, a line for each of its paragraphs."""
    body = child(shape, "txBody")
    if body is None:
        return ""
    paragraphs = [run_text(paragraph, _UNSEEN) for paragraph in body if local(paragraph.tag) == "p"]
    return "\n".join(paragraphs).strip()


def _cells(frame: Element) -> list[list[str]]:
    """The text of each cell of each row of the tables in the frame."""
    return [
        [_shape_text(cell) for cell in row if local(cell.tag) == "tc"]
        for table in frame.iter()
        if local(table.tag) == "tbl"
        for row in table
        if local(row.tag) == "tr"
    ]


def _notes(package: Package, slide: str) -> str:
    """The slide's speaker notes, in the body of its notes page."""
    parts = [
        link.target
        for link in package.relationships(slide)
        if link.kind == "notesSlide" and package.has(link.target)
    ]
    notes = []
    for part in parts[:1]:
        for event, element in package.parsed(part, units=frozenset({"sp"})):
            if event == "end" and local(element.tag) == "sp" and _placeholder(element) == "body":
                notes.append(_shape_text(element))
    return "\n".join(note for note in notes if note)
