"""contacts.contacts.read.v1: searching the contacts of the address books the owner reads."""

from typing import Any

import httpx
import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET
from tests.fakes import (
    ALICE_CALENDAR_ID,
    MMAUDET_CALENDAR_ID,
    MMAUDET_DOMAIN_ID,
    READ_ACCESS,
    FakeBoundary,
    jcard,
)

OWN = MMAUDET_CALENDAR_ID
DOMAIN = MMAUDET_DOMAIN_ID
DELEGATED = "0b2c4d6e-8f1a-4b3c-8d5e-7f9a1b3c5d7e"


async def search(client: AsyncClient, q: str, **params: Any) -> Response:
    return await client.get(
        "/contracts/v1/contacts/search", params={"q": q, **params}, headers=AS_MMAUDET
    )


def found(response: Response) -> list[tuple[str, str]]:
    """The contacts found, by their book and their id, in the order given."""
    assert response.status_code == 200, response.text
    return [(item["book_id"], item["contact_id"]) for item in response.json()["contacts"]]


def contact(uid: str, name: str, *properties: list[Any]) -> list[Any]:
    return jcard(uid, name, *properties)


async def test_contacts_are_found_in_the_owners_books_and_their_domains(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    own = boundary.contacts.owners()
    own.cards["jean.vcf"] = contact(
        "jean",
        "Jean Dupont",
        ["email", {"type": "work"}, "text", "jean.dupont@example.com"],
        ["tel", {"type": "cell"}, "text", "+33 6 12 34 56 78"],
        ["org", {}, "text", ["Example", "Sales"]],
        ["note", {}, "text", "Calls on Mondays"],
    )
    own.cards["other.vcf"] = contact("other", "Paul Martin")
    boundary.contacts.owners("collected").cards["dupont.vcf"] = contact(
        "dupont", "", ["email", {}, "text", "dupont@mail.example"]
    )
    team = boundary.contacts.book(ALICE_CALENDAR_ID, "team")
    team.cards["claire.vcf"] = contact("claire", "Claire Dupont")
    boundary.contacts.book(OWN, DELEGATED, source=team, access=READ_ACCESS)
    members = boundary.contacts.book(DOMAIN, "domain-members", group=True)
    members.cards["anne.vcf"] = contact("anne", "Anne Dupontel")
    # Alice's own book, which she shares with nobody, is never searched
    boundary.contacts.book(ALICE_CALENDAR_ID, "private").cards["secret.vcf"] = contact(
        "secret", "Bernard Dupont"
    )

    response = await search(client, "dupont")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "contacts": [
            {
                "book_id": f"{DOMAIN}~domain-members",
                "contact_id": "anne",
                "kind": "domain",
                "untrusted": {
                    "name": "Anne Dupontel",
                    "emails": [],
                    "phones": [],
                    "organization": None,
                },
            },
            {
                "book_id": f"{OWN}~{DELEGATED}",
                "contact_id": "claire",
                "kind": "shared",
                "untrusted": {
                    "name": "Claire Dupont",
                    "emails": [],
                    "phones": [],
                    "organization": None,
                },
            },
            {
                "book_id": f"{OWN}~collected",
                "contact_id": "dupont",
                "kind": "collected",
                "untrusted": {
                    "name": None,
                    "emails": ["dupont@mail.example"],
                    "phones": [],
                    "organization": None,
                },
            },
            {
                "book_id": f"{OWN}~contacts",
                "contact_id": "jean",
                "kind": "personal",
                "untrusted": {
                    "name": "Jean Dupont",
                    "emails": ["jean.dupont@example.com"],
                    "phones": ["+33 6 12 34 56 78"],
                    "organization": "Example",
                },
            },
        ],
        "truncated": False,
    }


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        pytest.param("a.c", ["dot"], id="a dot"),
        pytest.param("(sales)", ["parenthesis"], id="parentheses, unbalanced as an expression"),
        pytest.param("n (s", ["parenthesis"], id="an opening parenthesis alone"),
        pytest.param(".*", [], id="what would match anything"),
        pytest.param("a+b", ["plus"], id="a plus"),
        pytest.param("[ab]", [], id="brackets"),
        pytest.param("\\d", [], id="a backslash"),
        pytest.param("x|y", [], id="a bar"),
    ],
)
async def test_the_text_is_found_as_written_never_as_an_expression(
    client: AsyncClient, boundary: FakeBoundary, q: str, expected: list[str]
) -> None:
    # The search matches its query as a pattern: the contract escapes it, so that it is found
    # as written
    cards = boundary.contacts.owners().cards
    cards["abc.vcf"] = contact("abc", "abc")
    cards["dot.vcf"] = contact("dot", "a.c")
    cards["parenthesis.vcf"] = contact("parenthesis", "Ann (Sales)")
    cards["plus.vcf"] = contact("plus", "a+b")
    cards["digit.vcf"] = contact("digit", "Agent 7")

    response = await search(client, q)

    assert [contact_id for _, contact_id in found(response)] == expected


