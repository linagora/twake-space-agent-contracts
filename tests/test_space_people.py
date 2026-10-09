"""space.people.read.v1: the people of the user's spaces in Twake Space, found by their username,
email or name, each with the spaces they share with the user."""

import httpx
import pytest
from httpx import AsyncClient, Response

from tests.fakes import FakeBoundary, as_space_owner, space_person, space_uuid

PEOPLE = "/contracts/v1/space/people"
MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
JEANNE = space_person("jmartin", "Jeanne Martin")
PAUL = space_person("pmartin", "Paul Martin")
ALICE = space_person("alice", "Alice Durand")
HELENE = space_person("hdupre", "Hélène Dupré")


def usernames(response: Response) -> list[str]:
    """The people found, by username."""
    assert response.status_code == 200, response.text
    return [person["username"] for person in response.json()["people"]]


async def test_the_user_finds_each_person_of_their_spaces_once_with_the_spaces_they_share(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space("Design", {MMAUDET: "admin", JEANNE: "editor", ALICE: "viewer"})
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer", JEANNE: "admin"})
    # A space the user is not a member of: whoever is there alone stays unknown
    boundary.space.space("Finance", {ALICE: "admin", PAUL: "editor"})

    response = await client.get(PEOPLE, params={"q": "martin"}, headers=as_space_owner())

    assert response.status_code == 200, response.text
    assert response.json() == {
        "people": [
            {
                "username": "jmartin",
                "user_id": JEANNE.user_id,
                "email": JEANNE.email,
                "you": False,
                "spaces": [
                    {"space_id": design.id, "role": "editor", "untrusted": {"name": "Design"}},
                    {"space_id": roadmap.id, "role": "admin", "untrusted": {"name": "Roadmap"}},
                ],
                "untrusted": {"display_name": "Jeanne Martin"},
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize(
    "q",
    [
        pytest.param("HDUP", id="username"),
        pytest.param(f"{HELENE.email.split('@')[0]}@", id="email"),
        pytest.param("helene dupre", id="name, without accents"),
        pytest.param("Hélène\N{ZERO WIDTH SPACE}", id="name, with what a reader does not see"),
    ],
)
async def test_a_person_is_found_by_username_email_or_name_whatever_the_case_and_accents(
    client: AsyncClient, boundary: FakeBoundary, q: str
) -> None:
    boundary.space.space("Design", {MMAUDET: "admin", HELENE: "editor", ALICE: "viewer"})

    response = await client.get(PEOPLE, params={"q": q}, headers=as_space_owner())

    assert usernames(response) == ["hdupre"]


async def test_the_user_is_found_too_and_told_apart(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.space.space("Design", {MMAUDET: "admin", JEANNE: "editor"})
    boundary.space.space("Roadmap", {MMAUDET: "viewer"})

    response = await client.get(PEOPLE, params={"q": "maudet"}, headers=as_space_owner())

    assert usernames(response) == ["mmaudet"]
    found = response.json()["people"][0]
    assert found["you"] is True
    assert [space["untrusted"]["name"] for space in found["spaces"]] == ["Design", "Roadmap"]


@pytest.mark.parametrize("q", ["m", " m ", "m\N{ZERO WIDTH SPACE}", "m" * 101])
async def test_words_out_of_bounds_are_invalid(
    client: AsyncClient, boundary: FakeBoundary, q: str
) -> None:
    boundary.space.space("Design", {MMAUDET: "admin", JEANNE: "editor"})

    response = await client.get(PEOPLE, params={"q": q}, headers=as_space_owner())

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.space.requests == []


async def test_twenty_people_come_by_username_and_truncated_says_more_were_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    martins = {space_person(f"martin{number:02}"): "viewer" for number in reversed(range(25))}
    boundary.space.space("Design", {MMAUDET: "admin", **martins})

    many = await client.get(PEOPLE, params={"q": "martin"}, headers=as_space_owner())
    few = await client.get(PEOPLE, params={"q": "martin0"}, headers=as_space_owner())

    assert usernames(many) == [f"martin{number:02}" for number in range(20)]
    assert many.json()["truncated"] is True
    assert usernames(few) == [f"martin{number:02}" for number in range(10)]
    assert few.json()["truncated"] is False


async def test_the_members_of_the_first_fifty_spaces_by_name_are_searched_five_at_once(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    last = boundary.space.space("Space 50", {MMAUDET: "viewer", PAUL: "admin"})
    first = boundary.space.space("Space 00", {MMAUDET: "viewer", JEANNE: "admin"})
    rooms = [first] + [
        boundary.space.space(f"Space {number:02}", {MMAUDET: "viewer"}) for number in range(1, 50)
    ]

    response = await client.get(PEOPLE, params={"q": "martin"}, headers=as_space_owner())

    assert usernames(response) == ["jmartin"]
    assert response.json()["truncated"] is True
    read = {path for method, path in boundary.space.requests if path != "/spaces"}
    assert read == {f"/spaces/{room.id}" for room in rooms}
    assert f"/spaces/{last.id}" not in read
    assert boundary.space.most_at_once == 5


async def test_a_space_gone_while_its_members_are_read_is_left_out(
    client: AsyncClient, boundary: FakeBoundary, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The user left the space, or its admin deleted it, once Space listed it
    boundary.space.space("Design", {MMAUDET: "admin", JEANNE: "editor"})
    boundary.space.space("Roadmap", {MMAUDET: "viewer", PAUL: "admin"})
    handle = boundary.space.handle

    def leaving(request: httpx.Request) -> httpx.Response:
        answer = handle(request)
        if request.url.path == "/spaces":
            del boundary.space.spaces[space_uuid("Roadmap")]
        return answer

    monkeypatch.setattr(boundary.space, "handle", leaving)

    response = await client.get(PEOPLE, params={"q": "martin"}, headers=as_space_owner())

    assert usernames(response) == ["jmartin"]
    assert response.json()["truncated"] is False
