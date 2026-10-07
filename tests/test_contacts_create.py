"""contacts.contact.create.v1: the owner adds a contact to their own default address book."""

from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import ALICE_CALENDAR_ID, MMAUDET_CALENDAR_ID, READ_ACCESS, FakeBoundary, jcard

OWN = MMAUDET_CALENDAR_ID
DEFAULT_BOOK = f"{OWN}~contacts"
JEANNE = {
    "given_name": "Jeanne",
    "family_name": "Martin",
    "emails": [{"address": "jeanne.martin@example.com", "type": "work"}],
    "phones": [{"number": "+33 6 12 34 56 78", "type": "cell"}],
    "organization": "Example",
    "title": "Engineer",
    "addresses": [
        {
            "type": "work",
            "street": "12 rue de la Paix",
            "locality": "Paris",
            "postal_code": "75002",
            "country": "France",
        }
    ],
    "note": "Met at the trade fair.\nPrefers calls.",
    "birthday": "1980-05-17",
}


async def create(client: AsyncClient, *headers: dict[str, str], **body: Any) -> Response:
    """The call as the harness sends it, with what it adds to ask for a preview, if anything."""
    sent = AS_MMAUDET | {name: value for more in headers for name, value in more.items()}
    return await client.post("/contracts/v1/contacts/contacts", json=body, headers=sent)


def stored(boundary: FakeBoundary, contact_id: str) -> dict[str, list[list[Any]]]:
    """The properties of a card of the owner's default book, by name."""
    card = boundary.contacts.owners().cards[f"{contact_id}.vcf"]
    properties: dict[str, list[list[Any]]] = {}
    for prop in card[1]:
        properties.setdefault(prop[0], []).append(prop)
    return properties


async def test_a_contact_is_added_to_the_users_default_address_book(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, **JEANNE)

    assert response.status_code == 201, response.text
    created = response.json()
    contact_id = created["contact_id"]
    assert created == {
        "book_id": DEFAULT_BOOK,
        "contact_id": contact_id,
        "kind": "personal",
        "writable": True,
        "truncated": False,
        "untrusted": {
            "name": "Jeanne Martin",
            "given_name": "Jeanne",
            "family_name": "Martin",
            "nickname": None,
            "emails": [{"address": "jeanne.martin@example.com", "type": "work"}],
            "phones": [{"number": "+33 6 12 34 56 78", "type": "cell"}],
            "organization": "Example",
            "title": "Engineer",
            "addresses": [
                {
                    "type": "work",
                    "street": "12 rue de la Paix",
                    "locality": "Paris",
                    "region": None,
                    "postal_code": "75002",
                    "country": "France",
                }
            ],
            "note": "Met at the trade fair.\nPrefers calls.",
            "birthday": "1980-05-17",
        },
    }
    assert boundary.contacts.writes == [("PUT", f"/addressbooks/{OWN}/contacts/{contact_id}.vcf")]
    card = stored(boundary, contact_id)
    # A vCard 4.0 as the Contacts web app writes one: its job title in ROLE, which it shows
    assert card["version"] == [["version", {}, "text", "4.0"]]
    assert card["uid"] == [["uid", {}, "text", contact_id]]
    assert card["fn"] == [["fn", {}, "text", "Jeanne Martin"]]
    assert card["n"] == [["n", {}, "text", ["Martin", "Jeanne", "", "", ""]]]
    assert card["email"] == [["email", {"type": "work"}, "text", "jeanne.martin@example.com"]]
    assert card["tel"] == [["tel", {"type": "cell"}, "text", "+33 6 12 34 56 78"]]
    assert card["org"] == [["org", {}, "text", "Example"]]
    assert card["role"] == [["role", {}, "text", "Engineer"]]
    assert card["adr"] == [
        [
            "adr",
            {"type": "work"},
            "text",
            ["", "", "12 rue de la Paix", "Paris", "", "75002", "France"],
        ]
    ]
    assert card["bday"] == [["bday", {}, "date", "1980-05-17"]]
    assert "prodid" in card


@pytest.mark.parametrize(
    ("body", "name"),
    [
        pytest.param({"given_name": "Jeanne"}, "Jeanne", id="a given name"),
        pytest.param({"family_name": "Martin"}, "Martin", id="a family name"),
        pytest.param(
            {"organization": "Example", "title": "Support"}, "Example", id="an organization"
        ),
        pytest.param({"nickname": "JM", "note": "Neighbour"}, "JM", id="a nickname"),
        pytest.param(
            {"emails": [{"address": "info@example.com"}, {"address": "sales@example.com"}]},
            "info@example.com",
            id="emails",
        ),
        pytest.param({"phones": [{"number": "0612345678"}]}, "0612345678", id="a phone"),
        pytest.param(
            {"name": "Dr Jeanne Martin", "given_name": "Jeanne"}, "Dr Jeanne Martin", id="a name"
        ),
    ],
)
async def test_the_name_contacts_shows_comes_from_what_is_given(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any], name: str
) -> None:
    response = await create(client, **body)

    assert response.status_code == 201, response.text
    assert response.json()["untrusted"]["name"] == name
    assert stored(boundary, response.json()["contact_id"])["fn"] == [["fn", {}, "text", name]]


