"""OpenDocument text, spreadsheets and presentations (odt, ods, odp), as LibreOffice writes them:
their content part, read as Office's documents are read, and laid out as they are. A document
protected by a password says so in its manifest, its content being encrypted."""

import re
from datetime import datetime
from xml.etree.ElementTree import Element

from twake_space_agent_contracts.documents import MOST_COLUMNS, MOST_ROWS, layout
from twake_space_agent_contracts.documents.archives import Package, open_zip
from twake_space_agent_contracts.documents.reading import Encrypted, Output, Unreadable

MOST_SPACES = 100
"""The spaces that one element of spaces gives at most, whatever count it says."""

_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"
_PRESENTATION = "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}"
_MANIFEST = "{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}"

_PARAGRAPHS = frozenset({f"{_TEXT}p", f"{_TEXT}h"})
_CELLS = frozenset({f"{_TABLE}table-cell", f"{_TABLE}covered-table-cell"})
# What a reader does not see where it sits in the text: the text tracked changes deleted, notes,
# comments, and the reading set above a word; and also the templates of an index, whose
# "-source" elements end their names
_UNSEEN = frozenset(
    {f"{_TEXT}tracked-changes", f"{_TEXT}note", f"{_OFFICE}annotation", f"{_TEXT}ruby-text"}
)
# The shapes that hold text on a slide
_SHAPES = frozenset(
    f"{_DRAW}{name}"
    for name in (
        "frame",
        "custom-shape",
        "rect",
        "ellipse",
        "circle",
        "polygon",
        "regular-polygon",
        "polyline",
        "path",
        "line",
        "connector",
        "caption",
        "measure",
    )
)
# What the master page of a slide fills in, such as its number: not what the slide says
_MASTER_FIELDS = frozenset({"page-number", "footer", "header", "date-time", "page"})
# The blanks of a text, which collapse into one space where the text has no element for them
_BLANKS = re.compile(r"[ \t\r\n]+")
# A time of a cell, as ISO 8601 writes a duration
_DURATION = re.compile(r"PT(\d+)H(\d+)M(\d+)(?:\.\d+)?S")


def read_text(content: bytes, output: Output) -> None:
    """A text document: its headings, paragraphs, list items and tables, as a Word document's."""
    package = _open(content, output)
    # The paragraph styles of titles, Title and those derived from it, as LibreOffice derives one
    # for the title of a document it converts
    titles = {"Title"}
    tables: list[list[list[list[str]]]] = []
    lists = unseen = 0
    for event, element in package.parsed("content.xml", units=_PARAGRAPHS):
        tag = element.tag
        if _unseen(tag):
            unseen += 1 if event == "start" else -1
            if event == "end":
                _clear(element)
            continue
        if unseen:
            continue
        if event == "start":
            if tag == f"{_TEXT}list":
                lists += 1
            elif tag == f"{_TABLE}table":
                tables.append([])
            elif tag == f"{_TABLE}table-row" and tables:
                tables[-1].append([])
            elif tag in _CELLS and tables and tables[-1]:
                tables[-1][-1].append([])
            continue
        if tag == f"{_STYLE}style":
            if element.get(f"{_STYLE}parent-style-name") in titles:
                titles.add(element.get(f"{_STYLE}name", ""))
        elif tag == f"{_TEXT}list":
            lists -= 1
        elif tag in _PARAGRAPHS:
            text = _text(element)
            if tables and tables[-1] and tables[-1][-1]:
                tables[-1][-1][-1].append(text)
            elif tag == f"{_TEXT}h":
                layout.paragraph(output, text, level=_count(element.get(f"{_TEXT}outline-level")))
            elif element.get(f"{_TEXT}style-name") in titles:
                layout.paragraph(output, text, level=1)
            else:
                layout.paragraph(output, text, depth=lists - 1 if lists else None)
        elif tag in _CELLS and tables and tables[-1] and tables[-1][-1]:
            row = tables[-1][-1]
            repeated = min(_count(element.get(f"{_TABLE}number-columns-repeated")), MOST_COLUMNS)
            row.extend(list(row[-1]) for _ in range(repeated - 1))
        elif tag == f"{_TABLE}table" and tables:
            cells = [[" ".join(paragraphs) for paragraphs in row] for row in tables.pop()]
            # A table in a cell of another is part of that cell's text
            if tables and tables[-1] and tables[-1][-1]:
                tables[-1][-1][-1].append(" ".join(layout.rows(cells)))
            else:
                layout.table(output, cells)


