"""contacts.contact.update.v1: the owner changes a contact in one of their own address books."""

import copy
import json
from typing import Any

import pytest
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
FAMILY = "3e5f7a9b-1c2d-4e3f-8a4b-5c6d7e8f9a0b"
# A contact as the Contacts web app writes it, with what the contracts never change
PHOTO = ["photo", {}, "uri", "data:image/png;base64,iVBORw0KGgo="]
CATEGORIES = ["categories", {}, "text", "starred"]
SOURCE = ["x-twake-source", {}, "text", "import"]
JEAN = jcard(
    JEAN_ID,
    "Jean Dupont",
    ["n", {}, "text", ["Dupont", "Jean", "", "", ""]],
    ["email", {"type": "work"}, "text", "jean.dupont@example.com"],
    ["email", {"type": "home"}, "text", "jean@dupont.example"],
    ["tel", {"type": "cell"}, "text", "+33 6 12 34 56 78"],
    ["org", {}, "text", ["Example", "Sales"]],
    ["role", {}, "text", "Sales director"],
    ["note", {}, "text", "Met at the trade fair."],
    PHOTO,
    CATEGORIES,
    SOURCE,
)
NEW_PHONE = [{"number": "+33 6 98 76 54 32", "type": "cell"}]


async def update(
    client: AsyncClient, book_id: str, card: str, *headers: dict[str, str], **body: Any
) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything, on
    the contact of the card of that name, but for its .vcf."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.patch(
        f"/contracts/v1/contacts/address-books/{book_id}/contacts/{contact_id(card + '.vcf')}",
        json=body,
        headers=sent,
    )


def jeans(boundary: FakeBoundary, card: list[Any] = JEAN) -> FakeAddressBook:
    """The owner's default book, holding Jean's contact."""
    book = boundary.contacts.owners()
    book.cards[f"{JEAN_ID}.vcf"] = copy.deepcopy(card)
    return book


def properties(card: list[Any], *names: str) -> list[list[Any]]:
    return [prop for prop in card[1] if prop[0] in names]


async def test_only_the_fields_given_change(client: AsyncClient, boundary: FakeBoundary) -> None:
    book = jeans(boundary)

    response = await update(
        client, f"{OWN}~contacts", JEAN_ID, phones=NEW_PHONE, organization="Acme"
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["book_id"], answer["contact_id"]) == (
        f"{OWN}~contacts",
        contact_id(f"{JEAN_ID}.vcf"),
    )
    assert answer["untrusted"]["phones"] == [{"number": "+33 6 98 76 54 32", "type": "cell"}]
    assert answer["untrusted"]["organization"] == "Acme"
    assert boundary.contacts.writes == [("PUT", f"/addressbooks/{OWN}/contacts/{JEAN_ID}.vcf")]
    # The rest of the card stays as it was, where it was, its unit of organization too
    assert book.cards[f"{JEAN_ID}.vcf"] == jcard(
        JEAN_ID,
        "Jean Dupont",
        ["n", {}, "text", ["Dupont", "Jean", "", "", ""]],
        ["email", {"type": "work"}, "text", "jean.dupont@example.com"],
        ["email", {"type": "home"}, "text", "jean@dupont.example"],
        ["tel", {"type": "cell"}, "text", "+33 6 98 76 54 32"],
        ["org", {}, "text", ["Acme", "Sales"]],
        ["role", {}, "text", "Sales director"],
        ["note", {}, "text", "Met at the trade fair."],
        PHOTO,
        CATEGORIES,
        SOURCE,
    )


async def test_null_clears_a_field_and_a_list_replaces_the_whole_list(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)

    response = await update(
        client,
        f"{OWN}~contacts",
        JEAN_ID,
        emails=[{"address": "jean.dupont@acme.example", "type": "work"}],
        note=None,
        title=None,
        organization=None,
    )

    assert response.status_code == 200, response.text
    card = book.cards[f"{JEAN_ID}.vcf"]
    assert properties(card, "email") == [
        ["email", {"type": "work"}, "text", "jean.dupont@acme.example"]
    ]
    assert properties(card, "note", "role", "org") == []
    assert properties(card, "photo", "categories", "x-twake-source") == [PHOTO, CATEGORIES, SOURCE]


