from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")


async def list_boards(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get("/contracts/v1/tasks/boards", params=params, headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_the_user_lists_the_boards_of_their_projects(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    owned = tasks_member("mmaudet", "admin")
    inbox = boundary.tasks.board("Inbox", "INBOX", owned, inbox=True, project="Personal")
    website = boundary.tasks.board("Website", "WEB", MMAUDET, ALICE, project="Marketing")
    boundary.tasks.task(website, "Launch the new site")
    # A board of a project the user is not a member of
    boundary.tasks.board("Budget", "BUD", ALICE, project="Finance")

    answer = await list_boards(client)

    assert answer == {
        "boards": [
            {
                "board_id": inbox.id,
                "key_prefix": "INBOX",
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


async def test_a_personal_account_sees_only_the_boards_outside_organizations(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # A token whose user has no org_id: Tasks takes them for a personal account, and the
    # contracts show what Tasks shows them, an organization's board answering like an unknown one
    boundary.tasks.organizations[MMAUDET.email] = None
    owned = tasks_member("mmaudet", "admin")
    personal = boundary.tasks.board("Inbox", "INBOX", owned, inbox=True, organization=None)
    website = boundary.tasks.board("Website", "WEB", MMAUDET, ALICE)
    task = boundary.tasks.task(website, "Launch the new site", assignees=[MMAUDET])

    answer = await list_boards(client)
    read = await client.get(
        f"/contracts/v1/tasks/boards/{website.id}/tasks/{task.id}", headers=AS_MMAUDET
    )

    assert [listed["board_id"] for listed in answer["boards"]] == [personal.id]
    assert read.status_code == 404
    assert read.json()["code"] == "board_not_found"


async def test_the_board_of_a_space_says_so(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.tasks.board("Roadmap", "MAP", MMAUDET, managed=True, project="Team space")

    answer = await list_boards(client)

    assert [listed["space"] for listed in answer["boards"]] == [True]


async def test_archived_boards_are_listed_only_when_asked(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    current = boundary.tasks.board("Website", "WEB", MMAUDET)
    archived = boundary.tasks.board("Former site", "OLD", MMAUDET, archived=True)

    by_default = await list_boards(client)
    asked = await list_boards(client, include_archived="true")

    assert [listed["board_id"] for listed in by_default["boards"]] == [current.id]
    assert [(listed["board_id"], listed["archived"]) for listed in asked["boards"]] == [
        (archived.id, True),
        (current.id, False),
    ]


@pytest.mark.parametrize(("boards", "truncated"), [(100, False), (101, True)])
async def test_the_list_holds_a_hundred_boards_at_most(
    client: AsyncClient, boundary: FakeBoundary, boards: int, truncated: bool
) -> None:
    for number in range(boards):
        boundary.tasks.board(f"Board {number:03}", f"B{number}", MMAUDET)

    answer = await list_boards(client)

    assert len(answer["boards"]) == 100
    assert answer["truncated"] is truncated
