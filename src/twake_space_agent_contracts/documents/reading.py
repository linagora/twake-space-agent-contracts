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
        self._notes: set[int] = set()
        """Which of the lines are notes, by their place: what the text says of the document, rather
        than any of its text."""
        self._size = 0
        self._budget = budget
        self._deadline = deadline
        """When the reading stops, as time.monotonic counts it."""
        self.cut = False
        """Whether the text leaves out some of the document."""

    def add(self, text: str) -> None:
        """Adds the text, on lines of its own, as far as the budget goes: a line that goes beyond
        it is cut there, and the reading stops, so that the text never holds more characters than
        the budget, however long a line."""
        for line in text.split("\n"):
            # What the text may still take, its size counting a line break after each line
            room = self._budget - self._size
            if len(line) > room:
                if room > 0:
                    self._lines.append(line[:room])
                    self._size += room
                raise Full
            self._lines.append(line)
            self._size += len(line) + 1
        self.tick()

    def gap(self) -> None:
        """A blank line between blocks, such as before a heading: never first, nor twice."""
        if self._lines and self._lines[-1]:
            self.add("")

    def note(self, words: str) -> None:
        """Says, on a line of its own between brackets, what the text leaves out of the
        document."""
        self.cut = True
        self._notes.add(len(self._lines))
        self.add(f"[{words}]")

    def tick(self) -> None:
        """Stops the reading once its deadline passed."""
        if time.monotonic() >= self._deadline:
            raise OutOfTime

    def stop(self, words: str) -> None:
        """Ends the text with a note between brackets, which says why the rest of the document is
        not read: whatever the budget and the time, which may have run out."""
        self.cut = True
        self._notes.add(len(self._lines))
        self._lines.append(f"[{words}]")

    @property
    def empty(self) -> bool:
        """Whether the text holds none of the document's, its notes aside."""
        return not any(line for place, line in enumerate(self._lines) if place not in self._notes)

    def text(self) -> str:
        return "\n".join(self._lines).strip("\n")
