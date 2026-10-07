"""Contacts as the contracts write them: the fields a call gives, checked, and the card in jCard
they make."""

import re
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from twake_space_agent_contracts.contacts import line, paragraphs
from twake_space_agent_contracts.problems import Problem, invalid_request

LONGEST_TEXT = 200
"""The most characters of a name, an organization, a job title or a part of an address."""
LONGEST_EMAIL = 254
"""The longest email address, as SMTP takes one."""
LONGEST_PHONE = 50
LONGEST_NOTE = 10_000
MOST_EMAILS = 10
MOST_PHONES = 10
MOST_ADDRESSES = 5
FEWEST_DIGITS, MOST_DIGITS = 3, 20
"""How many digits a phone number has: from a short number to an international one with its
extension."""
PRODID = "-//Linagora//Twake Space agent contracts//EN"
"""Who wrote the cards the contracts make, as vCard asks every app to say."""

# An email address: its local part, dot-separated atoms of letters, digits and the signs RFC 5322
# allows, then a domain of two labels at least, the last one letters, or the punycode of a top
# level domain. Letters and digits of any script: an address may be internationalized.
_ATOM = r"[\w!#$%&'*+/=?^`{|}~-]+"
_LABEL = r"[^\W_](?:[\w-]{0,61}[^\W_])?"
EMAIL = re.compile(rf"{_ATOM}(?:\.{_ATOM})*@(?:{_LABEL}\.)+(?:[^\W\d_]{{2,63}}|xn--[a-z0-9-]+)")
# A phone number as people write one: digits, maybe after a plus, with spaces, dots, dashes,
# slashes and parentheses among them
PHONE = re.compile(r"\+?[0-9() ./-]+")


