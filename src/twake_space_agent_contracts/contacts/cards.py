"""Contacts as the contracts read them: the fields of a card in jCard, as people wrote them."""

import re
from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

from twake_space_agent_contracts.contacts import line, paragraphs
from twake_space_agent_contracts.contacts.carddav import Card, Kind

LONGEST_LINE = 200
"""The most characters a read gives of a name, an organization, a job title or a part of an
address."""
LONGEST_EMAIL = 320
LONGEST_PHONE = 100
LONGEST_NOTE = 10_000
MOST_ENTRIES = 20
"""The most emails, phones or addresses a read gives of a contact."""
# The types of an email, a phone or an address the contracts give, by how cards write them
TYPES = {
    "work": "work",
    "home": "home",
    "cell": "cell",
    "mobile": "cell",
    "fax": "fax",
    "other": "other",
}


class Email(BaseModel):
    address: str
    type: str | None = Field(description="work, home or other, when the contact says it.")


class Phone(BaseModel):
    number: str
    type: str | None = Field(
        description="cell, work, home, fax or other, when the contact says it."
    )


class Address(BaseModel):
    type: str | None = Field(description="home, work or other, when the contact says it.")
    street: str | None
    locality: str | None
    region: str | None
    postal_code: str | None
    country: str | None


class ContactText(BaseModel):
    """What people wrote in a contact, the user or others."""

    name: str | None = Field(description="The name Contacts shows.")
    given_name: str | None
    family_name: str | None
    nickname: str | None
    emails: list[Email]
    phones: list[Phone]
    organization: str | None
    title: str | None = Field(description="The job title.")
    addresses: list[Address]
    note: str | None
    birthday: str | None = Field(description="As written, such as 1980-05-17.")


class Contact(BaseModel):
    """A contact of an address book the user reads."""

    book_id: str
    contact_id: str
    kind: Kind = Field(
        description="What its address book is to the user, as list_address_books says."
    )
    writable: bool = Field(
        description="Whether update_contact and delete_contact act on it: in the user's own "
        "address books only."
    )
    truncated: bool = Field(
        description=f"Whether some of it is left out: a text cut, such as a note after "
        f"{LONGEST_NOTE} characters, or more than {MOST_ENTRIES} emails, phones or addresses."
    )
    untrusted: ContactText


