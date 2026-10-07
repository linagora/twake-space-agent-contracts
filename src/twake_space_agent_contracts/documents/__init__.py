"""The text of the user's documents, such as Word documents, which the service reads in a process of
its own, one per document: whatever a document crafted against its parser makes it do, such as
take all the memory, crash or never end, happens in that process rather than in the service, which
stops it once it takes too long. The process reads the document from its standard input, and
writes its text, or why it has none, as JSON
(python -m twake_space_agent_contracts.documents <kind> <budget> <seconds>)."""

import asyncio
import json
import sys
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass
from typing import Literal, get_args

Kind = Literal["docx", "pptx"]
"""What the service reads a document as."""

KINDS: dict[str, Kind] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
}
"""The kind of each type of document the service reads, by the type its uploader declared."""

Reason = Literal["encrypted", "too_large", "unreadable", "too_long"]
"""Why a document's text cannot be read: it is protected by a password; it holds more than the
service reads, once uncompressed; it is not the document its type says, or damaged; or its reading
gave no text in the time it has."""

_REASONS: dict[str, Reason] = {reason: reason for reason in get_args(Reason)}

AT_ONCE = 2
"""The documents read at the same time, each in its own process, the others waiting their turn:
what they take in memory adds up."""
READING_SECONDS = 10.0
"""The time a process reads a document for: then it stops, and gives the text it read."""
LONGEST_SECONDS = 15.0
"""The time a process has to answer, past which the service stops it: the time it reads for, and
the time to start and to stop, if what it was reading lets it."""


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
            stdin=PIPE,
            stdout=PIPE,
            stderr=DEVNULL,
            env={},
        )
        try:
            async with asyncio.timeout(LONGEST_SECONDS):
                output, _ = await process.communicate(content)
        except TimeoutError:
            raise Refused("too_long") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        # A process that crashed, or that the kernel stopped for the memory it took
        if process.returncode != 0:
            raise Refused("unreadable")
        return _text(output)


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
