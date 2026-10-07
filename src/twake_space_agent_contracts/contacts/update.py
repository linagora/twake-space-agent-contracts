"""contacts.contact.update.v1: the user changes a contact in one of their own address books in
Twake Contacts."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.contacts import EXAMPLE_IDS, UNTRUSTED, BookId, ContactId
from twake_space_agent_contracts.contacts.carddav import (
    Card,
    Contacts,
    contact_not_found,
    read_only,
    sent,
    unavailable,
)
from twake_space_agent_contracts.contacts.cards import Contact, contact_of, fields_of
from twake_space_agent_contracts.contacts.changes import ContactFields, changed, written
from twake_space_agent_contracts.contacts.summaries import changing
from twake_space_agent_contracts.previews import Previewing, digest_of
from twake_space_agent_contracts.problems import invalid_request


class ContactChanges(ContactFields):
    """The fields of a contact to change, and only those: null clears one."""


def router(contacts: Contacts, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/contacts", tags=["contacts.contact.update.v1"])

    @routes.patch(
        "/address-books/{book_id}/contacts/{contact_id}",
        operation_id="update_contact",
        summary="Change a contact of the user in Twake Contacts",
        description=(
            "Changes, as the user you act for, a contact in one of their own address books, "
            "those list_address_books gives as writable: only the fields given, null clearing "
            "one. A list given, emails, phones or addresses, replaces the whole list: give the "
            "entries to keep with the new ones. The name Contacts shows follows the given and "
            "family names, unless it was set apart from them; name sets it apart. What else the "
            "contact holds, such as a photo, stays as it is. A contact of an address book shared "
            "with the user, or of their domain's, is refused with address_book_read_only. In a "
            "book the user shared, those they shared it with see the change; Contacts tells "
            f"nobody. {UNTRUSTED} Example, to give a contact a new mobile number: {EXAMPLE_IDS}, "
            'body={"phones": [{"number": "+33 6 98 76 54 32", "type": "cell"}]}.'
        ),
        response_model=Contact,
        # The user's own contact, which nobody is told of: the owner's consent to write in
        # Contacts covers it, and they are not asked to confirm each one. It tells what it would
        # do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def update_contact(
        book_id: BookId,
        contact_id: ContactId,
        changes: ContactChanges,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> Contact | JSONResponse:
        if not changes.model_fields_set:
            raise invalid_request("Give at least one field to change.")
        fields = written(changes, sorted(changes.model_fields_set))
        book = await contacts.book(user, book_id)
        if not book.writable:
            raise read_only(book)
        card = await contacts.card(user, book, contact_id)
        if card is None:
            raise contact_not_found(book_id, contact_id)
        after = Card(book, contact_id, changed(card.jcard, fields))
        # Refused before the owner is asked: Contacts would not take it
        sent(after.jcard)
        # What the owner allows: the contact as it is. The proxy of the side service forwards
        # no If-Match: the card just read is the one checked, and written over at once
        digest = digest_of(book.book_id, contact_id, card.jcard)
        if preview.asked:
            before, now = fields_of(card.jcard)[0], fields_of(after.jcard)[0]
            return preview.answer(changing(book, before, now, preview.language), digest)
        preview.check(digest)
        if after.jcard == card.jcard:
            return contact_of(card)
        await contacts.put(user, after)
        written_now = await contacts.card(user, book, contact_id)
        if written_now is None:
            raise unavailable("Contacts did not keep the contact it was given.")
        return contact_of(written_now)

    return routes
