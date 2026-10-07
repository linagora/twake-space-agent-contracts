"""The text of the user's documents, such as Word documents, which the service reads in a process of
its own, one per document: whatever a document crafted against its parser makes it do, such as
take all the memory, or crash, happens in that process rather than in the service. The process
reads the document from its standard input, and writes its text, or why it has none, as JSON
(python -m twake_space_agent_contracts.documents <kind> <budget>)."""

import asyncio
import json
import sys
from asyncio.subprocess import DEVNULL, PIPE
from dataclasses import dataclass
from typing import Literal, get_args

Kind = Literal["docx"]
"""What the service reads a document as."""

KINDS: dict[str, Kind] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
"""The kind of each type of document the service reads, by the type its uploader declared."""

Reason = Literal["encrypted", "too_large", "unreadable"]
"""Why a document's text cannot be read: it is protected by a password; it holds more than the
service reads, once uncompressed; or it is not the document its type says, or damaged."""

_REASONS: dict[str, Reason] = {reason: reason for reason in get_args(Reason)}

AT_ONCE = 2
"""The documents read at the same time, each in its own process, the others waiting their turn:
what they take in memory adds up."""


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
            stdin=PIPE,
            stdout=PIPE,
            stderr=DEVNULL,
            env={},
        )
        try:
            output, _ = await process.communicate(content)
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
