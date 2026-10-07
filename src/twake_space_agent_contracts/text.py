"""Text that someone wrote, as the contracts give it back: without what a reader does not see."""

import unicodedata

UNSEEN = frozenset({"Cc", "Cf", "Cs"})
"""What a reader does not see, by Unicode category: the control characters, the format characters,
which are invisible and can reorder text, such as bidirectional marks, zero-width spaces and tags,
and the surrogates left alone, which no text holds. The harness refuses the first two in what it
shows an owner, but for line feeds and tabs."""


def seen(text: str) -> str:
    """The text without what a reader does not see, but for whitespace, which is left to collapse
    or to keep."""
    return "".join(
        character
        for character in text
        if character.isspace() or unicodedata.category(character) not in UNSEEN
    )