async def test_text_is_written_without_what_a_reader_does_not_see(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, given_name="Jeanne‮​", family_name=" Martin\x07 ", note="Hello⁦\n")

    assert response.status_code == 201, response.text
    card = stored(boundary, response.json()["contact_id"])
    assert card["n"] == [["n", {}, "text", ["Martin", "Jeanne", "", "", ""]]]
    assert card["note"] == [["note", {}, "text", "Hello"]]


async def test_the_same_call_made_again_adds_no_second_contact(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    first = await create(client, **JEANNE)
    again = await create(client, **JEANNE)

    assert first.status_code == 201, first.text
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert len(boundary.contacts.owners().cards) == 1
    assert len(boundary.contacts.writes) == 1


async def test_a_contact_with_an_email_the_address_book_has_already_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.contacts.owners().cards["jm.vcf"] = jcard(
        "jm", "J. Martin", ["email", {"type": "home"}, "text", "mailto:Jeanne.Martin@Example.com"]
    )

    response = await create(client, **JEANNE)

    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "contact_exists"
    assert (problem["book_id"], problem["contact_id"]) == (DEFAULT_BOOK, "jm")
    assert boundary.contacts.writes == []


async def test_an_email_known_outside_the_default_book_is_no_reason_to_refuse(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Contacts collects addresses, and others share their books: the user's own book is theirs
    boundary.contacts.owners("collected").cards["jm.vcf"] = jcard(
        "jm", None, ["email", {}, "text", "jeanne.martin@example.com"]
    )
    team = boundary.contacts.book(ALICE_CALENDAR_ID, "team")
    team.cards["jeanne.vcf"] = jcard(
        "jeanne", "Jeanne", ["email", {}, "text", "jeanne.martin@example.com"]
    )
    boundary.contacts.book(
        OWN, "0b2c4d6e-8f1a-4b3c-8d5e-7f9a1b3c5d7e", source=team, access=READ_ACCESS
    )
    # Nor one that merely holds the address in its text
    boundary.contacts.owners().cards["boss.vcf"] = jcard(
        "boss", "Paul", ["note", {}, "text", "Reports to jeanne.martin@example.com"]
    )

    response = await create(client, **JEANNE)

    assert response.status_code == 201, response.text


async def test_the_contact_the_same_call_added_then_changed_is_not_added_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    first = await create(client, given_name="Jeanne", family_name="Martin")
    contact_id = first.json()["contact_id"]
    # The user gives her a phone in Contacts
    boundary.contacts.owners().cards[f"{contact_id}.vcf"][1].append(
        ["tel", {}, "text", "0612345678"]
    )

    again = await create(client, given_name="Jeanne", family_name="Martin")

    assert again.status_code == 409
    assert (again.json()["code"], again.json()["contact_id"]) == ("contact_exists", contact_id)
    assert len(boundary.contacts.writes) == 1


@pytest.mark.parametrize(
    "address",
    [
        "jeanne.martin",
        "jeanne martin@example.com",
        "jeanne@example",
        "@example.com",
        "jeanne@@example.com",
        "jeanne..martin@example.com",
        "<jeanne@example.com>",
        "jeanne@example.com\nBcc: eve@example.com",
    ],
)
async def test_an_email_that_is_no_address_is_refused(
    client: AsyncClient, boundary: FakeBoundary, address: str
) -> None:
    response = await create(client, given_name="Jeanne", emails=[{"address": address}])

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_email"
    assert boundary.contacts.writes == []


@pytest.mark.parametrize(
    "number", ["call me", "12", "+33 6 12 34 56 78 9 0 1 2 3 4 5 6 7 8", "06\n12", "+33 (0)6 xx"]
)
async def test_a_phone_that_is_no_number_is_refused(
    client: AsyncClient, boundary: FakeBoundary, number: str
) -> None:
    response = await create(client, given_name="Jeanne", phones=[{"number": number}])

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_phone"
    assert boundary.contacts.writes == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="nothing"),
        pytest.param({"note": "Someone"}, id="nothing to name the contact by"),
        pytest.param({"given_name": " ​ "}, id="a blank name"),
        pytest.param({"given_name": "Jeanne", "photo": "x"}, id="a field it does not take"),
        pytest.param(
            {"given_name": "Jeanne", "emails": [{"address": "a@example.com", "kind": "work"}]},
            id="an email with a field it does not take",
        ),
        pytest.param(
            {"given_name": "Jeanne", "phones": [{"number": "0612345678", "type": "pager"}]},
            id="a type it does not take",
        ),
        pytest.param(
            {
                "given_name": "Jeanne",
                "emails": [{"address": f"a{i}@example.com"} for i in range(11)],
            },
            id="more than 10 emails",
        ),
        pytest.param({"given_name": "J" * 201}, id="a name over 200 characters"),
        pytest.param({"given_name": "Jeanne", "birthday": "17/05/1980"}, id="a birthday no day"),
        pytest.param(
            {"given_name": "Jeanne", "addresses": [{"type": "work"}]}, id="an empty address"
        ),
    ],
)
async def test_an_invalid_contact_is_refused_before_anything_is_written(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    response = await create(client, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.contacts.writes == []


async def test_the_preview_tells_what_is_added_and_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await create(client, asking_preview("en"), **JEANNE)

    summary, _ = preview_of(response)
    assert summary == (
        "Add the contact “Jeanne Martin” to your address book\n"
        "Emails: <jeanne.martin@example.com> (work)\n"
        "Phones: “+33 6 12 34 56 78” (mobile)\n"
        "Organization: “Example”\n"
        "Job title: “Engineer”\n"
        "Addresses: “12 rue de la Paix, 75002 Paris, France” (work)\n"
        "Birthday: Saturday 17 May 1980\n"
        "Note:\n"
        "\tMet at the trade fair.\n"
        "\tPrefers calls.\n"
        "Twake Contacts tells nobody."
    )
    assert boundary.contacts.writes == []


async def test_the_preview_speaks_the_owners_language(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    body = {"name": "Dr Jeanne Martin", "given_name": "Jeanne", "nickname": "JM"}

    response = await create(client, asking_preview("fr-FR,fr;q=0.9"), **body)

    summary, _ = preview_of(response)
    assert summary == (
        "Ajouter le contact « Dr Jeanne Martin » à ton carnet d'adresses\n"
        "Prénom : « Jeanne »\n"
        "Surnom : « JM »\n"
        "Twake Contacts ne prévient personne."
    )


async def test_the_preview_of_the_same_call_made_again_says_nothing_is_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    await create(client, **JEANNE)

    response = await create(client, asking_preview("en"), **JEANNE)

    summary, _ = preview_of(response)
    assert summary == (
        "The contact “Jeanne Martin” is in your address book already: nothing is added."
    )


async def test_the_call_its_owner_allowed_adds_the_contact(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("en"), **JEANNE))

    response = await create(client, allowed_after(digest), **JEANNE)

    assert response.status_code == 201, response.text


async def test_a_contact_added_since_the_preview_is_not_added_again(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(await create(client, asking_preview("en"), **JEANNE))
    # The same call, made by another turn meanwhile
    await create(client, **JEANNE)

    response = await create(client, allowed_after(digest), **JEANNE)

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert len(boundary.contacts.writes) == 1


async def test_the_preview_of_a_contact_with_all_it_takes_fits_what_the_harness_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Each field as long as the contract takes, in characters of four bytes
    wide = "\U0001f600"
    body = {
        name: wide * 200
        for name in ("name", "given_name", "family_name", "nickname", "organization", "title")
    } | {
        "emails": [
            {"address": f"{'a' * 60}{index}@{'b' * 60}.example", "type": "work"}
            for index in range(10)
        ],
        "phones": [{"number": "+33 6 12 34 56 78 90", "type": "cell"} for _ in range(10)],
        "addresses": [
            {
                part: wide * 200
                for part in ("street", "locality", "region", "postal_code", "country")
            }
            for _ in range(5)
        ],
        "note": wide * 10_000,
        "birthday": "1980-05-17",
    }

    summary, _ = preview_of(await create(client, asking_preview("en"), **body))

    assert summary.endswith("\nTwake Contacts tells nobody.")


async def test_contacts_losing_the_contact_answers_a_problem(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.contacts.failing_writes = "lost"

    response = await create(client, **JEANNE)

    assert response.status_code == 502
    assert response.json()["code"] == "contacts_unavailable"
    assert boundary.contacts.owners().cards == {}


async def test_a_contact_contacts_kept_but_answered_late_is_added(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.contacts.failing_writes = "landed"

    response = await create(client, **JEANNE)

    assert response.status_code == 201, response.text
    assert len(boundary.contacts.owners().cards) == 1


async def test_a_card_larger_than_contacts_takes_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # As nginx refuses a body larger than it takes in front of esn-sabre
    boundary.contacts.body_limit = 200

    response = await create(client, **JEANNE)

    assert response.status_code == 413
    assert response.json()["code"] == "contact_too_large"
    assert boundary.contacts.owners().cards == {}
