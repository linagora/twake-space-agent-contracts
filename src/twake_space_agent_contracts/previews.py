"""What a write would do, told without doing it.

Before it asks its owner about a write whose operation declares x-twake-preview, the harness calls
it as the call would go, with x-twake-preview: true and the owner's language in accept-language.
The contract checks the call as it would, reads what the call acts on, and answers what it would
do, writing nothing: a 200 that carries x-twake-preview: true back, whose JSON body holds a summary,
plain text for the owner, and a digest of what the call acts on. The call its owner then allows
carries that digest in x-twake-preview-digest: a contract that finds what the call acts on changed
since answers 409 and does nothing."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time
from typing import Annotated, Any, Literal

from fastapi import Depends, Header
from fastapi.responses import JSONResponse

from twake_space_agent_contracts.problems import Problem, invalid_request

PREVIEW_HEADER = "x-twake-preview"
DIGEST_HEADER = "x-twake-preview-digest"

Language = Literal["en", "fr"]
"""The languages the harness speaks to owners, and the summaries are written in."""

LONGEST = 200
"""How many characters of a text someone else wrote a summary shows, at most."""

LIMIT = 16_384
"""The most a summary may take for the harness to show it, as it counts it (CALL_BYTES in its
src/consents/request.ts): over it, the owner is not asked, and cannot confirm the call."""
BUDGET = LIMIT * 3 // 4
"""What a summary takes at most, well within what the harness shows. The text a write would put,
shown whole when it fits, takes what the rest of its summary leaves of it."""

# The line breaks the harness reads as such when it quotes a summary line by line
_BREAKS = re.compile("\r\n|[\r\x85\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}]")

# What the harness refuses in a summary, as no text to show an owner: control characters but line
# feeds and tabs, and format characters, which can turn text right to left or show nothing. What
# someone else wrote also loses surrogates left alone, which no text holds.
_UNSHOWN = {"Cc", "Cf", "Cs"}
# The quotation marks Unicode does not class as opening or closing quotes (Pi and Pf), and what
# passes for one of those the summaries quote with
_QUOTES = frozenset(
    "\u0022\uff02"  # straight, and full width
    "\u201a\u201e"  # low
    "\u301d\u301e\u301f\u300c\u300d\u300e\u300f\ufe41\ufe42\ufe43\ufe44"  # CJK
    "\u275b\u275c\u275d\u275e\u276e\u276f"  # ornaments
    "\u2033\u2036\u3003\u02ba\u02dd\u02ee\u05f4"  # primes, ditto and double apostrophes
    "\u226a\u226b\u300a\u300b\u27ea\u27eb"  # double angle brackets
)

_WEEKDAYS: dict[Language, tuple[str, ...]] = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
}
_MONTHS: dict[Language, tuple[str, ...]] = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
}


def changed_since_preview() -> Problem:
    return Problem(
        status=409,
        code="changed_since_preview",
        title="Changed since the preview",
        detail="What this call acts on changed since its owner was shown what it would do:"
        " nothing was done. Read it again before calling again.",
    )


def digest_of(*acted_on: Any) -> str:
    """The digest of what a call acts on, given as JSON values, the same on every replica: the
    SHA-256 of their JSON, its keys sorted, in a form the harness keeps and sends back as it is.
    A value JSON does not hold is refused with a TypeError, rather than written in a form one
    replica alone may give it, as a set in the order its hashes left it."""
    encoded = json.dumps(acted_on, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def _shown(summary: str) -> str:
    """The summary without what the harness would refuse in it."""
    return "".join(
        character
        for character in summary
        if character in "\n\t" or unicodedata.category(character) not in _UNSHOWN
    ).strip()


@dataclass(frozen=True)
class Preview:
    """What the harness asks of a write: what the call would do, in the owner's language, without
    doing it; or, for the call itself, the digest of the preview its owner was shown, if any."""

    asked: bool
    language: Language
    shown: str | None
    """The digest of the preview the owner was shown, which the call they allowed carries."""

    def answer(self, summary: str, digest: str) -> JSONResponse:
        """What the call would do, told to the harness: a 200, whatever the call answers, since
        the harness takes any other success for the call itself, done. A summary that would take
        more than its budget, which no contract writes, is cut, and says so: the harness would ask
        the owner nothing about it."""
        shown = _shown(summary)
        if shown_size(shown) > BUDGET:
            shown = _cut(shown, BUDGET, self.language)
        return JSONResponse({"summary": shown, "digest": digest}, headers={PREVIEW_HEADER: "true"})

    def check(self, digest: str) -> None:
        """Refuses the call its owner allowed when what it acts on changed since they were shown
        what it would do."""
        if self.shown is not None and self.shown != digest:
            raise changed_since_preview()


def _language(accept_language: str | None) -> Language:
    """The language of the summaries: the first one the header prefers that the harness speaks,
    English by default."""
    ranges: list[tuple[float, str]] = []
    for part in (accept_language or "").split(","):
        tag, *parameters = part.split(";")
        weight = 1.0
        for parameter in parameters:
            name, _, value = parameter.strip().partition("=")
            if name.lower() == "q":
                try:
                    weight = float(value)
                except ValueError:
                    weight = 0.0
        ranges.append((weight, tag.strip().lower().partition("-")[0]))
    # A stable sort: of the ranges of the same weight, the first written wins
    for weight, language in sorted(ranges, key=lambda found: -found[0]):
        if weight > 0 and language in ("en", "fr"):
            return "fr" if language == "fr" else "en"
    return "en"


async def _preview(
    asking: Annotated[str | None, Header(alias=PREVIEW_HEADER, include_in_schema=False)] = None,
    digest: Annotated[str | None, Header(alias=DIGEST_HEADER, include_in_schema=False)] = None,
    accept_language: Annotated[str | None, Header(include_in_schema=False)] = None,
) -> Preview:
    """The preview the request asks for, from the headers the harness sets.

    Left out of the OpenAPI document: they are the harness's, never a model's.
    """
    asked = asking is not None
    # Any other value could be meant as a preview: it is refused rather than taken for the call
    if asked and (asking or "").strip().lower() != "true":
        raise invalid_request(f"{PREVIEW_HEADER}: only true asks what the call would do")
    return Preview(
        asked=asked,
        language=_language(accept_language),
        shown=digest.strip() if digest else None,
    )


Previewing = Annotated[Preview, Depends(_preview)]
"""What the route of a write that declares x-twake-preview takes: the preview the harness asks for,
or the digest of the one the owner was shown."""


def one_line(text: str | None, longest: int = LONGEST) -> str:
    """Text someone else wrote, as a summary shows it: on one line, without what a reader does not
    see, cut after `longest` characters."""
    kept = "".join(
        character
        for character in text or ""
        if character.isspace() or unicodedata.category(character) not in _UNSHOWN
    )
    line = " ".join(kept.split())
    return line if len(line) <= longest else line[: longest - 1].rstrip() + "…"


def _unquoted(text: str) -> str:
    """Text someone else wrote, its quotation marks made plain apostrophes, which close no quote:
    else it could close the quotes it comes in, and go on as the summary's own words."""
    return "".join(
        "'"
        if character in _QUOTES or unicodedata.category(character) in ("Pi", "Pf")
        else character
        for character in text
    )