async def test_the_answer_gives_back_what_the_change_replaced_as_it_was(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary)

    response = await update(
        client,
        f"{OWN}~contacts",
        JEAN_ID,
        phones=NEW_PHONE,
        organization="Acme",
        note=None,
        family_name="Durand",
    )

    assert response.status_code == 200, response.text
    # Only the fields the change changed, the name Contacts shows too, under untrusted
    assert response.json()["untrusted"]["previous"] == {
        "name": "Jean Dupont",
        "family_name": "Dupont",
        "phones": [{"number": "+33 6 12 34 56 78", "type": "cell"}],
        "organization": "Example",
        "note": "Met at the trade fair.",
    }


async def test_a_change_that_changes_nothing_gives_back_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary)

    response = await update(client, f"{OWN}~contacts", JEAN_ID, organization="Example")

    assert response.status_code == 200, response.text
    assert response.json()["untrusted"]["previous"] == {}


async def test_the_name_contacts_shows_follows_the_names_it_is_made_of(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)

    response = await update(client, f"{OWN}~contacts", JEAN_ID, family_name="Durand")

    assert response.status_code == 200, response.text
    assert properties(book.cards[f"{JEAN_ID}.vcf"], "fn", "n") == [
        ["fn", {}, "text", "Jean Durand"],
        ["n", {}, "text", ["Durand", "Jean", "", "", ""]],
    ]


async def test_a_name_set_apart_from_the_names_stays_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    card = copy.deepcopy(JEAN)
    card[1][2] = ["fn", {}, "text", "Dr Jean Dupont"]
    book = jeans(boundary, card)

    kept = await update(client, f"{OWN}~contacts", JEAN_ID, family_name="Durand")
    cleared = await update(client, f"{OWN}~contacts", JEAN_ID, name=None)

    assert kept.status_code == 200, kept.text
    assert kept.json()["untrusted"]["name"] == "Dr Jean Dupont"
    # Without the name set apart, the one made of the names shows
    assert cleared.status_code == 200, cleared.text
    assert properties(book.cards[f"{JEAN_ID}.vcf"], "fn") == [["fn", {}, "text", "Jean Durand"]]


async def test_the_job_title_is_written_where_contacts_shows_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A card another app wrote, with its job title in TITLE alone
    card = jcard("anne", "Anne Martin", ["title", {}, "text", "Engineer"])
    book = boundary.contacts.owners()
    book.cards["anne.vcf"] = card

    response = await update(client, f"{OWN}~contacts", "anne", title="Lead engineer")

    assert response.status_code == 200, response.text
    assert response.json()["untrusted"]["title"] == "Lead engineer"
    assert properties(book.cards["anne.vcf"], "role", "title") == [
        ["role", {}, "text", "Lead engineer"],
        ["title", {}, "text", "Lead engineer"],
    ]


async def test_a_contact_left_with_nothing_to_show_it_by_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = boundary.contacts.owners("collected")
    book.cards["info.vcf"] = jcard(
        "info", "info@example.com", ["email", {}, "text", "info@example.com"]
    )

    response = await update(client, f"{OWN}~collected", "info", emails=[])

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.contacts.writes == []


