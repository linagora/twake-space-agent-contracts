"""Excel spreadsheets (xlsx, SpreadsheetML): sheet by sheet, each under a heading that numbers it
and gives its name, then each of its rows that hold values on a line, its values parted by tabs
from the first column: the values Excel last computed, never its formulas, dates as days and
times. Only the first MOST_ROWS rows and MOST_COLUMNS columns of a sheet come, as a note says."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from xml.etree.ElementTree import Element

from twake_space_agent_contracts.documents import MOST_COLUMNS, MOST_ROWS
from twake_space_agent_contracts.documents.archives import (
    Package,
    attribute,
    cell_text,
    child,
    local,
    open_office,
    relationship_id,
    run_text,
)
from twake_space_agent_contracts.documents.reading import Output

# What holds no text a reader sees: the properties of a run, and the readings set above words
_UNSEEN = frozenset({"rPr", "rPh"})
# The formats built into Excel that show a date or a time, by id, those of Asian languages included
_DATE_FORMATS = frozenset({*range(14, 23), *range(27, 37), *range(45, 48), *range(50, 59)})
# In a format of Excel's own: its text, which shows as it is, its padding and fillers; the elapsed
# hours, minutes or seconds; its colours, conditions and languages; and what shows dates and times
_LITERAL = re.compile(r'"[^"]*"|\\.|[_*].')
_ELAPSED = re.compile(r"\[(h+|m+|s+)\]", re.IGNORECASE)
_BRACKETED = re.compile(r"\[[^\]]*\]")
_DATE_PART = re.compile(r"[dmyhs]", re.IGNORECASE)
# A character that Excel writes _xHHHH_ in a text, such as a carriage return, which XML loses
_ESCAPED = re.compile(r"_x([0-9A-Fa-f]{4})_")
_COLUMN = re.compile(r"[A-Za-z]{1,3}")
_LAST_DAY = 2_958_465
"""The day 9999-12-31, the last a date of Excel's may be, counted from 1900."""

Value = str | int
"""A cell's value as text, or the index of its text among the workbook's shared strings."""


@dataclass
class _Sheet:
    rows: list[list[Value]] = field(default_factory=list)
    more_rows: bool = False
    more_columns: bool = False


@dataclass(frozen=True)
class _Tab:
    """A sheet as the workbook lists it."""

    name: str
    hidden: bool
    part: str | None
    """Its cells' part, None for a sheet without cells, such as a chart's."""


def read(content: bytes, output: Output) -> None:
    package = Package(open_office(content), output)
    workbook = package.main_part("xl/workbook.xml")
    links = {link.id: link for link in package.relationships(workbook)}
    parts = {link.kind: link.target for link in links.values() if package.has(link.target)}
    dates = _date_styles(package, parts["styles"]) if "styles" in parts else frozenset()
    date1904 = False
    tabs = []
    for event, element in package.parsed(workbook):
        if event != "end":
            continue
        name = local(element.tag)
        if name == "workbookPr":
            date1904 = attribute(element, "date1904") in ("1", "true")
        elif name == "sheet":
            link = links.get(relationship_id(element) or "")
            worksheet = link is not None and link.kind == "worksheet" and package.has(link.target)
            tabs.append(
                _Tab(
                    name=" ".join((attribute(element, "name") or "").split()),
                    hidden=attribute(element, "state") in ("hidden", "veryHidden"),
                    part=link.target if link is not None and worksheet else None,
                )
            )
    shared = parts.get("sharedStrings")
    for number, tab in enumerate(tabs, start=1):
        sheet = _sheet(package, tab.part, dates, date1904) if tab.part else _Sheet()
        strings = _strings(package, shared, sheet) if shared else {}
        heading = f"# Sheet {number}" + (" (hidden)" if tab.hidden else "")
        output.gap()
        output.add(f"{heading}: {tab.name}" if tab.name else heading)
        note = _cut(sheet)
        if note:
            output.note(note)
        for row in sheet.rows:
            values = (strings.get(value, "") if isinstance(value, int) else value for value in row)
            output.add("\t".join(values))


def _sheet(package: Package, part: str, dates: frozenset[int], date1904: bool) -> _Sheet:
    """The values of the sheet's first rows that hold any, and of their first columns."""
    sheet = _Sheet()
    cells: dict[int, Value] = {}
    column = 0
    for event, element in package.parsed(part):
        name = local(element.tag)
        if event == "start":
            if name == "row":
                cells, column = {}, 0
            continue
        if name == "c":
            # A cell without its reference follows the one before
            column = _column(attribute(element, "r")) or column + 1
            value = _value(element, dates, date1904)
            if value != "" and column > MOST_COLUMNS:
                sheet.more_columns = True
            elif value != "":
                cells[column] = value
            element.clear()
        elif name == "row":
            if cells and len(sheet.rows) == MOST_ROWS:
                sheet.more_rows = True
                break
            if cells:
                sheet.rows.append([cells.get(index, "") for index in range(1, max(cells) + 1)])
            element.clear()
    return sheet


