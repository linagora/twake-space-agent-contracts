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
# What a value and a list take in a change, which shows each field as it would be and as it was
CHANGED_VALUE = 300
CHANGED_LIST = 600
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
    delete: str
    change: str
    unchanged: str
    contact: str
    unnamed: str
    default_book: str
    collected_book: str
    named_book: str
    untitled_book: str
    field: str
    instead: str
    instead_of_none: str
    removing: str
    none: str
    note: str
    note_instead: str
    note_instead_of_none: str
    note_cleared: str
    told: str
    others: tuple[str, str]
    labels: dict[str, str]
    types: dict[str, str]


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        add="Ajouter {contact} à ton carnet d'adresses",
        added="{contact} est déjà dans ton carnet d'adresses : rien n'est ajouté.",
        delete="Supprimer {contact} de {book}, définitivement : Twake Contacts n'a pas de"
        " corbeille.",
        change="Modifier {contact} dans {book} :",
        unchanged="Rien ne change dans {contact}, dans {book}.",
        contact="le contact {name}",
        unnamed="le contact sans nom",
        default_book="ton carnet d'adresses",
        collected_book="tes contacts collectés",
        named_book="ton carnet d'adresses {name}",
        untitled_book="ton carnet d'adresses sans nom",
        field="{label} : {value}",
        instead="{label} : {value}, au lieu de {before}",
        instead_of_none="{label} : {value}, au lieu de rien",
        removing="{label} : {value}, ce qui retire {removed}",
        none="rien",
        note="Note :",
        note_instead="Au lieu de :",
        note_instead_of_none="Note, au lieu de rien :",
        note_cleared="Note : rien, ce qui retire :",
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
        delete="Delete {contact} from {book}, for good: Twake Contacts keeps no trash.",
        change="Change {contact} in {book}:",
        unchanged="Nothing changes in {contact}, in {book}.",
        contact="the contact {name}",
        unnamed="the contact without a name",
        default_book="your address book",
        collected_book="your collected contacts",
        named_book="your address book {name}",
        untitled_book="your untitled address book",
        field="{label}: {value}",
        instead="{label}: {value}, instead of {before}",
        instead_of_none="{label}: {value}, instead of none",
        removing="{label}: {value}, removing {removed}",
        none="none",
        note="Note:",
        note_instead="Instead of:",
        note_instead_of_none="Note, instead of none:",
        note_cleared="Note: none, removing:",
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


def _entries(text: ContactText, field: str, room: int, language: Language) -> list[str]:
    """The entries of a list of a contact, its emails, phones or addresses, each within `room`."""
    if field == "emails":
        return [_email(email, room, language) for email in text.emails]
    if field == "phones":
        return [_phone(phone, room, language) for phone in text.phones]
    return [_address(address, room, language) for address in text.addresses]


def value(text: ContactText, field: str, room: int, language: Language) -> str | None:
    """A field of a contact as a summary shows it, within `room`; None when it holds nothing."""
    if field in LISTS:
        entries = _entries(text, field, min(ITEM, room), language)
        return _listed(entries, room, language) if entries else None
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


def _held(text: ContactText, language: Language) -> list[str]:
    """Each field a contact holds, on a line of its own, but its name and its note. The given and
    family names come apart from the name only when it is not made of them."""
    words = _WORDS[language]
    names = " ".join(part for part in (text.given_name, text.family_name) if part)
    lines = []
    for field in FIELDS:
        if field in ("given_name", "family_name") and text.name == names:
            continue
        shown = value(text, field, LIST if field in LISTS else VALUE, language)
        if shown is not None:
            lines.append(words.field.format(label=words.labels[field], value=shown))
    return lines


def adding(text: ContactText, language: Language) -> str:
    """What adding a contact does, as the owner reads it: each field it holds, its note whole when
    it fits."""
    head = _WORDS[language].add.format(contact=contact_named(text, language))
    return _with_note([head, *_held(text, language)], text.note, language)


def deleting(book: Book, text: ContactText, language: Language) -> str:
    """What deleting a contact does, as the owner reads it: that it goes for good, and each field
    it holds, its note whole when it fits."""
    head = _WORDS[language].delete.format(
        contact=contact_named(text, language), book=book_named(book, language)
    )
    return _with_note([head, *_held(text, language)], text.note, language)


def added(text: ContactText, language: Language) -> str:
    """What a call made again does, as the owner reads it: nothing, the contact being in their
    address book already."""
    contact = _capitalized(contact_named(text, language))
    return _WORDS[language].added.format(contact=contact)


def _key(entry: Email | Phone | Address) -> tuple[str | None, ...]:
    """What tells an entry of a list apart from the others, whatever its type: an email by its
    address, a phone by its digits, an address by its parts."""
    if isinstance(entry, Email):
        return (entry.address.casefold(),)
    if isinstance(entry, Phone):
        return ("".join(character for character in entry.number if character.isdigit()),)
    parts = (entry.street, entry.locality, entry.region, entry.postal_code, entry.country)
    return tuple(part.casefold() if part else None for part in parts)


def _removed(text: ContactText, before: ContactText, field: str) -> ContactText:
    """The contact as it was, its list of that field holding only the entries the change
    removes."""
    kept = {_key(entry) for entry in getattr(text, field)}
    lost = [entry for entry in getattr(before, field) if _key(entry) not in kept]
    return before.model_copy(update={field: lost})


def _change(text: ContactText, before: ContactText, field: str, language: Language) -> str:
    """A field a change changes, as it would be and as it was: what it removes named as such."""
    words = _WORDS[language]
    room = CHANGED_LIST if field in LISTS else CHANGED_VALUE
    label = words.labels[field]
    now, then = value(text, field, room, language), value(before, field, room, language)
    if then is None:
        return words.instead_of_none.format(label=label, value=now)
    removed = (
        value(_removed(text, before, field), field, room, language) if field in LISTS else then
    )
    if removed is not None and (field in LISTS or now is None):
        return words.removing.format(label=label, value=now or words.none, removed=removed)
    return words.instead.format(label=label, value=now or words.none, before=then)


def changing(book: Book, before: ContactText, after: ContactText, language: Language) -> str:
    """What changing a contact does, as the owner reads it: each field it changes, as it would be
    and as it was, the name Contacts shows too, and the note, whole when it fits."""
    words = _WORDS[language]
    contact, where = contact_named(before, language), book_named(book, language)
    lines = [
        _change(after, before, field, language)
        for field in ("name", *FIELDS)
        if getattr(after, field) != getattr(before, field)
    ]
    if not lines and after.note == before.note:
        return words.unchanged.format(contact=contact, book=where)
    lines.insert(0, words.change.format(contact=contact, book=where))
    if after.note == before.note:
        return "\n".join([*lines, words.told])
    tail = "\n" + words.told
    if after.note is None:
        head = "\n".join([*lines, words.note_cleared]) + "\n"
        return head + excerpt(before.note or "", BUDGET - shown_size(head + tail), language) + tail
    if before.note is None:
        head = "\n".join([*lines, words.note_instead_of_none]) + "\n"
        return head + excerpt(after.note, BUDGET - shown_size(head + tail), language) + tail
    head = "\n".join([*lines, words.note]) + "\n"
    middle = "\n" + words.note_instead + "\n"
    room = (BUDGET - shown_size(head + middle + tail)) // 2
    return (
        head
        + excerpt(after.note, room, language)
        + middle
        + excerpt(before.note, room, language)
        + tail
    )
