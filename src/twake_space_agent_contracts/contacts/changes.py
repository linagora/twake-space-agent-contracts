"""Contacts as the contracts write them: the fields a call gives, checked, and the card in jCard
they make, or change."""

import re
from collections.abc import Iterable
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from twake_space_agent_contracts.contacts import line, paragraphs
from twake_space_agent_contracts.contacts.cards import ContactText, fields_of
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
# The properties a change replaces, by the field they hold
HELD_IN = {
    "nickname": {"nickname"},
    "emails": {"email"},
    "phones": {"tel"},
    "addresses": {"adr"},
    "note": {"note"},
    "birthday": {"bday"},
}
# What a card starts with, which the name Contacts shows follows
HEADING = {"version", "prodid", "uid"}
# The parts of an address the contracts write, in the order vCard gives them, after its post office
# box and its extended address
PARTS = ("street", "locality", "region", "postal_code", "country")

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
    parts = {name: _text(getattr(address, name)) or "" for name in PARTS}
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
                # Its post office box and extended address, which the contracts leave empty, then
                # the parts they write
                held[name] = [
                    ["adr", _typed(address["type"]), "text", ["", "", *map(address.get, PARTS)]]
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


def _named(prop: list[Any], names: set[str]) -> bool:
    return str(prop[0]).lower() in names


def _replaced(props: list[list[Any]], names: set[str], new: list[list[Any]]) -> list[list[Any]]:
    """The properties, those of these names replaced by the new ones, where the first was: at the
    end when there was none."""
    at = next((index for index, prop in enumerate(props) if _named(prop, names)), len(props))
    return [*props[:at], *new, *(prop for prop in props[at:] if not _named(prop, names))]


def _first(props: list[list[Any]], name: str) -> list[Any] | None:
    return next((prop for prop in props if _named(prop, {name})), None)


def _organization(props: list[list[Any]], organization: str | None) -> list[list[Any]]:
    """The organization, keeping the units the card names in it."""
    if organization is None:
        return []
    held = _first(props, "org")
    units = held[3][1:] if held is not None and isinstance(held[3], list) else []
    return [
        ["org", held[1] if held else {}, "text", [organization, *units] if units else organization]
    ]


def _titles(props: list[list[Any]], title: str | None) -> list[list[Any]]:
    """The job title, in ROLE, where the Contacts web app shows it, and in TITLE when the card
    holds one, so that no other app shows the former one."""
    if title is None:
        return []
    held = [["role", {}, "text", title]]
    if _first(props, "title") is not None:
        held.append(["title", {}, "text", title])
    return held


def _names(props: list[list[Any]], changes: dict[str, Any]) -> list[list[Any]]:
    """The structured name, its given and family names changed, its other parts as they are."""
    held = _first(props, "n")
    written = held[3] if held is not None else []
    parts = list(written) if isinstance(written, list) else [written]
    parts += [""] * (5 - len(parts))
    if "family_name" in changes:
        parts[0] = changes["family_name"] or ""
    if "given_name" in changes:
        parts[1] = changes["given_name"] or ""
    if not any(parts):
        return []
    return [["n", held[1] if held else {}, "text", parts]]


def _made_of(text: ContactText) -> dict[str, Any]:
    """What the name Contacts shows is made of, in a contact."""
    return {
        "given_name": text.given_name,
        "family_name": text.family_name,
        "organization": text.organization,
        "title": text.title,
        "nickname": text.nickname,
        "emails": [{"address": email.address} for email in text.emails],
        "phones": [{"number": phone.number} for phone in text.phones],
    }


def changed(card: list[Any], changes: dict[str, Any]) -> list[Any]:
    """The card with these fields changed, and nothing else: the properties that hold a field given
    are replaced by those that hold its new value, none for none, where the first one was. The
    name Contacts shows is the one given, else follows what it is made of, unless it was set apart
    from it."""
    props = [list(prop) for prop in card[1]]
    before, _ = fields_of(card)
    for name, value in changes.items():
        if name in HELD_IN:
            props = _replaced(props, HELD_IN[name], properties({name: value}).get(name, []))
        elif name == "organization":
            props = _replaced(props, {"org"}, _organization(props, value))
        elif name == "title":
            props = _replaced(props, {"role", "title"}, _titles(props, value))
    if changes.keys() & {"given_name", "family_name"}:
        props = _replaced(props, {"n"}, _names(props, changes))
    # The name follows what it is made of, unless it was set apart from it
    follows = (
        "name" in changes or before.name is None or before.name == shown_name(_made_of(before))
    )
    shown = changes.get("name") or (
        shown_name(_made_of(fields_of(["vcard", props])[0])) if follows else None
    )
    if follows and shown is None:
        raise invalid_request(
            "A contact keeps a name, an organization, an email or a phone to show it by."
        )
    held = _first(props, "fn")
    if shown is not None and (held is None or held[3] != shown):
        fn: list[list[Any]] = [["fn", {}, "text", shown]]
        if held is not None:
            props = _replaced(props, {"fn"}, fn)
        else:
            # Where a card names its contact, after what it starts with
            at = next(
                (index for index, prop in enumerate(props) if not _named(prop, HEADING)),
                len(props),
            )
            props = [*props[:at], *fn, *props[at:]]
    return [card[0], props]
