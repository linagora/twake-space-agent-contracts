"""contacts.contact.delete.v1: the user deletes a contact of one of their own address books in
Twake Contacts, for good."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.contacts import EXAMPLE_IDS, UNTRUSTED, BookId, ContactId
from twake_space_agent_contracts.contacts.carddav import Contacts, contact_not_found, read_only
from twake_space_agent_contracts.contacts.cards import Contact, contact_of, fields_of
from twake_space_agent_contracts.contacts.summaries import deleting
from twake_space_agent_contracts.previews import Previewing, digest_of


def router(contacts: Contacts, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/contacts", tags=["contacts.contact.delete.v1"])

    @routes.delete(
        "/address-books/{book_id}/contacts/{contact_id}",
        operation_id="delete_contact",
        summary="Delete a contact of the user in Twake Contacts, for good",
        description=(
            "Deletes, for good, a contact in one of the own address books of the user you act "
            "for, those list_address_books gives as writable: Contacts keeps no trash, and the "
            "contact comes back only if added again. Call it only once the user asked to delete "
            "this very contact; they confirm each call. It answers the contact as it was. A "
            "contact of an address book shared with the user, or of their domain's, is refused "
            "with address_book_read_only. In a book the user shared, those they shared it with "
            f"see it go; Contacts tells nobody. {UNTRUSTED} Example: {EXAMPLE_IDS}."
        ),
        response_model=Contact,
        # A contact the user loses for good, with no trash to take it back from: the owner
        # confirms each one, shown what it would delete
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def delete_contact(
        book_id: BookId,
        contact_id: ContactId,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> Contact | JSONResponse:
        book = await contacts.book(user, book_id)
        if not book.writable:
            raise read_only(book)
        card = await contacts.contact(user, book, contact_id)
        if card is None:
            raise contact_not_found(book_id, contact_id)
        # What the owner allows: the contact as it is. The proxy of the side service forwards
        # no If-Match: the card just read is the one checked, and deleted at once
        digest = digest_of(book.book_id, contact_id, card.jcard)
        if preview.asked:
            text, _ = fields_of(card.jcard)
            return preview.answer(deleting(book, text, preview.language), digest)
        preview.check(digest)
        if not await contacts.delete(user, card):
            raise contact_not_found(book_id, contact_id)
        return contact_of(card)

    return routes