def _column(reference: str | None) -> int | None:
    """The number of a cell's column, from 1 for A, as its reference, such as AB12, says."""
    found = _COLUMN.match(reference or "")
    if found is None:
        return None
    number = 0
    for letter in found.group().upper():
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def _value(cell: Element, dates: frozenset[int], date1904: bool) -> Value:
    """The cell's value, as Excel last computed it, or the index of its shared string."""
    kind = attribute(cell, "t") or "n"
    if kind == "inlineStr":
        inline = child(cell, "is")
        return _text(run_text(inline, _UNSEEN)) if inline is not None else ""
    found = child(cell, "v")
    raw = found.text if found is not None else None
    if not raw:
        return ""
    match kind:
        case "s":
            return int(raw) if raw.strip().isdigit() else ""
        case "b":
            return "TRUE" if raw.strip() in ("1", "true") else "FALSE"
        case "str" | "e" | "d":
            return _text(raw)
    style = attribute(cell, "s")
    if style is not None and style.isdigit() and int(style) in dates:
        date = _date(raw, date1904)
        if date is not None:
            return date
    return _number(raw)


def _text(text: str) -> str:
    """A text of the workbook, on one line, the characters Excel escaped as they are."""
    return cell_text(_ESCAPED.sub(lambda escaped: chr(int(escaped.group(1), 16)), text))


def _number(raw: str) -> str:
    """A number as Excel shows it in its General format, up to 15 digits."""
    try:
        number = float(raw)
    except ValueError:
        return _text(raw)
    return format(number, ".15g") if math.isfinite(number) else _text(raw)


def _date(raw: str, date1904: bool) -> str | None:
    """The day and time a number of days stands for, as Excel counts them: from 1904, or from 1900,
    which Excel takes for a leap year. A time alone for less than a day."""
    try:
        days = float(raw)
    except ValueError:
        return None
    if not 0 <= days <= _LAST_DAY:
        return None
    if date1904:
        start = datetime(1904, 1, 1)
    else:
        # Day 60 is 1900-02-29, which never was: the days before it count from a day later
        start = datetime(1899, 12, 31) if days < 60 else datetime(1899, 12, 30)
    try:
        moment = start + timedelta(seconds=round(days * 86_400))
    except OverflowError:
        return None
    time = "%H:%M" if moment.second == 0 else "%H:%M:%S"
    if days < 1:
        return moment.strftime(time)
    if moment.time() == datetime.min.time():
        return moment.strftime("%Y-%m-%d")
    return moment.strftime(f"%Y-%m-%d {time}")


def _date_styles(package: Package, part: str) -> frozenset[int]:
    """The styles of the workbook's cells that show their number as a date or a time, by index."""
    formats: dict[int, str] = {}
    styles: list[int] = []
    # The formats of the cells' own styles, apart from those of the named styles
    within = False
    for event, element in package.parsed(part):
        name = local(element.tag)
        if name == "cellXfs":
            within = event == "start"
        elif event == "end" and name == "numFmt":
            number = attribute(element, "numFmtId") or ""
            if number.isdigit():
                formats[int(number)] = attribute(element, "formatCode") or ""
        elif event == "end" and name == "xf" and within:
            number = attribute(element, "numFmtId") or ""
            styles.append(int(number) if number.isdigit() else 0)
    return frozenset(index for index, number in enumerate(styles) if _shows_dates(number, formats))


def _shows_dates(number: int, formats: dict[int, str]) -> bool:
    code = formats.get(number)
    if code is None:
        return number in _DATE_FORMATS
    shown = _LITERAL.sub("", code).split(";")[0]
    if _ELAPSED.search(shown):
        return True
    return _DATE_PART.search(_BRACKETED.sub("", shown)) is not None


def _strings(package: Package, part: str, sheet: _Sheet) -> dict[int, str]:
    """The shared strings that the sheet's cells show, by index: read up to the last of them, which
    Excel numbers in the order the workbook first shows them."""
    shown = {value for row in sheet.rows for value in row if isinstance(value, int)}
    strings: dict[int, str] = {}
    if not shown:
        return strings
    last = max(shown)
    index = 0
    for event, element in package.parsed(part):
        if event != "end" or local(element.tag) != "si":
            continue
        if index in shown:
            strings[index] = _text(run_text(element, _UNSEEN))
        element.clear()
        index += 1
        if index > last:
            break
    return strings


def _cut(sheet: _Sheet) -> str | None:
    """What a note says the sheet leaves out, if anything."""
    rows = f"its first {MOST_ROWS} rows that hold values"
    columns = f"its first {MOST_COLUMNS} columns"
    if sheet.more_rows and sheet.more_columns:
        return f"Only {rows}, and {columns}, are given."
    if sheet.more_rows:
        return f"Only {rows} are given."
    if sheet.more_columns:
        return f"Only {columns} are given."
    return None
