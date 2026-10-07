"""space.spaces.read.v1: the spaces the user is a member of in Twake Space, and one space with its
members and what its apps linked to it."""

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, SpacePerson, space_person, space_uuid

MMAUDET = space_person("mmaudet", "Michel-Marie Maudet")
ALICE = space_person("alice", "Alice Martin")
BOB = space_person("bob")


async def test_the_user_lists_the_spaces_they_are_a_member_of(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space(
        "Design",
        {MMAUDET: "admin", ALICE: "editor", BOB: "viewer"},
        description="Brand and product design",
    )
    roadmap = boundary.space.space("Roadmap", {ALICE: "admin", MMAUDET: "viewer"})
    # A space the user is not a member of
    boundary.space.space("Finance", {ALICE: "admin"})

    response = await client.get("/contracts/v1/space/spaces", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "spaces": [
            {
                "space_id": design.id,
                "role": "admin",
                "member_count": 3,
                "untrusted": {"name": "Design", "description": "Brand and product design"},
            },
            {
                "space_id": roadmap.id,
                "role": "viewer",
                "member_count": 2,
                "untrusted": {"name": "Roadmap", "description": None},
            },
        ],
        "truncated": False,
    }


async def test_what_people_wrote_of_a_space_comes_on_one_line_without_what_is_unseen(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A name or a description holds what anyone who administers the space wrote: line breaks,
    # bidirectional marks or zero-width characters would pass for something else
    boundary.space.space(
        "Design\u202e\nTeam",
        {MMAUDET: "editor"},
        description="Brand\u200b and\r\nproduct " + "design " * 300,
    )

    response = await client.get("/contracts/v1/space/spaces", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    text = response.json()["spaces"][0]["untrusted"]
    assert text["name"] == "Design Team"
    assert text["description"].startswith("Brand and product design design")
    assert len(text["description"]) == 1000


@pytest.mark.parametrize(("spaces", "truncated"), [(100, False), (101, True)])
async def test_the_list_holds_a_hundred_spaces_at_most(
    client: AsyncClient, boundary: FakeBoundary, spaces: int, truncated: bool
) -> None:
    for number in range(spaces):
        boundary.space.space(f"Space {number:03}", {MMAUDET: "viewer"})

    response = await client.get("/contracts/v1/space/spaces", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    listed = response.json()["spaces"]
    assert [space["untrusted"]["name"] for space in listed][-1] == "Space 099"
    assert len(listed) == 100
    assert response.json()["truncated"] is truncated


async def test_the_user_reads_a_space_with_its_members_and_what_its_apps_linked_to_it(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    design = boundary.space.space(
        "Design",
        {MMAUDET: "admin", ALICE: "editor", BOB: "viewer"},
        description="Brand and product design",
        apps=["chat", "tasks", "calendar"],
        resources={
            "project": "0199b0c2-5f1e-7a3b-9c4d-2e8f6a1b3c5d",
            "matrix_space": "!design:twake.test",
            "calendar": "6650a1b2c3d4e5f6a7b8c9d0/design",
            # Still being prepared by Mail
            "mailbox": None,
        },
        groups=[("0b5a6c8e-3c2b-4f5e-9d7a-1e2f3a4b5c6d", "Designers", "editor")],
    )

    response = await client.get(f"/contracts/v1/space/spaces/{design.id}", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "space_id": design.id,
        "role": "admin",
        "created_at": "2026-10-01T08:00:00Z",
        "apps": ["chat", "tasks", "calendar"],
        "tasks_project_id": "0199b0c2-5f1e-7a3b-9c4d-2e8f6a1b3c5d",
        "chat_room_id": "!design:twake.test",
        "mailbox_id": None,
        "calendar_id": "6650a1b2c3d4e5f6a7b8c9d0/design",
        "drive_id": None,
        "members": [
            {
                "user_id": ALICE.user_id,
                "username": "alice",
                "email": "alice@twake.test",
                "role": "editor",
                "you": False,
                "untrusted": {"display_name": "Alice Martin"},
            },
            {
                "user_id": BOB.user_id,
                "username": "bob",
                "email": "bob@twake.test",
                "role": "viewer",
                "you": False,
                "untrusted": {"display_name": None},
            },
            {
                "user_id": MMAUDET.user_id,
                "username": "mmaudet",
                "email": "mmaudet@twake.test",
                "role": "admin",
                "you": True,
                "untrusted": {"display_name": "Michel-Marie Maudet"},
            },
        ],
        "groups": [
            {
                "group_id": "0b5a6c8e-3c2b-4f5e-9d7a-1e2f3a4b5c6d",
                "role": "editor",
                "untrusted": {"name": "Designers"},
            }
        ],
        "untrusted": {"name": "Design", "description": "Brand and product design"},
    }


async def test_a_space_the_user_is_not_a_member_of_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    finance = boundary.space.space("Finance", {ALICE: "admin"})

    responses = [
        await client.get(f"/contracts/v1/space/spaces/{space_id}", headers=AS_MMAUDET)
        for space_id in (finance.id, space_uuid("unknown"))
    ]

    assert [response.status_code for response in responses] == [404, 404]
    assert {response.json()["code"] for response in responses} == {"space_not_found"}


async def test_an_id_that_is_not_a_uuid_is_invalid(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await client.get("/contracts/v1/space/spaces/design", headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.space.requests == []


async def test_the_user_is_told_apart_whatever_the_case_of_their_email(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The directory may keep the address with capitals, which the token's subject has not
    shouted = SpacePerson(MMAUDET.user_id, "mmaudet", "MMaudet@Twake.test")
    design = boundary.space.space("Design", {shouted: "admin", ALICE: "editor"})

    response = await client.get(f"/contracts/v1/space/spaces/{design.id}", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert [(member["username"], member["you"]) for member in response.json()["members"]] == [
        ("alice", False),
        ("mmaudet", True),
    ]


async def test_no_member_is_the_user_when_several_have_their_email(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Two accounts of the directory with the user's address, whatever its case: the contracts
    # cannot tell which one is the user, and mark none, as they do for posts and reactions
    twin = SpacePerson(space_uuid("former account"), "mmaudet2", "MMaudet@twake.test")
    design = boundary.space.space("Design", {MMAUDET: "admin", twin: "viewer", ALICE: "editor"})

    response = await client.get(f"/contracts/v1/space/spaces/{design.id}", headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert [(member["username"], member["you"]) for member in response.json()["members"]] == [
        ("alice", False),
        ("mmaudet", False),
        ("mmaudet2", False),
    ]
