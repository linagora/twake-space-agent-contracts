"""What every reader of documents shares, whatever the document's kind."""

import asyncio
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable
from typing import Any

import pytest
from openpyxl import Workbook

from tests.documents import empty_paragraphs, pdf, word, workbook
from twake_space_agent_contracts import documents
from twake_space_agent_contracts.documents import Busy, Kind, Reader, Refused
from twake_space_agent_contracts.documents import word as word_reader
from twake_space_agent_contracts.documents.reading import Output

MIB = 1_048_576


def test_reading_keeps_only_what_is_open_and_what_is_being_read() -> None:
    # The parser lets go of each paragraph once read, so that the memory reading takes does not
    # grow with them. Counted as Python counts what it allocates, the same everywhere, where what
    # the kernel counts of a process includes what its parent held when it started it
    content = empty_paragraphs(200_000)
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


async def test_a_reading_that_takes_more_time_on_a_processor_than_given_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A second of a processor, where a page of 200,000 operations that show blanks takes some four
    # to read, and all the time and the memory it wants otherwise: the kernel stops it. Its reading
    # takes up to some 175 MiB, beyond the 160 MiB the service gives, which a fast processor
    # reaches within that second
    monkeypatch.setattr(documents, "PROCESSOR_SECONDS", 1)
    monkeypatch.setattr(documents, "READING_SECONDS", 60.0)
    monkeypatch.setattr(documents, "LONGEST_SECONDS", 60.0)
    monkeypatch.setattr(documents, "MOST_MEMORY", 1_024 * MIB)

    with pytest.raises(Refused) as refused:
        await Reader().read("pdf", pdf("\n".join([" "] * 200_000)), 1_000)

    assert refused.value.reason == "too_long"


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux bounds a process's address space")
async def test_a_reading_that_takes_more_memory_than_given_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Less address space than the process takes once started, so that the first memory it takes
    # anew fails: for the document itself, 5 MB, which no memory it freed holds
    monkeypatch.setattr(documents, "MOST_MEMORY", 1_048_576)

    with pytest.raises(Refused) as refused:
        await Reader().read("pdf", pdf("\n".join([" "] * 500_000)), 1_000)

    assert refused.value.reason == "memory"


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux bounds a process's address space")
async def test_a_page_that_takes_more_memory_to_read_than_given_is_refused_for_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 160 MiB of address space, as the service gives: room for the process and the document, 5 MB,
    # but not for the reading of its page of 500,000 operations, which takes some 250 MiB more, and
    # all the time it wants: a page that runs out of memory is not a damaged one
    monkeypatch.setattr(documents, "MOST_MEMORY", 160 * MIB)
    monkeypatch.setattr(documents, "PROCESSOR_SECONDS", 60)
    monkeypatch.setattr(documents, "READING_SECONDS", 60.0)
    monkeypatch.setattr(documents, "LONGEST_SECONDS", 60.0)

    with pytest.raises(Refused) as refused:
        await Reader().read("pdf", pdf("\n".join([" "] * 500_000)), 1_000)

    assert refused.value.reason == "memory"


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux bounds a process's address space")
async def test_a_reading_that_runs_out_of_memory_gives_the_text_it_read_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The page that runs out of the 160 MiB comes after one the reading wrote: as a reading that
    # runs out of time, it gives that text, cut, and a last line says why the rest is missing
    monkeypatch.setattr(documents, "MOST_MEMORY", 160 * MIB)
    monkeypatch.setattr(documents, "PROCESSOR_SECONDS", 60)
    monkeypatch.setattr(documents, "READING_SECONDS", 60.0)
    monkeypatch.setattr(documents, "LONGEST_SECONDS", 60.0)

    text = await Reader().read("pdf", pdf("Plans", "\n".join([" "] * 500_000)), 1_000)

    assert text.cut is True
    assert text.text == (
        "# Page 1\nPlans\n[The rest of the document was not read: reading it took too much memory.]"
    )


# The service as uvicorn starts it, then whether it may be inspected, which prctl tells
SERVICE_STARTED = """
import ctypes, os
os.environ |= {
    "OIDC_ISSUER": "https://sign-up.test/",
    "CALENDAR_URL": "https://calendar.test",
}
from twake_space_agent_contracts.app import create_app_from_env
create_app_from_env()
print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))
"""


@pytest.mark.skipif(sys.platform != "linux", reason="only Linux has prctl")
def test_the_processes_the_service_starts_cannot_inspect_it() -> None:
    # Its reading processes run as the same user: once the service may not be dumped, none may
    # trace it, nor read its memory or its environment, where its settings are, through /proc
    started = subprocess.run(
        [sys.executable, "-c", SERVICE_STARTED], capture_output=True, text=True, check=True
    )

    assert started.stdout.strip() == "0"


async def test_two_documents_at_most_are_read_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two owners hold their turns: a third waits, then is told the service is busy
    monkeypatch.setattr(documents, "WAITING_SECONDS", 0.2)
    reader = Reader()
    held = asyncio.Event()
    inside: list[str] = []

    async def reading(owner: str) -> None:
        async with reader.turn(owner):
            inside.append(owner)
            await held.wait()

    readings = [asyncio.create_task(reading(f"user{number}@twake.test")) for number in (1, 2)]
    await asyncio.sleep(0.05)

    with pytest.raises(Busy):
        await reading("user3@twake.test")

    held.set()
    await asyncio.gather(*readings)
    assert inside == ["user1@twake.test", "user2@twake.test"]


async def test_an_owner_has_one_document_read_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # So that one owner's requests never hold every turn
    monkeypatch.setattr(documents, "WAITING_SECONDS", 0.2)
    reader = Reader()
    held = asyncio.Event()

    async def reading() -> None:
        async with reader.turn("mmaudet@twake.test"):
            await held.wait()

    first = asyncio.create_task(reading())
    await asyncio.sleep(0.05)

    with pytest.raises(Busy):
        await reading()

    held.set()
    await first


async def test_a_reading_process_starts_with_nothing_of_the_service_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Where the service's settings are, such as the key of the gateway's route to Synapse: the
    # process starts without any environment, isolated, which also leaves out any setting of
    # Python's own
    monkeypatch.setenv("CHAT_GATEWAY_KEY", "key-of-the-contracts-consumer")
    started: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    start = asyncio.create_subprocess_exec

    async def recorded(*command: Any, **options: Any) -> asyncio.subprocess.Process:
        started.append((command, options))
        return await start(*command, **options)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorded)

    text = await Reader().read("docx", word(lambda document: document.add_paragraph("Plans")), 99)

    assert text.text == "Plans"
    [(command, options)] = started
    assert options["env"] == {}
    assert command[:2] == (sys.executable, "-I")
