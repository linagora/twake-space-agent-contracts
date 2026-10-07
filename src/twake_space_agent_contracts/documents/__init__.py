"""The text of the user's documents, such as Word documents or PDFs, which the service reads in a
process of its own, one per document: whatever a document crafted against its parser makes it do,
such as take all the memory, crash or never end, happens in that process rather than in the
service, within the memory and the time the process is given. The process reads the document from
its standard input, and writes its text, or why it has none, as JSON
(python -m twake_space_agent_contracts.documents <kind> <budget> <seconds> <memory> <processor>)."""

import asyncio
import ctypes
import json
import signal
import sys
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass
from typing import Literal, get_args

Kind = Literal["docx", "pptx", "xlsx", "pdf", "odt", "ods", "odp"]
"""What the service reads a document as."""

KINDS: dict[str, Kind] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/pdf": "pdf",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
}
"""The kind of each type of document the service reads, by the type its uploader declared."""

MOST_ROWS = 1_000
"""The rows that hold values read from each sheet of a spreadsheet, at most."""
MOST_COLUMNS = 50
"""The columns read from each sheet of a spreadsheet, at most, from the first."""
MOST_PAGES = 200
"""The pages read from a PDF, at most, from the first."""

Reason = Literal["encrypted", "too_large", "unreadable", "no_text", "too_long", "memory"]
"""Why a document's text cannot be read: it is protected by a password; it holds more than the
service reads, once uncompressed; it is not the document its type says, or damaged; it holds no
text, as a PDF of images; its reading gave no text in the time it has; or it took more memory than
the reading is given."""

_REASONS: dict[str, Reason] = {reason: reason for reason in get_args(Reason)}
# The option of Linux's prctl that sets whether the process may be dumped
_SET_DUMPABLE = 4

AT_ONCE = 2
"""The documents read at the same time, each in its own process, the others waiting their turn:
what they take in memory adds up."""
READING_SECONDS = 10.0
"""The time a process reads a document for: then it stops, and gives the text it read."""
LONGEST_SECONDS = 15.0
"""The time a process has to answer, past which the service stops it: the time it reads for, and
the time to start and to stop, if what it was reading lets it."""
PROCESSOR_SECONDS = 20
"""The time a process may run on a processor, past which the kernel stops it, should the service
not have."""
MOST_MEMORY = 167_772_160
"""The address space a process may take, 160 MiB, which no allocation of its goes beyond: some 60
MiB once started, and the rest for the document and its reading, which takes up to some 80 MiB in
all for the longest texts. Two processes and the service stay well within the 512 MiB of a pod."""
ESCAPED_SIZE = 12
"""The bytes JSON takes at most for a character of a text it escapes, as \\ud83d\\ude00 for one
beyond the first 65,536."""
ANSWER_BEYOND_TEXT = 4_096
"""The bytes a process's answer takes at most beyond its text: its JSON, and the line that says why
its reading stopped."""


def forbid_inspection() -> None:
    """Keeps the processes that read documents, which run as the same user as the service, from
    inspecting it: once the service may not be dumped, no process but root's may trace it, nor read
    its memory or its environment, where its settings are, through /proc. Only Linux has this."""
    if sys.platform != "linux":
        return
    if ctypes.CDLL(None, use_errno=True).prctl(_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "the service could not forbid its inspection")


@dataclass(frozen=True)
class Text:
    """A document's text, as its reading process wrote it."""

    text: str
    cut: bool
    """Whether the process stopped before the end, as when the text took more than its budget."""


class Refused(Exception):
    """The document's text cannot be read, for this reason."""

    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason = reason


class Reader:
    """Reads documents, each in a process of its own, a few at a time."""

    def __init__(self, at_once: int = AT_ONCE) -> None:
        self.turn = asyncio.Semaphore(at_once)
        """Held while a document is downloaded and read."""

    async def read(self, kind: Kind, content: bytes, budget: int) -> Text:
        """The text of the document, up to budget characters, past which the process stops."""
        # Isolated: the process imports nothing from where it runs, and sees no environment, where
        # the service's own settings are
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-m",
            __name__,
            kind,
            str(budget),
            str(READING_SECONDS),
            str(MOST_MEMORY),
            str(PROCESSOR_SECONDS),
            stdin=PIPE,
            stdout=PIPE,
            stderr=DEVNULL,
            env={},
        )
        # The answer of a process that keeps to its budget: past that, it is not read any further
        most = ESCAPED_SIZE * budget + ANSWER_BEYOND_TEXT
        try:
            async with asyncio.timeout(LONGEST_SECONDS):
                async with asyncio.TaskGroup() as exchange:
                    exchange.create_task(_feed(process, content))
                    answering = exchange.create_task(_answer(process, most))
                output = answering.result()
                if len(output) > most:
                    raise Refused("unreadable")
                await process.wait()
        except TimeoutError:
            raise Refused("too_long") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        # A process the kernel stopped for the time it ran on a processor
        if process.returncode == -signal.SIGXCPU:
            raise Refused("too_long")
        # A process that crashed
        if process.returncode != 0:
            raise Refused("unreadable")
        return _text(output)


async def _feed(process: asyncio.subprocess.Process, content: bytes) -> None:
    """Gives the process the document, on its standard input."""
    assert process.stdin is not None
    try:
        process.stdin.write(content)
        await process.stdin.drain()
        process.stdin.close()
    except (BrokenPipeError, ConnectionResetError):
        # The process stopped before reading it all, as one that crashed: its exit says so
        pass


async def _answer(process: asyncio.subprocess.Process, most: int) -> bytes:
    """What the process answers on its standard output, and no more than one byte past the most
    it may answer."""
    assert process.stdout is not None
    answer = bytearray()
    while len(answer) <= most:
        chunk = await process.stdout.read(most + 1 - len(answer))
        if not chunk:
            break
        answer += chunk
    return bytes(answer)


def _text(output: bytes) -> Text:
    try:
        answer = json.loads(output)
    except ValueError:
        raise Refused("unreadable") from None
    match answer:
        case {"text": str(text), "cut": bool(cut)}:
            return Text(text, cut)
        case {"refused": str(reason)} if reason in _REASONS:
            raise Refused(_REASONS[reason])
    raise Refused("unreadable")
