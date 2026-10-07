"""What every reader of documents shares, whatever the document's kind."""

import time
import tracemalloc
from collections.abc import Callable

import pytest
from openpyxl import Workbook

from tests.documents import pdf, rezipped, word, workbook
from twake_space_agent_contracts.documents import Kind, Reader
from twake_space_agent_contracts.documents import word as word_reader
from twake_space_agent_contracts.documents.reading import Output

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MIB = 1_048_576


def test_reading_keeps_only_what_is_open_and_what_is_being_read() -> None:
    # Two hundred thousand empty paragraphs, different enough that the zip compresses them only
    # some four times: the parser lets go of each once read, so that the memory reading takes
    # does not grow with them. Counted as Python counts what it allocates, the same everywhere,
    # where what the kernel counts of a process includes what its parent held when it started it
    paragraphs = "".join(
        f'<w:p w:rsidR="{number * 2_654_435_761 % 2**32:08X}"/>' for number in range(200_000)
    )
    document = (
        f'<w:document xmlns:w="{WORD_NAMESPACE}"><w:body>{paragraphs}'
        "<w:p><w:r><w:t>End</w:t></w:r></w:p></w:body></w:document>"
    )
    content = rezipped(word(lambda _: None), {"word/document.xml": document.encode()})
    output = Output(65_536, time.monotonic() + 60)

    tracemalloc.start()
    try:
        word_reader.read(content, output)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert output.text() == "End"
    # Kept whole, those paragraphs take some 37 MiB
    assert peak < 10 * MIB


# Twenty thousand characters on one line
LONG = "word " * 4_000


def _long_cell(book: Workbook) -> None:
    sheet = book.active
    assert sheet is not None
    sheet["A1"] = LONG


LONG_LINES: dict[Kind, Callable[[], bytes]] = {
    "docx": lambda: word(lambda document: document.add_paragraph(LONG)),
    "xlsx": lambda: workbook(_long_cell),
    "pdf": lambda: pdf(LONG),
}


@pytest.mark.parametrize("kind", sorted(LONG_LINES))
async def test_a_reading_gives_no_more_text_than_its_budget_however_long_a_line(
    kind: Kind,
) -> None:
    # A paragraph, a cell or a page on one line far longer than asked: the reading process cuts
    # it, rather than hand the service all of it
    text = await Reader().read(kind, LONG_LINES[kind](), 1_000)

    assert text.cut is True
    assert 900 < len(text.text) <= 1_000
