from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")


async def open_boards(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.post(
        "/contracts/v1/tasks/boards/open", params=params, headers=AS_MMAUDET
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_the_user_opens_the_boards_of_their_projects(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    owned = tasks_member("mmaudet", "admin")
    inbox = boundary.tasks.board("Inbox", "INBOX", owned, inbox=True, project="Personal")
    website = boundary.tasks.board("Website", "WEB", MMAUDET, ALICE, project="Marketing")
    boundary.tasks.task(website, "Launch the new site")
    # A board of a project the user is not a member of
    boundary.tasks.board("Budget", "BUD", ALICE, project="Finance")

    answer = await open_boards(client)

    assert answer == {
        "boards": [
            {
                "board_id": inbox.id,
                "key_prefix": "INBOX",
                "project_id": tasks_id("project Personal"),
                "role": "admin",
                "inbox": True,
                "space": False,
                "archived": False,
                "open_tasks": 0,
                "untrusted": {"name": "Inbox", "project_name": "Personal"},
            },
            {
                "board_id": website.id,
                "key_prefix": "WEB",
                "project_id": tasks_id("project Marketing"),
                "role": "editor",
                "inbox": False,
                "space": False,
                "archived": False,
                "open_tasks": 1,
                "untrusted": {"name": "Website", "project_name": "Marketing"},
            },
        ],
        "truncated": False,
    }


async def test_opening_tasks_sets_up_the_inbox_and_accepts_invitations(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # What opening its web app does, which makes listing boards an act rather than a read
    boundary.tasks.board("Website", "WEB", ALICE, project="Marketing")
    boundary.tasks.invitations[MMAUDET.email] = [("Marketing", "editor")]

    first = await open_boards(client)
    again = await open_boards(client)

    assert [
        (board["untrusted"]["name"], board["inbox"], board["role"]) for board in first["boards"]
    ] == [("Inbox", True, "admin"), ("Website", False, "editor")]
    assert again == first
    assert boundary.tasks.invitations == {}


async def test_a_personal_account_opens_only_the_boards_outside_organizations(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A token whose user has no org_id: Tasks takes them for a personal account, and shows them
    # what lies outside any organization only
    boundary.tasks.organizations[MMAUDET.email] = None
    owned = tasks_member("mmaudet", "admin")
    personal = boundary.tasks.board("Inbox", "INBOX", owned, inbox=True, organization=None)
    boundary.tasks.board("Website", "WEB", MMAUDET, ALICE)

    answer = await open_boards(client)

    assert [listed["board_id"] for listed in answer["boards"]] == [personal.id]


async def test_the_board_of_a_space_says_so(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.tasks.board("Inbox", "INBOX", tasks_member("mmaudet", "admin"), inbox=True)
    boundary.tasks.board("Roadmap", "MAP", MMAUDET, managed=True, project="Team space")

    answer = await open_boards(client)

    assert [listed["space"] for listed in answer["boards"]] == [False, True]


async def test_archived_boards_are_listed_only_when_asked(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    inbox = boundary.tasks.board("Inbox", "INBOX", tasks_member("mmaudet", "admin"), inbox=True)
    current = boundary.tasks.board("Website", "WEB", MMAUDET)
    archived = boundary.tasks.board("Former site", "OLD", MMAUDET, archived=True)

    by_default = await open_boards(client)
    asked = await open_boards(client, include_archived="true")

    assert [listed["board_id"] for listed in by_default["boards"]] == [inbox.id, current.id]
    assert [(listed["board_id"], listed["archived"]) for listed in asked["boards"]] == [
        (inbox.id, False),
        (archived.id, True),
        (current.id, False),
    ]


@pytest.mark.parametrize(("boards", "truncated"), [(99, False), (100, True)])
async def test_the_list_holds_a_hundred_boards_at_most(
    client: AsyncClient, boundary: FakeBoundary, boards: int, truncated: bool
) -> None:
    # With the Inbox that opening Tasks sets up, the user has one board more
    for number in range(boards):
        boundary.tasks.board(f"Board {number:03}", f"B{number}", MMAUDET)

    answer = await open_boards(client)

    assert len(answer["boards"]) == 100
    assert answer["boards"][0]["inbox"] is True
    assert answer["truncated"] is truncated