class PreviousText(BaseModel):
    """The fields of a contact a change changed, as they were: those it left alone are not
    given."""

    name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    nickname: str | None = None
    emails: list[Email] | None = None
    phones: list[Phone] | None = None
    organization: str | None = None
    title: str | None = None
    addresses: list[Address] | None = None
    note: str | None = None
    birthday: str | None = None

    @model_serializer(mode="wrap")
    def _changed_only(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        written: dict[str, Any] = handler(self)
        return {name: value for name, value in written.items() if name in self.model_fields_set}


class ChangedText(ContactText):
    """What people wrote in a contact once changed, and the fields the change changed, as they
    were."""

    previous: PreviousText = Field(
        description="The fields the change changed, the name Contacts shows too, as they were, "
        "so that they can be put back: a field it left alone is not given."
    )


class ChangedContact(Contact):
    """A contact once changed."""

    untrusted: ChangedText


class ContactSummaryText(BaseModel):
    """What people wrote in a contact, of what a search gives."""

    name: str | None
    emails: list[str]
    phones: list[str]
    organization: str | None


class ContactSummary(BaseModel):
    """A contact found, but for most of what people wrote in it, which read_contact gives."""

    book_id: str
    contact_id: str
    kind: Kind
    untrusted: ContactSummaryText


def _properties(card: list[Any], name: str) -> list[list[Any]]:
    """The properties of that name of a card, whatever case it writes them in."""
    return [prop for prop in card[1] if prop[0].lower() == name]


def _flat(value: Any, between: str = " ") -> str:
    """A value as text: the parts of a list, such as the components of a structured value,
    joined."""
    if isinstance(value, list):
        return between.join(part for part in map(_flat, value) if part)
    if isinstance(value, str | int | float) and not isinstance(value, bool):
        return str(value)
    return ""


def _first(card: list[Any], *names: str) -> str | None:
    """The value of the first of these properties the card has, as text."""
    for name in names:
        for prop in _properties(card, name):
            return _flat(prop[3:], ", ")
    return None


def _component(value: Any, index: int) -> str:
    """A component of a structured value, such as the given name of a name; a value that is not
    structured is its first component."""
    parts = value if isinstance(value, list) else [value]
    return _flat(parts[index]) if index < len(parts) else ""


def _type(parameters: dict[str, Any]) -> str | None:
    """The type of an email, a phone or an address the contracts give: the first the card writes
    that they know, whatever its case."""
    written = parameters.get("type")
    kinds = written if isinstance(written, list) else [written]
    for kind in kinds:
        for token in str(kind or "").split(","):
            if token.strip().lower() in TYPES:
                return TYPES[token.strip().lower()]
    return None


def _without(prefix: str, value: str) -> str:
    """A value without the scheme of the URI it may be written as, such as mailto:."""
    return value[len(prefix) :] if value[: len(prefix)].lower() == prefix else value


class _Reading:
    """What the contracts give of what people wrote, and whether any of it is left out."""

    def __init__(self) -> None:
        self.truncated = False

    def line(self, text: str | None, longest: int = LONGEST_LINE) -> str | None:
        words, cut = line(text, longest)
        self.truncated |= cut
        return words

    def entries[Entry](self, entries: list[Entry]) -> list[Entry]:
        self.truncated |= len(entries) > MOST_ENTRIES
        return entries[:MOST_ENTRIES]


def fields_of(card: list[Any]) -> tuple[ContactText, bool]:
    """What people wrote in a card in jCard, of any vCard version, and whether some of it is left
    out: on one line but a note, without what a reader does not see, cut."""
    reading = _Reading()
    names = _properties(card, "n")
    name = names[0][3] if names else None
    emails = [
        Email(address=address, type=_type(prop[1]))
        for prop in _properties(card, "email")
        if (address := reading.line(_without("mailto:", _flat(prop[3])), LONGEST_EMAIL))
    ]
    phones = [
        Phone(number=number, type=_type(prop[1]))
        for prop in _properties(card, "tel")
        if (number := reading.line(_without("tel:", _flat(prop[3])), LONGEST_PHONE))
    ]
    addresses = [
        Address(
            type=_type(prop[1]),
            street=reading.line(_component(prop[3], 2)),
            locality=reading.line(_component(prop[3], 3)),
            region=reading.line(_component(prop[3], 4)),
            postal_code=reading.line(_component(prop[3], 5)),
            country=reading.line(_component(prop[3], 6)),
        )
        for prop in _properties(card, "adr")
    ]
    note, cut = paragraphs(_first(card, "note"), LONGEST_NOTE)
    reading.truncated |= cut
    organizations = _properties(card, "org")
    text = ContactText(
        name=reading.line(_first(card, "fn")),
        given_name=reading.line(_component(name, 1)) if names else None,
        family_name=reading.line(_component(name, 0)) if names else None,
        nickname=reading.line(_first(card, "nickname")),
        emails=reading.entries(emails),
        phones=reading.entries(phones),
        organization=reading.line(_component(organizations[0][3], 0)) if organizations else None,
        # Where the Contacts web app writes the job title, else where vCard does
        title=reading.line(_first(card, "role", "title")),
        addresses=reading.entries([address for address in addresses if _any_part(address)]),
        note=note,
        birthday=reading.line(_first(card, "bday")),
    )
    return text, reading.truncated


def _any_part(address: Address) -> bool:
    return any(
        (address.street, address.locality, address.region, address.postal_code, address.country)
    )


def contact_of(card: Card) -> Contact:
    """The contact, as read_contact gives it."""
    text, truncated = fields_of(card.jcard)
    return Contact(
        book_id=card.book.book_id,
        contact_id=card.contact_id,
        kind=card.book.kind,
        writable=card.book.writable,
        truncated=truncated,
        untrusted=text,
    )


def changed_contact(card: Card, before: ContactText) -> ChangedContact:
    """The contact as update_contact gives it once changed, with the fields it changed, as they
    were."""
    contact = contact_of(card)
    now = contact.untrusted
    previous = {
        name: getattr(before, name)
        for name in ContactText.model_fields
        if getattr(before, name) != getattr(now, name)
    }
    return ChangedContact(
        **contact.model_dump(exclude={"untrusted"}),
        untrusted=ChangedText(**now.model_dump(), previous=PreviousText(**previous)),
    )


def summary_of(card: Card) -> ContactSummary:
    """The contact, as search_contacts gives it."""
    text, _ = fields_of(card.jcard)
    return ContactSummary(
        book_id=card.book.book_id,
        contact_id=card.contact_id,
        kind=card.book.kind,
        untrusted=ContactSummaryText(
            name=text.name,
            emails=[email.address for email in text.emails],
            phones=[phone.number for phone in text.phones],
            organization=text.organization,
        ),
    )


def shown_as(text: ContactText) -> str:
    """How a contact reads, to sort contacts by: its name, else its first email or phone."""
    entries = [email.address for email in text.emails] + [phone.number for phone in text.phones]
    return (text.name or (entries[0] if entries else "")).casefold()


def holds(text: ContactText, words: str) -> bool:
    """Whether a contact holds these words, whatever their case, in what people wrote in it."""
    written = [
        text.name,
        text.given_name,
        text.family_name,
        text.nickname,
        text.organization,
        text.title,
        text.note,
        text.birthday,
        *(email.address for email in text.emails),
        *(phone.number for phone in text.phones),
        *(
            part
            for address in text.addresses
            for part in (
                address.street,
                address.locality,
                address.region,
                address.postal_code,
                address.country,
            )
        ),
    ]
    found = words.casefold()
    return any(found in " ".join(value.split()).casefold() for value in written if value)


def search_pattern(words: str) -> str:
    """The regular expression that finds these words, as written, in the vCard text of a card, as
    the search of Contacts reads it: with the escapes vCard writes a comma, a semicolon and a
    backslash with, then each other sign than a letter or a digit escaped by its code, which no
    expression reads otherwise, and which leaves no .json in the URL that esn-sabre would remove.
    """
    escaped = re.sub(r"([\\,;])", r"\\\1", words)
    return "".join(
        f"\\x{ord(character):02x}" if character.isascii() and not character.isalnum() else character
        for character in escaped
    )
