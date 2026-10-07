from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
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


async def create(
    client: AsyncClient, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.post(
        "/contracts/v1/tasks/projects", json=body, headers=AS_MMAUDET | (headers or {})
    )


async def test_a_new_project_comes_with_its_first_board(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks creates a project only as it creates a board outside any project, of the same name
    response = await create(client, name="Q4 launch", key_prefix="LAUNCH")

    assert response.status_code == 201, response.text
    answer = response.json()
    board = boundary.tasks.boards[answer["board"]["board_id"]]
    assert answer == {
        "project_id": board.project_id,
        "role": "admin",
        "personal": False,
        "space": False,
        "board": {
            "board_id": board.id,
            "key_prefix": "LAUNCH",
            "project_id": board.project_id,
            "role": "admin",
            "inbox": False,
            "space": False,
            "archived": False,
            "open_tasks": 0,
            "untrusted": {"name": "Q4 launch", "project_name": "Q4 launch"},
        },
        "untrusted": {"name": "Q4 launch"},
    }
    assert boundary.tasks.writes == [
        ("POST", "/api/boards", {"name": "Q4 launch", "keyPrefix": "LAUNCH"})
    ]
    assert [section["name"] for section in board.sections] == ["To do", "In progress", "Done"]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": "  ", "key_prefix": "LAUNCH"}, id="a blank name"),
        pytest.param({"name": "n" * 101, "key_prefix": "LAUNCH"}, id="a name over 100 characters"),
        pytest.param({"name": "Q4 launch"}, id="no key prefix"),
        pytest.param({"name": "Q4 launch", "key_prefix": "launch"}, id="a prefix in lower case"),
        pytest.param(
            {"name": "Q4 launch", "key_prefix": "4Q"}, id="a prefix starting with a digit"
        ),
        pytest.param({"name": "Q4 launch", "key_prefix": "LAUNCHQ4ABC"}, id="a prefix over 10"),
        pytest.param({"name": "Q4 launch", "key_prefix": "Q4-"}, id="a prefix with a dash"),
        pytest.param(
            {"name": "Q4 launch", "key_prefix": "LAUNCH", "members": ["alice@twake.test"]},
            id="a field it does not take",
        ),
    ],
)
async def test_an_invalid_project_is_refused_before_anything_is_created(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    response = await create(client, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.writes == []


async def test_the_prefix_of_the_inbox_is_taken_before_anything_is_created(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks keeps INBOX for every Inbox: the owner is not asked about a call it would refuse
    response = await create(client, name="Q4 launch", key_prefix="INBOX")

    assert response.status_code == 409
    assert response.json()["code"] == "key_prefix_taken"
    assert boundary.tasks.writes == []


async def test_a_prefix_tasks_keeps_for_another_board_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tasks.kept_prefixes.add("LAUNCH")

    response = await create(client, name="Q4 launch", key_prefix="LAUNCH")

    assert response.status_code == 409
    assert response.json()["code"] == "key_prefix_taken"
    assert "LAUNCH" in response.json()["detail"]
    assert boundary.tasks.boards == {}


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Créer le projet « Q4 launch » dans Twake Tasks, avec un premier tableau du même nom,"
            " dont les clés des tâches commencent par LAUNCH\n"
            "Tu en es le seul membre, avec le rôle administrateur. Tasks ne prévient personne.",
        ),
        (
            "en",
            "Create the project “Q4 launch” in Twake Tasks, with a first board of the same name,"
            " whose task keys start with LAUNCH\n"
            "You are its only member, as its admin. Tasks tells nobody.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_would_be_created_and_creates_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    response = await create(client, asking_preview(language), name="Q4 launch", key_prefix="LAUNCH")

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.tasks.writes == []
    assert boundary.tasks.boards == {}


@pytest.mark.parametrize(
    ("projects", "told"),
    [
        (["Q4 launch"], "You are a member of a project of that name already: this one is another."),
        (
            ["Q4 launch", "q4  LAUNCH"],
            "You are a member of 2 projects of that name already: this one is another.",
        ),
    ],
)
async def test_a_preview_tells_of_the_projects_of_that_name_already(
    client: AsyncClient, boundary: FakeBoundary, projects: list[str], told: str
) -> None:
    # Tasks takes a project of a name the user has already as another one
    for number, project in enumerate(projects):
        boundary.tasks.board(f"Board {number}", f"B{number}", MMAUDET, project=project)

    response = await create(client, asking_preview("en"), name="Q4 launch", key_prefix="LAUNCH")

    summary, _ = preview_of(response)
    assert summary.splitlines()[1] == told


async def test_the_owner_who_allowed_what_they_were_shown_gets_the_project(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(
        await create(client, asking_preview("fr"), name="Q4 launch", key_prefix="LAUNCH")
    )

    response = await create(client, allowed_after(digest), name="Q4 launch", key_prefix="LAUNCH")

    assert response.status_code == 201, response.text
    assert [board.project for board in boundary.tasks.boards.values()] == ["Q4 launch"]


async def test_a_project_of_that_name_created_since_the_preview_stops_the_call(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    _, digest = preview_of(
        await create(client, asking_preview("fr"), name="Q4 launch", key_prefix="LAUNCH")
    )
    # The same call went through meanwhile, as when the agent tried it twice
    created = await create(client, name="Q4 launch", key_prefix="LAUNCH")
    assert created.status_code == 201, created.text

    response = await create(client, allowed_after(digest), name="Q4 launch", key_prefix="LAUNCH")

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert len(boundary.tasks.boards) == 1
