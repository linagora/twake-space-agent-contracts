"""What the readers of every kind of document share, in the reading process: the text they write,
line by line, up to its budget and its deadline, and the reasons they refuse a document for."""

import time
from typing import ClassVar


class Refusal(Exception):
    """The document's text cannot be read, for the reason the service is told."""

    reason: ClassVar[str]


class Encrypted(Refusal):
    """The document is protected by a password."""

    reason = "encrypted"


class TooLarge(Refusal):
    """The document holds more than the service reads, once uncompressed."""

    reason = "too_large"


class Unreadable(Refusal):
    """The document is not what its type says, or it is damaged."""

    reason = "unreadable"


class NoText(Refusal):
    """The document holds no text, as a PDF of images does."""

    reason = "no_text"


class Full(Exception):
    """The text holds more than its budget: the rest of the document is not read."""


class OutOfTime(Exception):
    """The reading took all its time: the rest of the document is not read."""


class Output:
    """The text a reader writes, line by line, up to a budget of characters and until a deadline:
    once the text goes beyond its budget, or the deadline passes, the reader stops, and the text is
    cut."""

    def __init__(self, budget: int, deadline: float) -> None:
        self._lines: list[str] = []
        self._size = 0
        self._budget = budget
        self._deadline = deadline
        """When the reading stops, as time.monotonic counts it."""
        self.cut = False
        """Whether the text leaves out some of the document."""

    def add(self, text: str) -> None:
        """Adds the text, on lines of its own."""
        for line in text.split("\n"):
            self._lines.append(line)
            self._size += len(line) + 1
            if self._size > self._budget:
                raise Full
        self.tick()

    def gap(self) -> None:
        """A blank line between blocks, such as before a heading: never first, nor twice."""
        if self._lines and self._lines[-1]:
            self.add("")

    def note(self, words: str) -> None:
        """Says, on a line of its own between brackets, what the text leaves out of the
        document."""
        self.cut = True
        self.add(f"[{words}]")

    def tick(self) -> None:
        """Stops the reading once its deadline passed."""
        if time.monotonic() >= self._deadline:
            raise OutOfTime

    def stop(self, words: str) -> None:
        """Ends the text with a note between brackets, which says why the rest of the document is
        not read: whatever the budget and the time, which have run out."""
        self.cut = True
        self._lines.append(f"[{words}]")

    @property
    def empty(self) -> bool:
        return not any(self._lines)

    def text(self) -> str:
        return "\n".join(self._lines).strip("\n")
