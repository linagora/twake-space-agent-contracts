"""contacts.contacts.read.v1: the user's contacts in Twake Contacts: those that hold some words, in
all the address books they read, and one contact."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.contacts import (
    EXAMPLE_IDS,
    UNTRUSTED,
    BookId,
    ContactId,
)
from twake_space_agent_contracts.contacts.carddav import (
    SEARCHED,
    Card,
    Contacts,
    contact_not_found,
)
from twake_space_agent_contracts.contacts.cards import (
    Contact,
    ContactSummary,
    contact_of,
    fields_of,
    holds,
    search_pattern,
    shown_as,
    summary_of,
)
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.text import seen


class ContactList(BaseModel):
    contacts: list[ContactSummary]
    truncated: bool = Field(
        description="Whether more contacts may hold the words than the list gives: give more "
        "words, or a higher limit."
    )


def router(contacts: Contacts, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/contacts", tags=["contacts.contacts.read.v1"])

    @routes.get(
        "/search",
        operation_id="search_contacts",
        summary="Search the user's contacts in Twake Contacts",
        description=(
            "Finds the contacts whose text holds q, whatever its case, as people wrote it: their "
            "name, nickname, emails, phones, organization, job title, addresses, note or "
            "birthday. q is found as written, never read as a pattern. It searches every "
            "address book list_address_books gives, the user's own, those shared with them and "
            "their domain's, and gives each contact with its book_id, its contact_id and the "
            "kind of its book, its name, emails, phones and organization, by name: read_contact "
            "gives the rest. truncated tells that more contacts may hold q than the list gives. "
            f"{UNTRUSTED} Example, for the contacts named Dupont: q=dupont, limit=20."
        ),
    )
    async def search_contacts(
        user: Annotated[User, Depends(caller)],
        q: Annotated[
            str,
            Query(
                min_length=2,
                max_length=100,
                description="The words to find, 2 to 100 characters, such as a name, part of an "
                "email or of a phone number.",
            ),
        ],
        limit: Annotated[
            int, Query(ge=1, le=100, description="How many contacts to give, 20 by default.")
        ] = 20,
    ) -> ContactList:
        words = " ".join(seen(q).split())
        if len(words) < 2:
            raise invalid_request("q: Give 2 to 100 characters to find.")
        owner = await contacts.owner(user)
        books = await contacts.books(user, owner)
        cards, complete = await contacts.search(user, books, search_pattern(words), SEARCHED)
        ranks = {book.book_id: rank for rank, book in enumerate(books)}
        found: dict[tuple[str, int, str], Card] = {}
        for card in cards:
            text, _ = fields_of(card.jcard)
            if holds(text, words):
                key = (shown_as(text), ranks[card.book.book_id], card.contact_id)
                found.setdefault(key, card)
        kept = [found[key] for key in sorted(found)]
        return ContactList(
            contacts=[summary_of(card) for card in kept[:limit]],
            truncated=len(kept) > limit or not complete,
        )

    @routes.get(
        "/address-books/{book_id}/contacts/{contact_id}",
        operation_id="read_contact",
        summary="Read a contact of the user in Twake Contacts",
        description=(
            "Reads a contact in an address book the user you act for reads, by the book_id and "
            "the contact_id that search_contacts gives: its names, emails, phones, organization, "
            "job title, addresses, note and birthday. writable tells whether update_contact and "
            "delete_contact act on it. A contact outside the user's address books answers like "
            f"an unknown one. {UNTRUSTED} Example: {EXAMPLE_IDS}."
        ),
    )
    async def read_contact(
        book_id: BookId, contact_id: ContactId, user: Annotated[User, Depends(caller)]
    ) -> Contact:
        book = await contacts.book(user, book_id)
        card = await contacts.contact(user, book, contact_id)
        if card is None:
            raise contact_not_found(book_id, contact_id)
        return contact_of(card)

    return routes
