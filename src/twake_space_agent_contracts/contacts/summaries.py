"""What the writes of Twake Contacts tell the owner they would do, in their language."""

from dataclasses import dataclass
from datetime import date

from twake_space_agent_contracts.contacts.carddav import Book
from twake_space_agent_contracts.contacts.cards import Address, ContactText, Email, Phone
from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    day,
    excerpt,
    one_line,
    quoted,
    shown_size,
)

# What a value, an item of a list and a list take of a summary at most, as the harness counts it,
# so that a contact holding all it may, in characters of four bytes, leaves room for its note
VALUE = 400
ITEM = 300
LIST = 1_000
# The fields of a contact, in the order a summary tells them; the name and the note apart
FIELDS = (
    "given_name",
    "family_name",
    "nickname",
    "emails",
    "phones",
    "organization",
    "title",
    "addresses",
    "birthday",
)
LISTS = ("emails", "phones", "addresses")


@dataclass(frozen=True)
class _Words:
    """What a preview of a write in Contacts tells the owner, in one language."""

    add: str
    added: str
    contact: str
    unnamed: str
    default_book: str
    collected_book: str
    named_book: str
    untitled_book: str
    field: str
    note: str
    told: str
    others: tuple[str, str]
    labels: dict[str, str]
    types: dict[str, str]


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        add="Ajouter {contact} à ton carnet d'adresses",
        added="{contact} est déjà dans ton carnet d'adresses : rien n'est ajouté.",
        contact="le contact {name}",
        unnamed="le contact sans nom",
        default_book="ton carnet d'adresses",
        collected_book="tes contacts collectés",
        named_book="ton carnet d'adresses {name}",
        untitled_book="ton carnet d'adresses sans nom",
        field="{label} : {value}",
        note="Note :",
        told="Twake Contacts ne prévient personne.",
        others=("et 1 autre", "et {count} autres"),
        labels={
            "name": "Nom",
            "given_name": "Prénom",
            "family_name": "Nom de famille",
            "nickname": "Surnom",
            "emails": "Adresses mail",
            "phones": "Téléphones",
            "organization": "Organisation",
            "title": "Poste",
            "addresses": "Adresses",
            "birthday": "Anniversaire",
            "note": "Note",
        },
        types={
            "work": "travail",
            "home": "domicile",
            "cell": "mobile",
            "fax": "fax",
            "other": "autre",
        },
    ),
    "en": _Words(
        add="Add {contact} to your address book",
        added="{contact} is in your address book already: nothing is added.",
        contact="the contact {name}",
        unnamed="the contact without a name",
        default_book="your address book",
        collected_book="your collected contacts",
        named_book="your address book {name}",
        untitled_book="your untitled address book",
        field="{label}: {value}",
        note="Note:",
        told="Twake Contacts tells nobody.",
        others=("and 1 other", "and {count} others"),
        labels={
            "name": "Name",
            "given_name": "First name",
            "family_name": "Last name",
            "nickname": "Nickname",
            "emails": "Emails",
            "phones": "Phones",
            "organization": "Organization",
            "title": "Job title",
            "addresses": "Addresses",
            "birthday": "Birthday",
            "note": "Note",
        },
        types={"work": "work", "home": "home", "cell": "mobile", "fax": "fax", "other": "other"},
    ),
}


def fitted(text: str, room: int) -> str:
    """Text on one line that takes at most `room` of a summary, cut with an ellipsis when it would
    take more."""
    if shown_size(text) <= room:
        return text
    kept, spent = [], shown_size("…")
    for character in text:
        spent += shown_size(character)
        if spent > room:
            break
        kept.append(character)
    return "".join(kept).rstrip() + "…"


def said(text: str | None, room: int, language: Language) -> str:
    """Words someone wrote, as a summary shows them: on one line, between quotation marks, which
    none of theirs can close, all within `room`."""
    marks = shown_size(quoted("", language))
    return quoted(fitted(one_line(text, 1_000), room - marks), language)


def _kind(kind: str | None, language: Language) -> str:
    types = _WORDS[language].types
    return f" ({types[kind]})" if kind in types else ""


