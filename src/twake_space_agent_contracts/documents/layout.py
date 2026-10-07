"""How the readers lay a document out as text, whatever its format, so that a document reads the
same written by one application as by another: a text's headings as Markdown's, its list items
after a dash and its tables as rows of cells parted by tabs; a presentation's slides and a
spreadsheet's sheets, each under a heading that numbers it, and a line between brackets for what it
leaves out."""

from datetime import datetime

from twake_space_agent_contracts.documents import MOST_COLUMNS, MOST_ROWS
from twake_space_agent_contracts.documents.reading import Output

DEEPEST_LIST = 8
"""How far a list item is indented at most, nested lists after."""


def paragraph(
    output: Output, text: str, *, level: int | None = None, depth: int | None = None
) -> None:
    """A paragraph of a text: a heading of that level, from 1, an item of a list nested that deep,
    from 0, or else the paragraph as it is. A paragraph without text is left out."""
    text = text.strip()
    if not text:
        return
    if level is not None:
        output.gap()
        output.add("#" * min(level, 6) + " " + " ".join(text.split()))
    elif depth is not None:
        output.add("  " * min(depth, DEEPEST_LIST) + "- " + text)
    else:
        output.add(text)


def table(output: Output, cells: list[list[str]]) -> None:
    """A table of a text, as rows, between blank lines."""
    lines = rows(cells)
    if lines:
        output.gap()
        output.add("\n".join(lines))
        output.gap()


def rows(cells: list[list[str]]) -> list[str]:
    """The rows of a table that hold any text, each a line of its cells parted by tabs, without the
    empty cells that end it."""
    lines = []
    for row in cells:
        texts = [cell_text(cell) for cell in row]
        while texts and not texts[-1]:
            texts.pop()
        if texts:
            lines.append("\t".join(texts))
    return lines


def cell_text(text: str) -> str:
    """Text that sits in a cell of a row: on one line, without the tabs that part cells."""
    return " ".join(text.split())


def slide(
    output: Output, number: int, *, hidden: bool, title: str, lines: list[str], notes: str
) -> None:
    """A slide of a presentation, under a heading that numbers it and gives its title, then the
    lines of its other text, and its speaker notes under a heading of their own."""
    heading = f"# Slide {number}" + (" (hidden)" if hidden else "")
    output.gap()
    output.add(f"{heading}: {title}" if title else heading)
    for line in lines:
        output.add(line)
    if notes:
        output.add("## Notes")
        output.add(notes)


def sheet(
    output: Output,
    number: int,
    *,
    name: str,
    hidden: bool,
    values: list[list[str]],
    more_rows: bool,
    more_columns: bool,
) -> None:
    """A sheet of a spreadsheet, under a heading that numbers it and gives its name, then a line
    between brackets when it holds more rows or columns than are read, then the values of its
    rows, parted by tabs."""
    heading = f"# Sheet {number}" + (" (hidden)" if hidden else "")
    output.gap()
    output.add(f"{heading}: {name}" if name else heading)
    first_rows = f"its first {MOST_ROWS} rows that hold values"
    first_columns = f"its first {MOST_COLUMNS} columns"
    if more_rows and more_columns:
        output.note(f"Only {first_rows}, and {first_columns}, are given.")
    elif more_rows:
        output.note(f"Only {first_rows} are given.")
    elif more_columns:
        output.note(f"Only {first_columns} are given.")
    for row in values:
        output.add("\t".join(row))


def number(value: float) -> str:
    """A number as a spreadsheet shows it in its general format, up to 15 digits."""
    return format(value, ".15g")


def moment(when: datetime, *, time_only: bool = False) -> str:
    """A day and a time as a spreadsheet's value: the day alone at midnight, the time alone when
    the value is one, and seconds only when there are some."""
    time = "%H:%M" if when.second == 0 else "%H:%M:%S"
    if time_only:
        return when.strftime(time)
    if when.time() == datetime.min.time():
        return when.strftime("%Y-%m-%d")
    return when.strftime(f"%Y-%m-%d {time}")
