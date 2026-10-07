from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")


async def list_projects(client: AsyncClient) -> dict[str, Any]:
    response = await client.get("/contracts/v1/tasks/projects", headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_the_user_lists_the_projects_they_are_a_member_of(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    owned = tasks_member("mmaudet", "admin")
    boundary.tasks.board("Inbox", "INBOX", owned, inbox=True, project="Personal")
    boundary.tasks.board("Website", "WEB", MMAUDET, ALICE, project="Marketing")
    boundary.tasks.board("Blog", "BLOG", MMAUDET, ALICE, project="Marketing")
    boundary.tasks.board(
        "Roadmap", "MAP", tasks_member("mmaudet", "viewer"), managed=True, project="Team space"
    )
    # A project the user is not a member of
    boundary.tasks.board("Budget", "BUD", ALICE, project="Finance")

    answer = await list_projects(client)

    assert answer == {
        "projects": [
            {
                "project_id": tasks_id("project Marketing"),
                "role": "editor",
                "personal": False,
                "space": False,
                "untrusted": {"name": "Marketing"},
            },
            {
                "project_id": tasks_id("project Personal"),
                "role": "admin",
                "personal": True,
                "space": False,
                "untrusted": {"name": "Personal"},
            },
            {
                "project_id": tasks_id("project Team space"),
                "role": "viewer",
                "personal": False,
                "space": True,
                "untrusted": {"name": "Team space"},
            },
        ],
        "truncated": False,
    }


async def test_listing_projects_only_reads(client: AsyncClient, boundary: FakeBoundary) -> None:
    # Unlike open_boards, it neither sets up the Inbox nor accepts the user's invitations
    boundary.tasks.board("Website", "WEB", ALICE, project="Marketing")
    boundary.tasks.invitations[MMAUDET.email] = [("Marketing", "editor")]

    answer = await list_projects(client)

    assert answer == {"projects": [], "truncated": False}
    assert boundary.tasks.invitations == {MMAUDET.email: [("Marketing", "editor")]}
    assert boundary.tasks.requests == [("/api/projects", {})]


@pytest.mark.parametrize(("projects", "truncated"), [(100, False), (101, True)])
async def test_the_list_holds_a_hundred_projects_at_most(
    client: AsyncClient, boundary: FakeBoundary, projects: int, truncated: bool
) -> None:
    for number in range(projects):
        boundary.tasks.board(f"Board {number:03}", f"B{number}", MMAUDET, project=f"P{number:03}")

    answer = await list_projects(client)

    assert len(answer["projects"]) == 100
    assert answer["truncated"] is truncated