def _email(email: Email, room: int, language: Language) -> str:
    """An email address, between angle brackets, which it cannot hold, and its type."""
    kind = _kind(email.type, language)
    address = one_line(email.address, 1_000).replace("<", "").replace(">", "")
    return "<" + fitted(address, room - shown_size("<>" + kind)) + ">" + kind


def _phone(phone: Phone, room: int, language: Language) -> str:
    kind = _kind(phone.type, language)
    return said(phone.number, room - shown_size(kind), language) + kind


def _address(address: Address, room: int, language: Language) -> str:
    town = " ".join(part for part in (address.postal_code, address.locality) if part)
    parts = (address.street, town, address.region, address.country)
    kind = _kind(address.type, language)
    return said(", ".join(part for part in parts if part), room - shown_size(kind), language) + kind


def _listed(items: list[str], room: int, language: Language) -> str:
    """Items of a list, the first one, then as many as fit in `room`, then how many others."""
    one, many = _WORDS[language].others

    def others(count: int) -> str:
        return "" if not count else " " + (one if count == 1 else many.format(count=count))

    shown = items[:1]
    for item in items[1:]:
        if shown_size(", ".join([*shown, item]) + others(len(items) - len(shown) - 1)) > room:
            break
        shown.append(item)
    return ", ".join(shown) + others(len(items) - len(shown))


def value(text: ContactText, field: str, room: int, language: Language) -> str | None:
    """A field of a contact as a summary shows it, within `room`; None when it holds nothing."""
    item = min(ITEM, room)
    if field == "emails":
        items = [_email(email, item, language) for email in text.emails]
    elif field == "phones":
        items = [_phone(phone, item, language) for phone in text.phones]
    elif field == "addresses":
        items = [_address(address, item, language) for address in text.addresses]
    if field in LISTS:
        return _listed(items, room, language) if items else None
    written = getattr(text, field)
    if not written:
        return None
    if field == "birthday":
        try:
            return day(date.fromisoformat(written), language)
        except ValueError:
            pass
    return said(written, room, language)


def contact_named(text: ContactText, language: Language) -> str:
    """A contact, as a summary names it: by the name Contacts shows."""
    words = _WORDS[language]
    if not text.name:
        return words.unnamed
    return words.contact.format(name=said(text.name, VALUE, language))


def book_named(book: Book, language: Language) -> str:
    """One of the user's own address books, as a summary names it."""
    words = _WORDS[language]
    if book.default:
        return words.default_book
    if book.kind == "collected":
        return words.collected_book
    if not one_line(book.display_name):
        return words.untitled_book
    return words.named_book.format(name=said(book.display_name, VALUE, language))


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _with_note(lines: list[str], note: str | None, language: Language) -> str:
    """The lines of a summary, then the note, whole when it fits in what they leave, then what
    Contacts tells."""
    words = _WORDS[language]
    tail = words.told
    if not note:
        return "\n".join([*lines, tail])
    head = "\n".join([*lines, words.note]) + "\n"
    room = BUDGET - shown_size(head) - shown_size("\n" + tail)
    return head + excerpt(note, room, language) + "\n" + tail


def adding(text: ContactText, language: Language) -> str:
    """What adding a contact does, as the owner reads it: each field it holds, its note whole when
    it fits. The given and family names come apart from the name only when it is not made of
    them."""
    words = _WORDS[language]
    lines = [words.add.format(contact=contact_named(text, language))]
    names = " ".join(part for part in (text.given_name, text.family_name) if part)
    for field in FIELDS:
        if field in ("given_name", "family_name") and text.name == names:
            continue
        shown = value(text, field, LIST if field in LISTS else VALUE, language)
        if shown is not None:
            lines.append(words.field.format(label=words.labels[field], value=shown))
    return _with_note(lines, text.note, language)


def added(text: ContactText, language: Language) -> str:
    """What a call made again does, as the owner reads it: nothing, the contact being in their
    address book already."""
    contact = _capitalized(contact_named(text, language))
    return _WORDS[language].added.format(contact=contact)
