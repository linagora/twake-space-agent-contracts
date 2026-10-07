"""Word documents (docx, WordprocessingML): their paragraphs in order, headings as Markdown's,
list items after a dash, and their tables as rows of tab-separated cells."""

import re
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

from twake_space_agent_contracts.documents.archives import (
    Package,
    attribute,
    cell_text,
    child,
    local,
    open_office,
    run_text,
)
from twake_space_agent_contracts.documents.reading import Output

# What holds no text a reader sees: the properties of a paragraph or a run, among which its tab
# stops; deleted or moved text, kept for tracked changes; the codes of fields, whose results are
# the text; and the readings set above words (ruby)
_UNSEEN = frozenset({"pPr", "rPr", "del", "moveFrom", "delText", "instrText", "delInstrText", "rt"})
_HEADING = re.compile(r"heading ([1-9])")
# Style chains longer than any Word writes
_DEEPEST_STYLE = 20


@dataclass(frozen=True)
class _Style:
    name: str
    based_on: str | None
    outline: int | None
    """The outline level of its paragraphs, from 0 for the highest headings."""
    listed: bool
    """Whether its paragraphs are list items."""


@dataclass
class _Table:
    rows: list[list[list[str]]] = field(default_factory=list)
    """Each row's cells, each cell's paragraphs."""


def read(content: bytes, output: Output) -> None:
    package = Package(open_office(content), output)
    document = package.main_part("word/document.xml")
    styles = _styles(package, document)
    tables: list[_Table] = []
    for event, element in package.parsed(document):
        name = local(element.tag)
        if event == "start":
            if name == "tbl":
                tables.append(_Table())
            elif name == "tr" and tables:
                tables[-1].rows.append([])
            elif name == "tc" and tables and tables[-1].rows:
                tables[-1].rows[-1].append([])
            continue
        if name == "p":
            if tables and tables[-1].rows and tables[-1].rows[-1]:
                tables[-1].rows[-1][-1].append(_text(element))
            else:
                _paragraph(element, styles, output)
            element.clear()
        elif name == "tbl" and tables:
            rows = _rows(tables.pop())
            # A table in a cell of another is part of that cell's text
            if tables and tables[-1].rows and tables[-1].rows[-1]:
                tables[-1].rows[-1][-1].append(" ".join(" ".join(row) for row in rows))
            elif rows:
                output.gap()
                output.add("\n".join("\t".join(row) for row in rows))
                output.gap()
            element.clear()


def _rows(table: _Table) -> list[list[str]]:
    """The table's rows that hold any text, each cell on one line, without the empty cells that
    end a row."""
    rows = []
    for row in table.rows:
        cells = [cell_text(" ".join(paragraphs)) for paragraphs in row]
        while cells and not cells[-1]:
            cells.pop()
        if cells:
            rows.append(cells)
    return rows


def _paragraph(element: Element, styles: dict[str, _Style], output: Output) -> None:
    text = _text(element).strip()
    if not text:
        return
    properties = child(element, "pPr")
    style_id = attribute(child(properties, "pStyle"), "val")
    level = _outline(properties)
    if level is None:
        level = _style_outline(styles, style_id)
    numbering = child(properties, "numPr")
    if level is not None:
        output.gap()
        output.add("#" * min(level + 1, 6) + " " + " ".join(text.split()))
    elif _listed(numbering, styles, style_id):
        depth = attribute(child(numbering, "ilvl"), "val") or "0"
        indent = "  " * int(depth) if depth.isdigit() and int(depth) < 9 else ""
        output.add(f"{indent}- {text}")
    else:
        output.add(text)


def _text(paragraph: Element) -> str:
    """The text of a paragraph as a reader sees it, in order."""
    return run_text(paragraph, _UNSEEN)


def _outline(properties: Element | None) -> int | None:
    """The outline level that the paragraph's properties, or a style's, give: 0 to 8 for
    headings, from the highest, and 9 for body text."""
    value = attribute(child(properties, "outlineLvl"), "val")
    if value is None or not value.isdigit():
        return None
    level = int(value)
    return level if level < 9 else None


def _listed(numbering: Element | None, styles: dict[str, _Style], style_id: str | None) -> bool:
    if numbering is not None:
        # Numbering 0 takes the paragraph out of the list its style puts it in
        return attribute(child(numbering, "numId"), "val") != "0"
    return any(style.listed for style in _chain(styles, style_id))


def _style_outline(styles: dict[str, _Style], style_id: str | None) -> int | None:
    """The outline level of the paragraphs of the style, or of the styles it is based on: from its
    own properties, else from its name, as heading 1 or Title."""
    for style in _chain(styles, style_id):
        if style.outline is not None:
            return style.outline
        heading = _HEADING.fullmatch(style.name.lower())
        if heading:
            return int(heading.group(1)) - 1
        if style.name.lower() == "title":
            return 0
    return None


def _chain(styles: dict[str, _Style], style_id: str | None) -> list[_Style]:
    """The style and those it is based on, from the closest."""
    chain: list[_Style] = []
    while style_id is not None and style_id in styles and len(chain) < _DEEPEST_STYLE:
        style = styles[style_id]
        chain.append(style)
        style_id = style.based_on
    return chain


def _styles(package: Package, document: str) -> dict[str, _Style]:
    """The document's paragraph styles, by id."""
    parts = [
        link.target
        for link in package.relationships(document)
        if link.kind == "styles" and package.has(link.target)
    ]
    styles: dict[str, _Style] = {}
    if not parts:
        return styles
    for event, element in package.parsed(parts[0]):
        if event != "end" or local(element.tag) != "style":
            continue
        style_id = attribute(element, "styleId")
        if attribute(element, "type") == "paragraph" and style_id is not None:
            properties = child(element, "pPr")
            styles[style_id] = _Style(
                name=attribute(child(element, "name"), "val") or "",
                based_on=attribute(child(element, "basedOn"), "val"),
                outline=_outline(properties),
                listed=child(properties, "numPr") is not None,
            )
        element.clear()
    return styles
