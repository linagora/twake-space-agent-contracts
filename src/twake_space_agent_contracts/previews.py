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

# What the harness refuses in a summary, as no text to show an owner: control characters but line
# feeds and tabs, and format characters, which can turn text right to left or show nothing. What
# someone else wrote also loses surrogates left alone, which no text holds.
_UNSHOWN = {"Cc", "Cf", "Cs"}

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
    """The digest of what a call acts on, given as JSON values: the SHA-256 of their JSON, in a
    form the harness keeps and sends back as it is."""
    encoded = json.dumps(acted_on, sort_keys=True, separators=(",", ":"), default=str)
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
        the harness takes any other success for the call itself, done."""
        return JSONResponse(
            {"summary": _shown(summary), "digest": digest}, headers={PREVIEW_HEADER: "true"}
        )

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


def person(name: str | None, address: str | None) -> str | None:
    """Someone as their mail or their calendar names them, a name and an address, as a summary
    shows them; None for no one."""
    name, address = one_line(name), one_line(address, 320)
    if name and address and name.lower() != address.lower():
        return f"{name} <{address}>"
    return address or name or None


def quoted(text: str, language: Language) -> str:
    """Words someone else wrote, between the quotation marks of the owner's language."""
    return f"« {text} »" if language == "fr" else f"“{text}”"


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
