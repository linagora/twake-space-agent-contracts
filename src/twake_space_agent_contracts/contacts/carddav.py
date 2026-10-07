"""Twake Contacts, through the Calendar side service as the user, with their own token: the address
books of esn-sabre, in its JSON dialect of CardDAV, behind the side service's /dav proxy, and the
side service's search across several of them.

The proxy forwards neither If-Match nor If-None-Match: no write of a card can be conditional."""

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import httpx

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem

Kind = Literal["personal", "collected", "shared", "domain"]
"""What an address book is to the user: one of their own; the one Contacts collects addresses in,
theirs too; someone else's, which they share with the user or the user subscribed to; or one of
the user's domain."""

DEFAULT_BOOK = "contacts"
"""The book esn-sabre gives each user, where their contacts go."""
COLLECTED_BOOK = "collected"
"""The book esn-sabre gives each user, where Contacts collects the addresses they write to."""
SEPARATOR = "~"
"""What separates, in the id of an address book, the home it lies in from its name."""
HOME = r"[A-Za-z0-9]{1,64}"
"""The id of a home of address books in esn-sabre: its user's, or its domain's."""
NAME = r"[A-Za-z0-9._~-]{1,200}"
"""The names of the address books the contracts take: those the search of Contacts takes, plain
segments of a path."""
BOOK_ID = rf"^{HOME}{SEPARATOR}{NAME}$"
CONTACT_ID = r"^[A-Za-z0-9][A-Za-z0-9._~@+=-]{0,199}$"
"""The name of a contact's card in its address book, without its extension: what the Contacts web
app and CardDAV clients name cards with, letters, digits and a few signs."""
CARD = ".vcf"
"""The extension of a card's name."""
REWRITTEN = ".json"
"""What esn-sabre removes from wherever a URL holds it: a name that holds it cannot be reached."""
JCARD = "application/vcard+json"
"""What esn-sabre gives a card in, converted to vCard 4.0, and takes one in."""
SEARCHED = 200
"""The most contacts a search asks Contacts for, all address books together: of those, the
contracts keep the ones whose text holds the words, as people wrote them, rather than the names
vCard writes them under."""
LARGEST_CARD = 1024 * 1024
"""The most a card may take as the contracts send it, in bytes: what nginx takes in a request in
front of esn-sabre."""

# What the user's own home lists: their books, the delegations they accepted (invite status 2),
# and their subscriptions, with how many contacts each shows
OWN_BOOKS = {
    "personal": "true",
    "shared": "true",
    "subscribed": "true",
    "inviteStatus": "2",
    "contactsCount": "true",
}
# What a domain's home lists for its members: its books, but those it disabled
DOMAIN_BOOKS = {"personal": "true", "contactsCount": "true"}
# The order the books are listed in, by what they are to the user, the default one first
RANKS = {"personal": 1, "collected": 2, "shared": 3, "domain": 4}


def _contacts_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


def unavailable(detail: str) -> Problem:
    return _contacts_problem("contacts_unavailable", "Contacts unavailable", detail)


def _refused(detail: str) -> Problem:
    return _contacts_problem("contacts_refused", "Contacts refused the user's token", detail)


def book_not_found(book_id: str) -> Problem:
    return Problem(
        status=404,
        code="address_book_not_found",
        title="Address book not found",
        detail=f"The user reads no address book {book_id}: list_address_books gives those they"
        " read.",
    )


def contact_not_found(book_id: str, contact_id: str) -> Problem:
    return Problem(
        status=404,
        code="contact_not_found",
        title="Contact not found",
        detail=f"Address book {book_id} has no contact {contact_id}.",
    )


def too_large() -> Problem:
    return Problem(
        status=413,
        code="contact_too_large",
        title="Contact too large",
        detail=f"The contact would take more than the {LARGEST_CARD // 1024} KiB Contacts takes"
        " in a card, such as with a large photo: nothing was written.",
    )


def _user_not_found() -> Problem:
    return Problem(
        status=404,
        code="contacts_user_not_found",
        title="Contacts user not found",
        detail="Contacts has no user with the email of the user you act for.",
    )