def quoted(text: str, language: Language) -> str:
    """Words someone else wrote, between the quotation marks of the owner's language, which none
    of theirs can close."""
    shown = _unquoted(text)
    return f"« {shown} »" if language == "fr" else f"“{shown}”"


def person(name: str | None, address: str | None, language: Language) -> str | None:
    """Someone as their mail or their calendar names them, as a summary shows them: their name in
    quotes and their address between angle brackets, which what they wrote cannot close; None for
    no one."""
    name = one_line(name)
    address = one_line(address, 320).replace("<", "").replace(">", "")
    shown = []
    if name and name.lower() != address.lower():
        shown.append(quoted(name, language))
    if address:
        shown.append(f"<{address}>")
    return " ".join(shown) or None


def shown_size(text: str) -> int:
    """What text takes of a summary, as the harness counts it: its bytes in UTF-8, and those of
    its HTML, which escapes &, < and >."""
    plain = len(text.encode("utf-8", "surrogatepass"))
    return 2 * plain + 4 * text.count("&") + 3 * (text.count("<") + text.count(">"))


# The line that says how much of a text a summary leaves out, for one character and for more
_CUT: dict[Language, tuple[str, str]] = {
    "fr": (
        "(coupé ici : 1 caractère de plus n'est pas montré)",
        "(coupé ici : {count} caractères de plus ne sont pas montrés)",
    ),
    "en": (
        "(cut here: 1 more character is not shown)",
        "(cut here: {count} more characters are not shown)",
    ),
}


def _number(count: int, language: Language) -> str:
    """A count as the owner reads it: 1 234 or 1,234."""
    return f"{count:,}".replace(",", " ") if language == "fr" else f"{count:,}"


def _cut(text: str, budget: int, language: Language, indent: str = "") -> str:
    """The beginning of the text that takes no more than `budget` of a summary, each of its lines
    after `indent`, and a line of its own that says how many characters it leaves out."""
    one, many = _CUT[language]
    # Room for that line, however many it says
    room = budget - shown_size("\n" + many.format(count=_number(len(text), language)))
    spent, end = shown_size(indent), 0
    for character in text:
        # A line break takes the indent of the next line along
        spent += shown_size(character) + (shown_size(indent) if character == "\n" else 0)
        if spent > room:
            break
        end += 1
    kept = text[:end].rstrip()
    left = len(text) - len(kept)
    said = one if left == 1 else many.format(count=_number(left, language))
    return indent + kept.replace("\n", "\n" + indent) + "\n" + said


def excerpt(text: str, budget: int, language: Language) -> str:
    """The text a write would put, as its summary shows it: line by line, each line after a tab,
    so that none passes for the summary's own, without what a reader does not see. Whole when it
    takes no more than `budget` of the summary; else cut, with a line that says how much of it is
    left out, never silently."""
    kept = "".join(
        character
        for character in _BREAKS.sub("\n", text)
        if character in "\n\t" or unicodedata.category(character) not in _UNSHOWN
    ).rstrip()
    shown = "\t" + kept.replace("\n", "\n\t")
    return shown if shown_size(shown) <= budget else _cut(kept, budget, language, "\t")


def day(value: date, language: Language) -> str:
    """A day as the owner reads it: mardi 13 octobre 2026, Tuesday 13 October 2026."""
    number = "1er" if language == "fr" and value.day == 1 else str(value.day)
    weekday = _WEEKDAYS[language][value.weekday()]
    return f"{weekday} {number} {_MONTHS[language][value.month - 1]} {value.year}"


def time_of_day(value: time, language: Language) -> str:
    """The time of day as the owner reads it: 17 h or 17 h 30, 17:00 or 17:30."""
    if language == "fr":
        return f"{value.hour} h" + (f" {value.minute:02d}" if value.minute else "")
    return f"{value.hour:02d}:{value.minute:02d}"