def read_spreadsheet(content: bytes, output: Output) -> None:
    """A spreadsheet: sheet by sheet, the values of its rows, as an Excel spreadsheet's."""
    package = _open(content, output)
    hidden_styles: set[str] = set()
    number = 0
    sheet = _Sheet("", hidden=False)
    # How deep within tables the parser is: a cell may hold a table of its own
    tables = 0
    units = frozenset({f"{_TABLE}table-row", f"{_STYLE}style"})
    for event, element in package.parsed("content.xml", units=units):
        tag = element.tag
        if event == "start":
            if tag == f"{_TABLE}table":
                tables += 1
                if tables == 1:
                    number += 1
                    sheet = _Sheet(
                        name=" ".join(element.get(f"{_TABLE}name", "").split()),
                        hidden=element.get(f"{_TABLE}style-name") in hidden_styles,
                    )
            continue
        if tag == f"{_STYLE}style":
            # Hidden from the sheets' tabs
            if any(inner.get(f"{_TABLE}display") == "false" for inner in element):
                hidden_styles.add(element.get(f"{_STYLE}name", ""))
        elif tag == f"{_TABLE}table-row" and tables == 1:
            sheet.add(element)
            output.tick()
        elif tag == f"{_TABLE}table":
            tables -= 1
            if tables:
                continue
            layout.sheet(
                output,
                number,
                name=sheet.name,
                hidden=sheet.hidden,
                values=sheet.values,
                more_rows=sheet.more_rows,
                more_columns=sheet.more_columns,
            )


def read_presentation(content: bytes, output: Output) -> None:
    """A presentation: slide by slide, with its notes, as a PowerPoint presentation's."""
    package = _open(content, output)
    hidden_styles: set[str] = set()
    number = 0
    hidden = False
    titles: list[str] = []
    lines: list[str] = []
    notes: list[str] = []
    within_notes = 0
    for event, element in package.parsed("content.xml", units=_SHAPES | {f"{_STYLE}style"}):
        tag = element.tag
        if event == "start":
            if tag == f"{_DRAW}page":
                number += 1
                hidden = element.get(f"{_DRAW}style-name") in hidden_styles
                titles, lines, notes = [], [], []
            elif tag == f"{_PRESENTATION}notes":
                within_notes += 1
            continue
        if tag == f"{_STYLE}style":
            # Kept out of the slide show
            if any(inner.get(f"{_PRESENTATION}visibility") == "hidden" for inner in element):
                hidden_styles.add(element.get(f"{_STYLE}name", ""))
        elif tag == f"{_PRESENTATION}notes":
            within_notes -= 1
        elif tag in _SHAPES:
            kind = element.get(f"{_PRESENTATION}class")
            shown = _lines(element)
            if within_notes:
                notes.extend(shown if kind == "notes" else [])
            elif kind == "title":
                titles.append(" ".join(" ".join(shown).split()))
            elif kind not in _MASTER_FIELDS:
                lines.extend(shown)
        elif tag == f"{_DRAW}page":
            layout.slide(
                output,
                number,
                hidden=hidden,
                title=" ".join(title for title in titles if title),
                lines=lines,
                notes="\n".join(notes),
            )


class _Sheet:
    """The values of a sheet's first rows that hold any, and of their first columns."""

    def __init__(self, name: str, hidden: bool) -> None:
        self.name = name
        self.hidden = hidden
        self.values: list[list[str]] = []
        self.more_rows = False
        self.more_columns = False

    def add(self, row: Element) -> None:
        """Adds the row's values, as many times as it is repeated, as far as is read: a row a sheet
        repeats to its end is never unfolded."""
        values: dict[int, str] = {}
        column = 0
        for cell in row:
            if cell.tag not in _CELLS:
                continue
            repeated = _count(cell.get(f"{_TABLE}number-columns-repeated"))
            value = _value(cell) if cell.tag == f"{_TABLE}table-cell" else ""
            if value:
                for index in range(column + 1, min(column + repeated, MOST_COLUMNS) + 1):
                    values[index] = value
                self.more_columns = self.more_columns or column + repeated > MOST_COLUMNS
            column += repeated
        if not values:
            return
        line = [values.get(index, "") for index in range(1, max(values) + 1)]
        for _ in range(_count(row.get(f"{_TABLE}number-rows-repeated"))):
            if len(self.values) == MOST_ROWS:
                self.more_rows = True
                return
            self.values.append(line)


