"""contacts.contact.create.v1: the user adds a contact to their own default address book in Twake
Contacts."""

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.contacts import UNTRUSTED
from twake_space_agent_contracts.contacts.carddav import (
    SEARCHED,
    Book,
    Card,
    Contacts,
    contact_exists,
    sent,
    unavailable,
)
from twake_space_agent_contracts.contacts.cards import (
    Contact,
    contact_of,
    fields_of,
    search_pattern,
)
from twake_space_agent_contracts.contacts.changes import (
    ContactFields,
    new_card,
    shown_name,
    written,
)
from twake_space_agent_contracts.contacts.summaries import added, adding
from twake_space_agent_contracts.previews import Previewing, digest_of
from twake_space_agent_contracts.problems import invalid_request

# The namespace of the UIDs of the contacts the contract adds: the same contact, asked for again,
# gets the same UID
UID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:twake:contracts:contacts.contact.create.v1")


class NewContact(ContactFields):
    """A contact to add to the user's default address book."""


def _uid(email: str, fields: dict[str, object]) -> str:
    """The UID of the contact the user asks for: the same for the same fields, so that a call made
    again finds the contact it added."""
    return str(uuid.uuid5(UID_NAMESPACE, email + "\n" + json.dumps(fields, sort_keys=True)))


async def _with_email(contacts: Contacts, user: User, book: Book, emails: list[str]) -> Card | None:
    """A contact of the book with one of these email addresses, whatever their case."""
    pattern = "|".join(search_pattern(email) for email in emails)
    cards, _ = await contacts.search(user, [book], pattern, SEARCHED)
    wanted = {email.casefold() for email in emails}
    for card in cards:
        text, _ = fields_of(card.jcard)
        if any(email.address.casefold() in wanted for email in text.emails):
            return card
    return None


def router(contacts: Contacts, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/contacts", tags=["contacts.contact.create.v1"])

    @routes.post(
        "/contacts",
        operation_id="create_contact",
        status_code=201,
        summary="Add a contact to the user's address book in Twake Contacts",
        description=(
            "Adds a contact to the default address book of the user you act for, their own: give "
            "at least a name, an organization, an email or a phone. The name Contacts shows is "
            "name, by default the given and family names. Contacts tells nobody. A contact of "
            "that address book with one of the emails is not added twice: contact_exists names "
            "it, to change with update_contact. The same call made again adds no second contact: "
            "it answers 200 with the one added. Its fields come back under untrusted, as "
            f"read_contact gives them. {UNTRUSTED} Example, for a colleague met at a fair: "
            'body={"given_name": "Jeanne", "family_name": "Martin", "emails": [{"address": '
            '"jeanne.martin@example.com", "type": "work"}], "organization": "Acme"}.'
        ),
        responses={
            200: {"model": Contact, "description": "The contact the same call added before"}
        },
        response_model=Contact,
        # A new contact in the user's own address book, which nobody is told of: the owner's
        # consent to write in Contacts covers it, and they are not asked to confirm each one. It
        # tells what it would do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def create_contact(
        new: NewContact,
        user: Annotated[User, Depends(caller)],
        response: Response,
        preview: Previewing,
    ) -> Contact | JSONResponse:
        fields = written(new, ContactFields.model_fields)
        if shown_name(fields) is None:
            raise invalid_request(
                "A contact has a name, an organization, an email or a phone to show it by."
            )
        owner = await contacts.owner(user)
        books = await contacts.books(user, owner)
        book = next((book for book in books if book.default), None)
        if book is None:
            raise unavailable("Contacts gave no default address book for the user.")
        uid = _uid(user.email, fields)
        card = Card(book, uid, new_card(uid, fields))
        # Refused before the owner is asked: Contacts would not take it
        sent(card.jcard)
        text, _ = fields_of(card.jcard)
        # The proxy of the side service forwards no If-None-Match: the contact is looked for
        # first, so that a call made again neither adds it twice nor writes over what the user
        # changed since
        found = await contacts.card(user, book, uid)
        if found is not None and fields_of(found.jcard)[0] != text:
            raise contact_exists(
                book, uid, "The same call added this contact before, which was changed since"
            )
        emails = [email["address"] for email in fields["emails"]]
        if found is None and emails:
            other = await _with_email(contacts, user, book, emails)
            if other is not None:
                raise contact_exists(
                    book,
                    other.contact_id,
                    "The user's address book has a contact with one of these emails already",
                )
        # What the owner allows: the contact, by its UID, as the book holds it, if at all
        digest = digest_of(book.book_id, uid, found.jcard if found is not None else None)
        if preview.asked:
            if found is not None:
                return preview.answer(added(text, preview.language), digest)
            return preview.answer(adding(text, preview.language), digest)
        preview.check(digest)
        if found is not None:
            response.status_code = 200
            return contact_of(found)
        return contact_of(await contacts.add(user, card))

    return routes
