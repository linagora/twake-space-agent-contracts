"""Text that someone wrote, as the contracts give it back: without what a reader does not see; and
the email addresses the contracts take."""

import re
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


def paragraphs(text: str) -> str:
    """Text other people wrote, line by line, without what a reader does not see, the spaces of
    each line and its blank runs collapsed."""
    lines = (" ".join(part.split()) for part in seen(text).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def line(text: str | None, longest: int) -> tuple[str | None, bool]:
    """Words someone wrote, as the contracts give them back: on one line, without what a reader
    does not see, cut after `longest` characters; None for none. Whether they were cut comes
    with them."""
    words = " ".join(seen(text or "").split())
    return words[:longest] or None, len(words) > longest


def paragraphs_within(text: str | None, longest: int) -> tuple[str | None, bool]:
    """Text someone wrote on several lines, as `paragraphs` gives it back, cut after `longest`
    characters; None for none. Whether it was cut comes with it."""
    kept = paragraphs(text or "")
    return kept[:longest] or None, len(kept) > longest


# A web address: http or https, a host, then anything but spaces
_WEB_LINK = re.compile(r"https?://[^\s/?#]+(?:[/?#]\S*)?", re.IGNORECASE)


def web_link(written: str | None, longest: int) -> str | None:
    """A link someone else wrote, passed on when it is a web address alone, which a reader sees
    whole, `longest` characters at most; None for anything else."""
    if written and len(written) <= longest and seen(written) == written:
        return written if _WEB_LINK.fullmatch(written) else None
    return None


# An email address: its local part, dot-separated atoms of letters, digits and the signs RFC 5322
# allows, then a domain of two labels at least, the last one letters, or the punycode of a top
# level domain. Letters and digits of any script: an address may be internationalized.
_ATOM = r"[\w!#$%&'*+/=?^`{|}~-]+"
_LABEL = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
EMAIL = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@(?:{_LABEL}\.)+(?:[^\W\d_]{{2,63}}|xn--[a-z0-9-]+)")