def _open(content: bytes, output: Output) -> Package:
    package = Package(open_zip(content), output)
    if package.has("META-INF/manifest.xml"):
        for event, element in package.parsed("META-INF/manifest.xml"):
            if event == "end" and element.tag == f"{_MANIFEST}encryption-data":
                raise Encrypted("the document is protected by a password")
    if not package.has("content.xml"):
        raise Unreadable("no content")
    return package


def _unseen(tag: str) -> bool:
    return tag in _UNSEEN or (tag.startswith(_TEXT) and tag.endswith("-source"))


def _clear(element: Element) -> None:
    """Clears the element, but for the text that follows it, which belongs to its parent."""
    tail = element.tail
    element.clear()
    element.tail = tail


def _count(value: str | None) -> int:
    """A count an attribute gives, such as of repeated cells, 1 by default."""
    return int(value) if value is not None and value.isdigit() and int(value) > 0 else 1


def _text(paragraph: Element) -> str:
    """The text of a paragraph or a heading, as a reader sees it: its own blanks collapse into a
    space, where its spaces, tabs and line breaks written as elements show as they are."""
    pieces: list[str] = []
    _inline(paragraph, pieces)
    return "".join(pieces).strip(" ")


def _inline(element: Element, pieces: list[str]) -> None:
    if element.text:
        pieces.append(_BLANKS.sub(" ", element.text))
    for inner in element:
        if inner.tag == f"{_TEXT}s":
            pieces.append(" " * min(_count(inner.get(f"{_TEXT}c")), MOST_SPACES))
        elif inner.tag == f"{_TEXT}tab":
            pieces.append("\t")
        elif inner.tag == f"{_TEXT}line-break":
            pieces.append("\n")
        # A shape's paragraphs come apart, as paragraphs of their own
        elif not _unseen(inner.tag) and not inner.tag.startswith(_DRAW):
            _inline(inner, pieces)
        if inner.tail:
            pieces.append(_BLANKS.sub(" ", inner.tail))


def _lines(element: Element) -> list[str]:
    """The paragraphs within the element, each a line, and its tables, each row a line."""
    lines: list[str] = []
    for inner in element:
        if inner.tag in _PARAGRAPHS:
            text = _text(inner)
            if text:
                lines.append(text)
        elif inner.tag == f"{_TABLE}table":
            lines.extend(layout.rows(_cells(inner)))
        elif not _unseen(inner.tag):
            lines.extend(_lines(inner))
    return lines


def _cells(table: Element) -> list[list[str]]:
    """The text of each cell of each row of a table, as many times as it is repeated, as far as
    rows are read."""
    rows = []
    for row in table.iter(f"{_TABLE}table-row"):
        cells: list[str] = []
        for cell in row:
            if cell.tag in _CELLS:
                repeated = _count(cell.get(f"{_TABLE}number-columns-repeated"))
                text = " ".join(_lines(cell))
                cells.extend([text] * min(repeated, MOST_COLUMNS - len(cells)))
        rows.append(cells)
    return rows


def _value(cell: Element) -> str:
    """The value of a cell, whatever language shows it, as an Excel spreadsheet's: a number, a day
    and a time, a time, TRUE or FALSE, or else its text."""
    match cell.get(f"{_OFFICE}value-type"):
        case "float" | "percentage" | "currency":
            try:
                number = float(cell.get(f"{_OFFICE}value", ""))
            except ValueError:
                pass
            else:
                return layout.number(number)
        case "date":
            try:
                return layout.moment(datetime.fromisoformat(cell.get(f"{_OFFICE}date-value", "")))
            except ValueError:
                pass
        case "time":
            duration = _DURATION.fullmatch(cell.get(f"{_OFFICE}time-value", ""))
            if duration:
                hours, minutes, seconds = (int(part) for part in duration.groups())
                shown = f"{hours:02d}:{minutes:02d}"
                return shown if seconds == 0 else f"{shown}:{seconds:02d}"
        case "boolean":
            return "TRUE" if cell.get(f"{_OFFICE}boolean-value") == "true" else "FALSE"
    return layout.cell_text(" ".join(_lines(cell)))
