"""contacts.contact.delete.v1: the owner deletes a contact of one of their own address books, for
good, confirming each one."""

import copy
from typing import Any

from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import (
    ALICE_CALENDAR_ID,
    MMAUDET_CALENDAR_ID,
    MMAUDET_DOMAIN_ID,
    READ_WRITE_ACCESS,
    FakeAddressBook,
    FakeBoundary,
    contact_id,
    jcard,
)

OWN = MMAUDET_CALENDAR_ID
DOMAIN = MMAUDET_DOMAIN_ID
JEAN_ID = "0b5a6c8e-3c2b-4f5e-9d7a-1e2f3a4b5c6d"
DELEGATED = "0b2c4d6e-8f1a-4b3c-8d5e-7f9a1b3c5d7e"
SUBSCRIBED = "1c3d5e7f-9a2b-4c4d-9e6f-8a1b2c3d4e5f"
JEAN = jcard(
    JEAN_ID,
    "Jean Dupont",
    ["n", {}, "text", ["Dupont", "Jean", "", "", ""]],
    ["email", {"type": "work"}, "text", "jean.dupont@example.com"],
    ["email", {"type": "home"}, "text", "jean@dupont.example"],
    ["tel", {"type": "cell"}, "text", "+33 6 12 34 56 78"],
    ["org", {}, "text", ["Example", "Sales"]],
    ["photo", {}, "uri", "data:image/png;base64,iVBORw0KGgo="],
)


async def delete(
    client: AsyncClient, book_id: str, card: str, *headers: dict[str, str]
) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything, on
    the contact of the card of that name, but for its .vcf."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    contact = contact_id(f"{card}.vcf")
    return await client.delete(
        f"/contracts/v1/contacts/address-books/{book_id}/contacts/{contact}", headers=sent
    )


def jeans(boundary: FakeBoundary, name: str = "contacts") -> FakeAddressBook:
    """A book of the owner's, holding Jean's contact."""
    book = boundary.contacts.owners(name)
    book.cards[f"{JEAN_ID}.vcf"] = copy.deepcopy(JEAN)
    return book


async def test_a_contact_is_deleted_for_good_and_answered_as_it_was(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    book.cards["other.vcf"] = jcard("other", "Paul Martin")

    response = await delete(client, f"{OWN}~contacts", JEAN_ID)

    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    assert (answer["book_id"], answer["contact_id"]) == (
        f"{OWN}~contacts",
        contact_id(f"{JEAN_ID}.vcf"),
    )
    assert answer["untrusted"]["name"] == "Jean Dupont"
    assert answer["untrusted"]["emails"][0] == {
        "address": "jean.dupont@example.com",
        "type": "work",
    }
    assert boundary.contacts.writes == [("DELETE", f"/addressbooks/{OWN}/contacts/{JEAN_ID}.vcf")]
    assert list(book.cards) == ["other.vcf"]


async def test_a_contact_of_the_collected_book_is_deleted_too(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary, "collected")

    response = await delete(client, f"{OWN}~collected", JEAN_ID)

    assert response.status_code == 200, response.text
    assert book.cards == {}


async def test_a_contact_of_a_book_others_own_is_never_deleted(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Even where Contacts lets the user write: the book is someone else's, or the domain's
    team = boundary.contacts.book(ALICE_CALENDAR_ID, "team")
    team.cards[f"{JEAN_ID}.vcf"] = copy.deepcopy(JEAN)
    boundary.contacts.book(OWN, DELEGATED, source=team, access=READ_WRITE_ACCESS)
    boundary.contacts.book(OWN, SUBSCRIBED, source=team, subscribed=True, publicly_writable=True)
    rooms = boundary.contacts.book(DOMAIN, "rooms", group=True, members_write=True)
    rooms.cards[f"{JEAN_ID}.vcf"] = copy.deepcopy(JEAN)

    responses = [
        await delete(client, book_id, JEAN_ID)
        for book_id in (f"{OWN}~{DELEGATED}", f"{OWN}~{SUBSCRIBED}", f"{DOMAIN}~rooms")
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert {response.json()["code"] for response in responses} == {"address_book_read_only"}
    assert boundary.contacts.writes == []
    assert list(team.cards) == list(rooms.cards) == [f"{JEAN_ID}.vcf"]


async def test_a_delete_contacts_refuses_for_the_owners_rights_is_a_problem(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    boundary.contacts.failing_writes = "refused"

    response = await delete(client, f"{OWN}~contacts", JEAN_ID)

    assert response.status_code == 403
    assert response.json()["code"] == "address_book_read_only"
    assert list(book.cards) == [f"{JEAN_ID}.vcf"]


async def test_an_unknown_contact_is_not_found(client: AsyncClient, boundary: FakeBoundary) -> None:
    jeans(boundary)

    unknown = await delete(client, f"{OWN}~contacts", "unknown")
    elsewhere = await delete(client, f"{ALICE_CALENDAR_ID}~contacts", JEAN_ID)
    rewritten = await delete(client, f"{OWN}~contacts", f"{JEAN_ID}.json")

    assert (unknown.status_code, unknown.json()["code"]) == (404, "contact_not_found")
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (404, "address_book_not_found")
    assert (rewritten.status_code, rewritten.json()["code"]) == (404, "contact_not_found")
    assert boundary.contacts.writes == []


async def test_the_preview_tells_which_contact_goes_and_that_it_goes_for_good(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary)

    response = await delete(client, f"{OWN}~contacts", JEAN_ID, asking_preview("en"))

    summary, _ = preview_of(response)
    assert summary == (
        "Delete the contact “Jean Dupont” from your address book, for good: Twake Contacts keeps"
        " no trash.\n"
        "Emails: <jean.dupont@example.com> (work), <jean@dupont.example> (home)\n"
        "Phones: “+33 6 12 34 56 78” (mobile)\n"
        "Organization: “Example”\n"
        "Twake Contacts tells nobody."
    )
    assert boundary.contacts.writes == []


async def test_the_preview_speaks_the_owners_language(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary, "collected")

    response = await delete(client, f"{OWN}~collected", JEAN_ID, asking_preview("fr"))

    summary, _ = preview_of(response)
    assert summary.splitlines()[0] == (
        "Supprimer le contact « Jean Dupont » de tes contacts collectés, définitivement :"
        " Twake Contacts n'a pas de corbeille."
    )


async def test_the_call_its_owner_allowed_deletes_the_contact(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    _, digest = preview_of(await delete(client, f"{OWN}~contacts", JEAN_ID, asking_preview("en")))

    response = await delete(client, f"{OWN}~contacts", JEAN_ID, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert book.cards == {}


async def test_a_contact_changed_since_the_preview_is_not_deleted(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    _, digest = preview_of(await delete(client, f"{OWN}~contacts", JEAN_ID, asking_preview("en")))
    # The user gives the contact another email in Contacts meanwhile: the owner confirmed deleting
    # the contact they were shown
    book.cards[f"{JEAN_ID}.vcf"][1].append(["email", {}, "text", "jd@example.org"])

    response = await delete(client, f"{OWN}~contacts", JEAN_ID, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert list(book.cards) == [f"{JEAN_ID}.vcf"]


async def test_a_contact_gone_since_the_preview_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    _, digest = preview_of(await delete(client, f"{OWN}~contacts", JEAN_ID, asking_preview("en")))
    del book.cards[f"{JEAN_ID}.vcf"]

    response = await delete(client, f"{OWN}~contacts", JEAN_ID, allowed_after(digest))

    assert response.status_code == 404
    assert response.json()["code"] == "contact_not_found"
