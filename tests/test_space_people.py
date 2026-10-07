"""space.people.read.v1: the people of the user's organization, as Twake Space finds them in its
directory, to add to a space."""

from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, space_person

JEANNE = space_person("jmartin", "Jeanne Martin")
PAUL = space_person("pmartin", "Paul Martin")
ALICE = space_person("alice", "Alice Durand")


async def test_the_user_finds_the_people_of_their_organization(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.space.people(JEANNE, PAUL, ALICE)

    response = await client.get(
        "/contracts/v1/space/people", params={"q": "martin"}, headers=AS_MMAUDET
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "people": [
            {
                "username": "jmartin",
                "email": "jmartin@twake.test",
                "untrusted": {"display_name": "Jeanne Martin"},
            },
            {
                "username": "pmartin",
                "email": "pmartin@twake.test",
                "untrusted": {"display_name": "Paul Martin"},
            },
        ],
        "next_page": None,
    }


async def test_the_people_come_twenty_a_page(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.space.people(*(space_person(f"martin{number:02}") for number in range(25)))

    first = await client.get(
        "/contracts/v1/space/people", params={"q": "martin"}, headers=AS_MMAUDET
    )
    second = await client.get(
        "/contracts/v1/space/people",
        params={"q": "martin", "page": first.json()["next_page"]},
        headers=AS_MMAUDET,
    )

    assert first.status_code == second.status_code == 200
    assert [person["username"] for person in first.json()["people"]] == [
        f"martin{number:02}" for number in range(20)
    ]
    assert first.json()["next_page"] == 2
    assert [person["username"] for person in second.json()["people"]] == [
        f"martin{number:02}" for number in range(20, 25)
    ]
    assert second.json()["next_page"] is None


async def test_without_words_all_the_people_of_the_organization_are_listed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.space.people(JEANNE, ALICE, space_person("bob"))
    # Someone of another organization, whom the user never finds
    boundary.space.people(PAUL, organization="acme")

    response = await client.get("/contracts/v1/space/people", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert [person["username"] for person in response.json()["people"]] == [
        "jmartin",
        "alice",
        "bob",
    ]
    assert response.json()["people"][2]["untrusted"] == {"display_name": None}


async def test_fewer_than_two_characters_to_find_are_invalid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.space.people(JEANNE)

    responses = [
        await client.get("/contracts/v1/space/people", params={"q": q}, headers=AS_MMAUDET)
        for q in ("m", " m ", "m\u200b")
    ]

    assert [response.status_code for response in responses] == [400, 400, 400]
    assert {response.json()["code"] for response in responses} == {"invalid_request"}
    assert boundary.space.requests == []