async def test_a_contact_of_the_collected_book_changes_too(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = boundary.contacts.owners("collected")
    book.cards["info.vcf"] = jcard("info", None, ["email", {}, "text", "info@example.com"])

    response = await update(client, f"{OWN}~collected", "info", organization="Acme")

    assert response.status_code == 200, response.text
    # A card without a name gets the one Contacts shows
    assert properties(book.cards["info.vcf"], "fn", "org") == [
        ["fn", {}, "text", "Acme"],
        ["org", {}, "text", "Acme"],
    ]


async def test_a_contact_of_a_book_others_own_is_never_changed(
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
        await update(client, book_id, JEAN_ID, phones=NEW_PHONE)
        for book_id in (f"{OWN}~{DELEGATED}", f"{OWN}~{SUBSCRIBED}", f"{DOMAIN}~rooms")
    ]

    assert [response.status_code for response in responses] == [403, 403, 403]
    assert {response.json()["code"] for response in responses} == {"address_book_read_only"}
    assert boundary.contacts.writes == []


async def test_a_contact_of_a_book_contacts_lets_the_owner_only_read_is_not_changed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    family = boundary.contacts.owners(FAMILY, privileges=["dav:read"])
    family.cards[f"{JEAN_ID}.vcf"] = copy.deepcopy(JEAN)

    response = await update(client, f"{OWN}~{FAMILY}", JEAN_ID, phones=NEW_PHONE)

    assert response.status_code == 403
    assert response.json()["code"] == "address_book_read_only"
    assert boundary.contacts.writes == []


async def test_a_change_contacts_refuses_for_the_owners_rights_is_a_problem(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    boundary.contacts.failing_writes = "refused"

    response = await update(client, f"{OWN}~contacts", JEAN_ID, phones=NEW_PHONE)

    assert response.status_code == 403
    assert response.json()["code"] == "address_book_read_only"
    assert book.cards[f"{JEAN_ID}.vcf"] == JEAN


async def test_an_unknown_contact_is_not_found(client: AsyncClient, boundary: FakeBoundary) -> None:
    jeans(boundary)

    unknown = await update(client, f"{OWN}~contacts", "unknown", phones=NEW_PHONE)
    elsewhere = await update(client, f"{ALICE_CALENDAR_ID}~contacts", JEAN_ID, phones=NEW_PHONE)

    assert (unknown.status_code, unknown.json()["code"]) == (404, "contact_not_found")
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (404, "address_book_not_found")
    assert boundary.contacts.writes == []


@pytest.mark.parametrize(
    ("body", "code"),
    [
        pytest.param({}, "invalid_request", id="nothing to change"),
        pytest.param({"photo": "x"}, "invalid_request", id="a field it does not take"),
        pytest.param({"emails": [{"address": "jean"}]}, "invalid_email", id="an invalid email"),
        pytest.param({"phones": [{"number": "call me"}]}, "invalid_phone", id="an invalid phone"),
    ],
)
async def test_an_invalid_change_is_refused_before_anything_is_written(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any], code: str
) -> None:
    jeans(boundary)

    response = await update(client, f"{OWN}~contacts", JEAN_ID, **body)

    assert response.status_code == 400
    assert response.json()["code"] == code
    assert boundary.contacts.writes == []


async def test_a_change_that_changes_nothing_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary)

    response = await update(client, f"{OWN}~contacts", JEAN_ID, organization="Example")

    assert response.status_code == 200, response.text
    assert boundary.contacts.writes == []


async def test_the_preview_tells_each_change_as_it_would_be_and_as_it_was(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary)

    response = await update(
        client,
        f"{OWN}~contacts",
        JEAN_ID,
        asking_preview("en"),
        phones=NEW_PHONE,
        title=None,
        note="Calls on Mondays.",
        birthday="1980-05-17",
    )

    summary, _ = preview_of(response)
    assert summary == (
        "Change the contact “Jean Dupont” in your address book:\n"
        "Phones: “+33 6 98 76 54 32” (mobile), instead of “+33 6 12 34 56 78” (mobile)\n"
        "Job title: none, instead of “Sales director”\n"
        "Birthday: Saturday 17 May 1980, instead of none\n"
        "Note:\n"
        "\tCalls on Mondays.\n"
        "Instead of:\n"
        "\tMet at the trade fair.\n"
        "Twake Contacts tells nobody."
    )
    assert boundary.contacts.writes == []