@dataclass(frozen=True)
class Owner:
    """The user as Contacts knows them: their id, which names their home of address books, and
    those of their domains, whose homes hold their domains' books."""

    user_id: str
    domain_ids: tuple[str, ...]


@dataclass(frozen=True)
class Book:
    """An address book the user reads: in their home, theirs or someone else's shared with them,
    or in their domain's home. What its owner wrote of it comes as they wrote it."""

    home: str
    name: str
    kind: Kind
    display_name: str | None
    description: str | None
    contact_count: int | None
    write_allowed: bool
    """Whether Contacts lets the user write in it, as it lists the book's rights."""

    @property
    def book_id(self) -> str:
        return f"{self.home}{SEPARATOR}{self.name}"

    @property
    def default(self) -> bool:
        """Whether it is the user's default book, where their new contacts go."""
        return self.kind == "personal" and self.name == DEFAULT_BOOK

    @property
    def writable(self) -> bool:
        """Whether the contracts write in it: in the user's own books only, which Contacts lets
        them write in, never in someone else's shared with them, nor in their domain's, whatever
        rights Contacts gives them there."""
        return self.kind in ("personal", "collected") and self.write_allowed


def contact_exists(book: Book, contact_id: str, why: str) -> Problem:
    return Problem(
        status=409,
        code="contact_exists",
        title="Contact exists",
        detail=f"{why}: nothing was added. Read it with read_contact, and change it with"
        " update_contact rather than adding another.",
        extensions={"book_id": book.book_id, "contact_id": contact_id},
    )


def _text(value: Any) -> str | None:
    """Words of an address book, None for none."""
    return value if isinstance(value, str) and value else None


def _owned(item: dict[str, Any]) -> bool:
    """Whether a book of the user's home is their own: one esn-sabre gives them as its owner,
    neither a delegation nor a subscription, which show someone else's book."""
    return (
        not item.get("openpaas:source")
        and not item.get("openpaas:subscription-type")
        and str(item.get("dav:share-access")) == "1"
    )


def _href(item: dict[str, Any]) -> str:
    """Where esn-sabre says an item is, in its _links."""
    links = item.get("_links")
    own = links.get("self") if isinstance(links, dict) else None
    href = own.get("href") if isinstance(own, dict) else None
    return href if isinstance(href, str) else ""


def _book(item: Any, home: str, domain: bool) -> Book | None:
    """A book as esn-sabre lists it in that home; None for one of another home, or whose name the
    contracts do not take."""
    if not isinstance(item, dict):
        raise unavailable("Contacts gave an address book in an unexpected form.")
    found = re.fullmatch(rf"(?:.*/)?addressbooks/({HOME})/(.+)\.json", _href(item))
    if found is None or found[1] != home:
        return None
    name = found[2]
    if not re.fullmatch(NAME, name) or REWRITTEN in name:
        return None
    kind: Kind
    if domain:
        kind = "domain"
    elif not _owned(item):
        kind = "shared"
    else:
        kind = "collected" if name == COLLECTED_BOOK else "personal"
    count = item.get("numberOfContacts")
    rights = item.get("dav:acl")
    return Book(
        home=home,
        name=name,
        kind=kind,
        display_name=_text(item.get("dav:name")),
        description=_text(item.get("carddav:description")),
        contact_count=count if isinstance(count, int) and not isinstance(count, bool) else None,
        write_allowed=isinstance(rights, list) and "dav:write" in rights,
    )


def _rank(book: Book) -> int:
    return 0 if book.default else RANKS[book.kind]


@dataclass(frozen=True)
class Card:
    """A contact in one of the books the user reads, by its id there, in jCard: the vCard of RFC
    6350 in JSON (RFC 7095), each property its name, its parameters, its type and its values."""

    book: Book
    contact_id: str
    jcard: list[Any]


def is_jcard(value: Any) -> bool:
    """Whether a value is a card in jCard, each property a name, parameters, a type and values."""
    return (
        isinstance(value, list)
        and len(value) == 2
        and value[0] == "vcard"
        and isinstance(value[1], list)
        and all(
            isinstance(prop, list)
            and len(prop) >= 4
            and isinstance(prop[0], str)
            and isinstance(prop[1], dict)
            and isinstance(prop[2], str)
            for prop in value[1]
        )
    )