async def test_the_words_vcard_writes_a_card_in_find_no_contact(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Contacts searches the vCard text of each card, its property names too
    boundary.contacts.owners().cards["jean.vcf"] = contact(
        "jean",
        "Jean Dupont",
        ["email", {"type": "work"}, "text", "jean@example.com"],
        ["tel", {"type": "cell"}, "text", "0612345678"],
    )
    boundary.contacts.owners().cards["telma.vcf"] = contact("telma", "Telma Ray")

    by_property = [await search(client, words) for words in ("vcard", "email", "work", "uid")]
    by_name = await search(client, "tel")

    assert [found(response) for response in by_property] == [[], [], [], []]
    assert found(by_name) == [(f"{OWN}~contacts", "telma")]


@pytest.mark.parametrize(
    ("q", "name"),
    [
        pytest.param("Dupont, Jean", "Dupont, Jean", id="a comma, which vCard escapes"),
        pytest.param("R&D; Lab", "R&D; Lab", id="a semicolon, which vCard escapes"),
        pytest.param(
            "data.jsonl", "data.jsonl file", id=".json, which esn-sabre removes from URLs"
        ),
        pytest.param("ÉLODIE", "Élodie Brun", id="another case"),
    ],
)
async def test_text_vcard_or_esn_sabre_would_change_is_found_as_written(
    client: AsyncClient, boundary: FakeBoundary, q: str, name: str
) -> None:
    boundary.contacts.owners().cards["found.vcf"] = contact("found", name)

    response = await search(client, q)

    assert found(response) == [(f"{OWN}~contacts", "found")]


async def test_a_search_gives_limit_contacts_and_says_when_more_are_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    for index in range(3):
        boundary.contacts.owners().cards[f"martin{index}.vcf"] = contact(
            f"martin{index}", f"Martin {index}"
        )

    first = await search(client, "martin", limit="2")
    all_of_them = await search(client, "martin", limit="3")

    assert found(first) == [(f"{OWN}~contacts", "martin0"), (f"{OWN}~contacts", "martin1")]
    assert first.json()["truncated"] is True
    assert len(found(all_of_them)) == 3
    assert all_of_them.json()["truncated"] is False


async def test_what_people_wrote_comes_without_what_a_reader_does_not_see(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.contacts.owners().cards["eve.vcf"] = contact(
        "eve", "Eve‮ Martin​", ["org", {}, "text", "Evil\x07 Corp"]
    )

    response = await search(client, "martin")

    assert response.status_code == 200, response.text
    assert response.json()["contacts"][0]["untrusted"] == {
        "name": "Eve Martin",
        "emails": [],
        "phones": [],
        "organization": "Evil Corp",
    }


@pytest.mark.parametrize(
    "q",
    [
        pytest.param("a", id="one character"),
        pytest.param(" a ", id="one character among blanks"),
        pytest.param("a" * 101, id="more than 100 characters"),
    ],
)
async def test_a_search_takes_2_to_100_characters(
    client: AsyncClient, boundary: FakeBoundary, q: str
) -> None:
    response = await search(client, q)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.contacts.searches == []


async def test_contacts_down_answers_a_problem(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.calendar.down = True

    response = await search(client, "dupont")

    assert response.status_code == 502
    assert response.json()["code"] == "contacts_unavailable"


@pytest.mark.parametrize(
    "found",
    [
        pytest.param({"_embedded": {"dav:item": "jean"}}, id="contacts in no list"),
        pytest.param({"_embedded": {"dav:item": ["jean"]}}, id="a contact in no object"),
        pytest.param({"_embedded": {"dav:item": [{"_links": {}}]}}, id="a contact without data"),
    ],
)
async def test_contacts_found_in_an_unexpected_form_are_a_problem(
    client: AsyncClient, boundary: FakeBoundary, found: dict[str, Any]
) -> None:
    boundary.contacts.answers["/contacts/api/"] = httpx.Response(200, json=found)

    response = await search(client, "dupont")

    assert response.status_code == 502
    assert response.json()["code"] == "contacts_unavailable"