async def test_the_preview_speaks_the_owners_language(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    family = boundary.contacts.owners(FAMILY, display_name="Famille")
    family.cards[f"{JEAN_ID}.vcf"] = copy.deepcopy(JEAN)

    response = await update(
        client, f"{OWN}~{FAMILY}", JEAN_ID, asking_preview("fr"), family_name="Durand"
    )

    summary, _ = preview_of(response)
    assert summary == (
        "Modifier le contact « Jean Dupont » dans ton carnet d'adresses « Famille » :\n"
        "Nom : « Jean Durand », au lieu de « Jean Dupont »\n"
        "Nom de famille : « Durand », au lieu de « Dupont »\n"
        "Twake Contacts ne prévient personne."
    )


async def test_a_contact_changed_since_the_preview_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    book = jeans(boundary)
    _, digest = preview_of(
        await update(client, f"{OWN}~contacts", JEAN_ID, asking_preview("en"), phones=NEW_PHONE)
    )
    # The user changes the contact in Contacts meanwhile
    book.cards[f"{JEAN_ID}.vcf"][1].append(["nickname", {}, "text", "JD"])

    response = await update(
        client, f"{OWN}~contacts", JEAN_ID, allowed_after(digest), phones=NEW_PHONE
    )

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.contacts.writes == []


async def test_the_call_its_owner_allowed_changes_the_contact(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    jeans(boundary)
    _, digest = preview_of(
        await update(client, f"{OWN}~contacts", JEAN_ID, asking_preview("en"), phones=NEW_PHONE)
    )

    response = await update(
        client, f"{OWN}~contacts", JEAN_ID, allowed_after(digest), phones=NEW_PHONE
    )

    assert response.status_code == 200, response.text
    assert len(boundary.contacts.writes) == 1


async def test_a_contact_that_would_be_larger_than_contacts_takes_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A photo that leaves the card just under what Contacts takes, which more phones take it over
    card = copy.deepcopy(JEAN)
    photo = ["photo", {}, "uri", ""]
    card[1].append(photo)
    photo[3] = "A" * (1024 * 1024 - 100 - len(json.dumps(card, separators=(",", ":"))))
    jeans(boundary, card)
    phones = [{"number": f"+33 6 12 34 56 {index:02d}"} for index in range(10)]

    response = await update(client, f"{OWN}~contacts", JEAN_ID, phones=phones)

    assert response.status_code == 413
    assert response.json()["code"] == "contact_too_large"
    assert boundary.contacts.writes == []


async def test_the_preview_of_a_change_of_all_a_contact_holds_fits_what_the_harness_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Each field as long as a read gives it, changed to as long as the contract takes, in
    # characters of four bytes
    wide, other = "\U0001f600", "\U0001f601"
    singles = ("nickname", "org", "role")
    card = jcard(
        "full",
        wide * 300,
        ["n", {}, "text", [wide * 300, wide * 300, "", "", ""]],
        *([name, {}, "text", wide * 300] for name in singles),
        *(["email", {}, "text", f"{'a' * 300}{index}@example.com"] for index in range(25)),
        *(["tel", {}, "text", f"+33 6 12 34 56 {index:02d}"] for index in range(25)),
        *(["adr", {}, "text", ["", "", *[wide * 300] * 5]] for _ in range(25)),
        ["note", {}, "text", wide * 20_000],
        ["bday", {}, "text", wide * 300],
    )
    boundary.contacts.owners(FAMILY, display_name=wide * 300).cards["full.vcf"] = card
    body = {
        name: other * 200
        for name in ("name", "given_name", "family_name", "nickname", "organization", "title")
    } | {
        "emails": [{"address": f"{'b' * 200}{index}@example.org"} for index in range(10)],
        "phones": [{"number": f"+33 7 12 34 56 {index:02d}"} for index in range(10)],
        "addresses": [{part: other * 200 for part in ("street", "locality", "country")}] * 5,
        "note": other * 10_000,
        "birthday": "1980-05-17",
    }

    response = await update(client, f"{OWN}~{FAMILY}", "full", asking_preview("en"), **body)

    summary, _ = preview_of(response)
    assert summary.endswith("\nTwake Contacts tells nobody.")