def _only_a_day(written: object) -> object:
    """A day as written, such as 1980-05-17, and nothing else, such as a time or a number."""
    if not isinstance(written, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", written):
        raise ValueError("a day is written as 1980-05-17")
    return written


Text = Annotated[
    str, Field(max_length=LONGEST_TEXT, description="Plain text, 200 characters at most.")
]
Part = Annotated[str, Field(max_length=LONGEST_TEXT)]


class NewEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: Annotated[
        str,
        Field(
            min_length=1,
            max_length=LONGEST_EMAIL,
            description="An email address, such as jeanne.martin@example.com.",
        ),
    ]
    type: Literal["work", "home", "other"] | None = None


class NewPhone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: Annotated[
        str,
        Field(
            min_length=1,
            max_length=LONGEST_PHONE,
            description="Digits, maybe after a +, with spaces, dots, dashes, slashes or "
            "parentheses, such as +33 6 12 34 56 78.",
        ),
    ]
    type: Literal["cell", "work", "home", "fax", "other"] | None = None


class NewAddress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["home", "work", "other"] | None = None
    street: Part | None = None
    locality: Annotated[Part | None, Field(description="The town or the city.")] = None
    region: Part | None = None
    postal_code: Part | None = None
    country: Part | None = None


class ContactFields(BaseModel):
    """The fields of a contact the contracts write."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        Text | None,
        Field(
            description="The name Contacts shows, such as Dr Jeanne Martin. By default the given "
            "and family names, else the organization, the job title, the nickname, the first "
            "email or the first phone."
        ),
    ] = None
    given_name: Text | None = None
    family_name: Text | None = None
    nickname: Text | None = None
    emails: Annotated[list[NewEmail], Field(max_length=MOST_EMAILS)] | None = None
    phones: Annotated[list[NewPhone], Field(max_length=MOST_PHONES)] | None = None
    organization: Text | None = None
    title: Annotated[Text | None, Field(description="The job title.")] = None
    addresses: Annotated[list[NewAddress], Field(max_length=MOST_ADDRESSES)] | None = None
    note: Annotated[
        str | None,
        Field(
            max_length=LONGEST_NOTE,
            description=f"Plain text, on several lines, {LONGEST_NOTE} characters at most.",
        ),
    ] = None
    birthday: Annotated[
        date | None,
        BeforeValidator(lambda day: day if day is None else _only_a_day(day)),
        Field(description="The day, such as 1980-05-17."),
    ] = None


def _invalid(code: str, title: str, detail: str) -> Problem:
    return Problem(status=400, code=code, title=title, detail=detail)


def _email(address: str) -> str:
    """The address, if it is one."""
    if not EMAIL.fullmatch(address):
        raise _invalid(
            "invalid_email",
            "Invalid email",
            f"emails: {address!r} is not an email address, such as jeanne.martin@example.com.",
        )
    return address


def _phone(number: str) -> str:
    """The phone number, if it is one."""
    digits = sum(character.isdigit() for character in number)
    if not PHONE.fullmatch(number) or not FEWEST_DIGITS <= digits <= MOST_DIGITS:
        raise _invalid(
            "invalid_phone",
            "Invalid phone",
            f"phones: {number!r} is not a phone number, written with {FEWEST_DIGITS} to "
            f"{MOST_DIGITS} digits, such as +33 6 12 34 56 78.",
        )
    return number


def _text(value: str | None) -> str | None:
    return line(value, LONGEST_TEXT)[0]


def _address(address: NewAddress) -> dict[str, Any]:
    parts = {
        name: _text(getattr(address, name))
        for name in ("street", "locality", "region", "postal_code", "country")
    }
    if not any(parts.values()):
        raise invalid_request(
            "addresses: An address has a street, a locality, a region, a postal code or a country."
        )
    return {"type": address.type} | parts


def written(fields: ContactFields, names: Iterable[str]) -> dict[str, Any]:
    """The fields of these names as a card holds them: text on one line, a note on its lines,
    without what a reader does not see, and none when nothing is left; the emails and the phones
    checked, each once."""
    values: dict[str, Any] = {}
    for name in names:
        value = getattr(fields, name)
        if name == "emails":
            emails = [(_email(email.address.strip()), email.type) for email in value or []]
            value = [
                {"address": address, "type": kind}
                for index, (address, kind) in enumerate(emails)
                if address.casefold() not in {other.casefold() for other, _ in emails[:index]}
            ]
        elif name == "phones":
            phones = [(_phone(phone.number.strip()), phone.type) for phone in value or []]
            value = [
                {"number": number, "type": kind}
                for index, (number, kind) in enumerate(phones)
                if number not in {other for other, _ in phones[:index]}
            ]
        elif name == "addresses":
            value = [_address(address) for address in value or []]
        elif name == "note":
            value = paragraphs(value, LONGEST_NOTE)[0]
        elif name == "birthday":
            value = value.isoformat() if value is not None else None
        else:
            value = _text(value)
        values[name] = value
    return values


def shown_name(fields: dict[str, Any]) -> str | None:
    """The name Contacts shows of a contact with these fields, as its web app makes one: the name
    given, else the given and family names, the organization, the job title, the nickname, the
    first email or the first phone."""
    names = " ".join(part for part in (fields.get("given_name"), fields.get("family_name")) if part)
    emails, phones = fields.get("emails") or [], fields.get("phones") or []
    candidates = (
        fields.get("name"),
        names,
        fields.get("organization"),
        fields.get("title"),
        fields.get("nickname"),
        emails[0]["address"] if emails else None,
        phones[0]["number"] if phones else None,
    )
    return next((candidate for candidate in candidates if candidate), None)


def _typed(kind: str | None) -> dict[str, str]:
    return {"type": kind} if kind else {}


def properties(fields: dict[str, Any]) -> dict[str, list[list[Any]]]:
    """The properties of a card that hold these fields, in jCard, by the field they hold: a field
    that holds nothing has none. The job title goes to ROLE, where the Contacts web app writes and
    shows it."""
    held: dict[str, list[list[Any]]] = {}
    for name, value in fields.items():
        if name in ("given_name", "family_name") or not value:
            continue
        match name:
            case "name":
                held[name] = [["fn", {}, "text", value]]
            case "nickname" | "note":
                held[name] = [[name, {}, "text", value]]
            case "organization":
                held[name] = [["org", {}, "text", value]]
            case "title":
                held[name] = [["role", {}, "text", value]]
            case "emails":
                held[name] = [
                    ["email", _typed(email["type"]), "text", email["address"]] for email in value
                ]
            case "phones":
                held[name] = [
                    ["tel", _typed(phone["type"]), "text", phone["number"]] for phone in value
                ]
            case "addresses":
                held[name] = [
                    [
                        "adr",
                        _typed(address["type"]),
                        "text",
                        [
                            "",
                            "",
                            *(
                                address[part] or ""
                                for part in ("street", "locality", "region", "postal_code")
                            ),
                            address["country"] or "",
                        ],
                    ]
                    for address in value
                ]
            case "birthday":
                held[name] = [["bday", {}, "date", value]]
    family, given = fields.get("family_name"), fields.get("given_name")
    if family or given:
        held["names"] = [["n", {}, "text", [family or "", given or "", "", "", ""]]]
    return held


def new_card(uid: str, fields: dict[str, Any]) -> list[Any]:
    """A card in jCard of vCard 4.0 that holds these fields, under that UID, with the name Contacts
    shows."""
    held = properties(fields | {"name": shown_name(fields)})
    order = ("name", "names", "nickname", "emails", "phones", "organization", "title")
    rest = ("addresses", "birthday", "note")
    return [
        "vcard",
        [
            ["version", {}, "text", "4.0"],
            ["prodid", {}, "text", PRODID],
            ["uid", {}, "text", uid],
            *(prop for name in (*order, *rest) for prop in held.get(name, [])),
        ],
    ]
