"""contacts.addressbooks.read.v1: the address books the user reads in Twake Contacts: their own,
those other people shared with them, and their domain's."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.contacts import UNTRUSTED, line
from twake_space_agent_contracts.contacts.carddav import Book, Contacts, Kind

LONGEST_NAME = 200
LONGEST_DESCRIPTION = 1000


class BookText(BaseModel):
    """What people wrote of an address book: its name and its description."""

    name: str | None
    description: str | None


class AddressBook(BaseModel):
    book_id: str
    kind: Kind = Field(
        description="personal, one of the user's own; collected, theirs too, where Contacts "
        "collects addresses; shared, someone else's that they share with the user or the user "
        "subscribed to; domain, one of the user's domain, such as its directory of members."
    )
    default: bool = Field(
        description="Whether it is the user's default address book, where create_contact adds "
        "contacts."
    )
    writable: bool = Field(
        description="Whether update_contact and delete_contact act in it: in the user's own "
        "address books only."
    )
    contact_count: int | None
    untrusted: BookText


class AddressBooks(BaseModel):
    address_books: list[AddressBook]


def listed(book: Book) -> AddressBook:
    return AddressBook(
        book_id=book.book_id,
        kind=book.kind,
        default=book.default,
        writable=book.writable,
        contact_count=book.contact_count,
        untrusted=BookText(
            name=line(book.display_name, LONGEST_NAME)[0],
            description=line(book.description, LONGEST_DESCRIPTION)[0],
        ),
    )


def router(contacts: Contacts, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/contacts", tags=["contacts.addressbooks.read.v1"])

    @routes.get(
        "/address-books",
        operation_id="list_address_books",
        summary="List the user's address books in Twake Contacts",
        description=(
            "Lists the address books of the user you act for in Twake Contacts: their own, the "
            "default one first, where create_contact adds contacts, then the one Contacts "
            "collects addresses in; those other people share with them, or they subscribed to; "
            "then their domain's, such as its directory of members. kind says which. writable "
            "tells where update_contact and delete_contact act: in the user's own address books "
            "only, never in a shared one or the domain's. Give book_id to read_contact. "
            f"{UNTRUSTED} Example: (no parameters)."
        ),
    )
    async def list_address_books(user: Annotated[User, Depends(caller)]) -> AddressBooks:
        owner = await contacts.owner(user)
        books = await contacts.books(user, owner)
        return AddressBooks(address_books=[listed(book) for book in books])

    return routes