def contact_id_of(card_name: str) -> str | None:
    """The id of a contact by the name of its card; None for a card the contracts cannot name."""
    contact_id = card_name.removesuffix(CARD)
    if contact_id == card_name or REWRITTEN in contact_id:
        return None
    return contact_id if re.fullmatch(CONTACT_ID, contact_id) else None


def sent(jcard: list[Any]) -> bytes:
    """A card as the contracts send it, refused when it is larger than Contacts takes."""
    body = json.dumps(jcard, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > LARGEST_CARD:
        raise too_large()
    return body


def _card_path(book: Book, contact_id: str) -> str:
    return f"/dav/addressbooks/{book.home}/{book.name}/{quote(contact_id + CARD, safe='')}"


class Contacts:
    """Twake Contacts, through the Calendar side service, called as the user with their own
    token."""

    def __init__(self, url: str, http: httpx.AsyncClient) -> None:
        self._url = url
        self._http = http

    async def _request(
        self,
        user: User,
        method: str,
        path: str,
        *,
        accept: str = "application/json",
        passing: frozenset[int] = frozenset(),
        **request: Any,
    ) -> httpx.Response:
        """The side service's answer: a success, or one of the statuses `passing` names. esn-sabre
        answers JSON when Accept names it alone."""
        headers = {"Authorization": f"Bearer {user.token}", "Accept": accept}
        headers |= request.pop("headers", {})
        try:
            response = await self._http.request(
                method, self._url + path, headers=headers, **request
            )
        except httpx.HTTPError as error:
            raise unavailable(f"Contacts did not answer {method} {path}.") from error
        if response.is_success or response.status_code in passing:
            return response
        if response.status_code in (401, 403):
            raise _refused(f"Contacts answered {response.status_code} to {method} {path}.")
        raise unavailable(f"Contacts answered {response.status_code} to {method} {path}.")

    async def _json(
        self,
        user: User,
        method: str,
        path: str,
        *,
        passing: frozenset[int] = frozenset(),
        **request: Any,
    ) -> Any:
        """The side service's JSON answer; None for one of the statuses `passing` names."""
        response = await self._request(user, method, path, passing=passing, **request)
        if not response.is_success:
            return None
        try:
            return response.json()
        except ValueError as error:
            raise unavailable(f"Contacts did not answer {method} {path} in JSON.") from error

    async def owner(self, user: User) -> Owner:
        """The user as Contacts knows them, by their email."""
        found = await self._json(user, "GET", "/api/users", params={"email": user.email})
        if isinstance(found, list) and not found:
            raise _user_not_found()
        try:
            user_id = found[0]["_id"]
            domain_ids = tuple(domain["domain_id"] for domain in found[0].get("domains") or [])
        except (IndexError, KeyError, TypeError, AttributeError) as error:
            raise unavailable("Contacts gave the user in an unexpected form.") from error
        homes = [user_id, *domain_ids]
        if not all(isinstance(home, str) and re.fullmatch(HOME, home) for home in homes):
            raise unavailable("Contacts gave the user in an unexpected form.")
        return Owner(user_id=user_id, domain_ids=domain_ids)

    async def books(self, user: User, owner: Owner) -> list[Book]:
        """The address books the user reads: their own, the default one first, then those shared
        with them, then their domains'. A domain whose home they cannot read gives none."""
        found = await self._json(
            user, "GET", f"/dav/addressbooks/{owner.user_id}.json", params=OWN_BOOKS
        )
        listed = [(item, owner.user_id, False) for item in _items(found)]
        for domain_id in owner.domain_ids:
            found = await self._json(
                user,
                "GET",
                f"/dav/addressbooks/{domain_id}.json",
                params=DOMAIN_BOOKS,
                passing=frozenset({403, 404}),
            )
            if found is not None:
                listed += [(item, domain_id, True) for item in _items(found)]
        books = [book for item, home, domain in listed if (book := _book(item, home, domain))]
        return sorted(books, key=_rank)

    async def book(self, user: User, book_id: str) -> Book:
        """The address book of that id, if the user reads it: one that list_address_books gives.
        Any other, someone else's they were not given or one that does not exist, is not
        found."""
        owner = await self.owner(user)
        for book in await self.books(user, owner):
            if book.book_id == book_id:
                return book
        raise book_not_found(book_id)

    async def search(
        self, user: User, books: list[Book], pattern: str, limit: int
    ) -> tuple[list[Card], bool]:
        """The contacts of these books whose vCard text matches the regular expression, as the
        side service's search finds them: the first `limit`, in each book by the names of their
        cards, then the books in order, in jCard as the book keeps them; one outside these books,
        or that the contracts cannot name, is left out. Whether Contacts found fewer than
        `limit`, all there are, comes with them."""
        if not books:
            return [], True
        found = await self._json(
            user,
            "POST",
            "/contacts/api/contacts/search",
            params={"limit": str(limit), "offset": "0"},
            json={
                "query": pattern,
                "addressBooks": [
                    {"userId": book.home, "addressBookId": book.name} for book in books
                ],
            },
        )
        try:
            items = found["_embedded"]["dav:item"]
            hits = [(_href(item), item["data"]) for item in items]
        except (KeyError, TypeError, AttributeError) as error:
            raise unavailable("Contacts gave the contacts found in an unexpected form.") from error
        located = {(book.home, book.name): book for book in books}
        cards = []
        for href, data in hits:
            path = re.fullmatch(rf"(?:.*/)?addressbooks/({HOME})/([^/]+)/([^/]+)", href)
            book = located.get((path[1], path[2])) if path else None
            contact_id = contact_id_of(path[3]) if path else None
            if book is not None and contact_id is not None and is_jcard(data):
                cards.append(Card(book, contact_id, data))
        return cards, len(hits) < limit

    async def card(self, user: User, book: Book, contact_id: str) -> Card | None:
        """The contact of that id in the book, in jCard of vCard 4.0; None if the book holds
        none, or if the user cannot read it there."""
        # esn-sabre would read another card than the one named
        if REWRITTEN in contact_id:
            return None
        path = _card_path(book, contact_id)
        response = await self._request(
            user, "GET", path, accept=JCARD, passing=frozenset({403, 404})
        )
        if not response.is_success:
            return None
        try:
            jcard = response.json()
        except ValueError as error:
            raise unavailable(f"Contacts did not answer GET {path} in jCard.") from error
        if not is_jcard(jcard):
            raise unavailable("Contacts gave the contact in an unexpected form.")
        return Card(book, contact_id, jcard)

    async def put(self, user: User, card: Card) -> None:
        """Writes the card at its place, a new one or in place of the one there. The proxy
        forwards no If-Match: the write cannot be conditional, so it follows the read at once."""
        response = await self._request(
            user,
            "PUT",
            _card_path(card.book, card.contact_id),
            content=sent(card.jcard),
            headers={"Content-Type": JCARD},
            passing=frozenset({413}),
        )
        if response.status_code == 413:
            raise too_large()

    async def add(self, user: User, card: Card) -> Card:
        """Writes a new card, then reads it back as Contacts keeps it. The side service waits for
        esn-sabre longer than the service waits for it: a write it did not confirm may have been
        kept all the same, which the read tells."""
        unconfirmed: Problem | None = None
        try:
            await self.put(user, card)
        except Problem as problem:
            # A token Contacts refuses, or a card it does not take, wrote nothing
            if problem.code != "contacts_unavailable":
                raise
            unconfirmed = problem
        added = await self.card(user, card.book, card.contact_id)
        if added is None:
            raise unconfirmed or unavailable("Contacts did not keep the contact it was given.")
        return added


def _items(found: Any) -> list[Any]:
    """The address books esn-sabre lists in a home."""
    try:
        items = found["_embedded"]["dav:addressbook"]
    except (KeyError, TypeError) as error:
        raise unavailable("Contacts gave the address books in an unexpected form.") from error
    if not isinstance(items, list):
        raise unavailable("Contacts gave the address books in an unexpected form.")
    return items
