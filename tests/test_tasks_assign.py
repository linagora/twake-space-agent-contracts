from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, TasksBoard, TasksMember, TasksTask, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")
BOB = tasks_member("bob")


def website(boundary: FakeBoundary, *members: TasksMember, **more: Any) -> TasksBoard:
    return boundary.tasks.board("Website", "WEB", *(members or (MMAUDET, ALICE, BOB)), **more)


async def assign(
    client: AsyncClient, task: TasksTask, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.put(
        f"/contracts/v1/tasks/boards/{task.board}/tasks/{task.id}/assignees",
        json=body,
        headers=AS_MMAUDET | (headers or {}),
    )


async def test_a_task_is_assigned_to_the_members_named_by_their_email(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page")

    response = await assign(client, task, assignees=["alice@twake.test", "mmaudet@twake.test"])

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["task_id"], answer["assignees"], answer["assigned_to_me"]) == (
        task.id,
        ["alice@twake.test", "mmaudet@twake.test"],
        True,
    )
    assert boundary.tasks.writes == [
        (
            "PUT",
            f"/api/boards/{board.id}/tasks/{task.id}/assignees",
            {"userIds": [ALICE.user_id, MMAUDET.user_id]},
        )
    ]


@pytest.mark.parametrize(
    ("assignees", "after"),
    [
        pytest.param(["alice@twake.test"], ["alice@twake.test"], id="another member"),
        pytest.param([], [], id="nobody"),
    ],
)
async def test_the_list_given_replaces_the_assignees(
    client: AsyncClient, boundary: FakeBoundary, assignees: list[str], after: list[str]
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page", assignees=[BOB])

    response = await assign(client, task, assignees=assignees)

    assert response.status_code == 200, response.text
    assert response.json()["assignees"] == after


@pytest.mark.parametrize(
    "members",
    [
        pytest.param([MMAUDET, ALICE], id="no member joined with it"),
        pytest.param(
            [MMAUDET, ALICE, BOB, TasksMember(tasks_id("bob's former account"), BOB.email)],
            id="two members joined with it",
        ),
    ],
)
async def test_an_email_that_names_no_member_once_lists_those_to_choose_from(
    client: AsyncClient, boundary: FakeBoundary, members: list[TasksMember]
) -> None:
    task = boundary.tasks.task(website(boundary, *members), "Fix the login page")

    response = await assign(client, task, assignees=["alice@twake.test", "bob@twake.test"])

    assert response.status_code == 409
    problem = response.json()
    assert problem["code"] == "assignee_not_member"
    assert "bob@twake.test" in problem["detail"]
    assert problem["members"] == ["alice@twake.test", "mmaudet@twake.test"]
    assert boundary.tasks.writes == []


async def test_an_email_counts_once_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page")

    response = await assign(client, task, assignees=["Alice@Twake.test", "alice@twake.test"])

    assert response.status_code == 200, response.text
    assert boundary.tasks.writes == [
        ("PUT", f"/api/boards/{board.id}/tasks/{task.id}/assignees", {"userIds": [ALICE.user_id]})
    ]


async def test_assignees_that_do_not_change_are_not_written(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Whatever their order: Tasks would tell nobody, and the task stays as it is
    task = boundary.tasks.task(website(boundary), "Fix the login page", assignees=[ALICE, BOB])

    response = await assign(client, task, assignees=["bob@twake.test", "alice@twake.test"])

    assert response.status_code == 200, response.text
    assert response.json()["assignees"] == ["alice@twake.test", "bob@twake.test"]
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no assignees"),
        pytest.param({"assignees": None}, id="null"),
        pytest.param({"assignees": "alice@twake.test"}, id="not a list"),
        pytest.param({"assignees": ["Alice"]}, id="not an email"),
        pytest.param(
            {"assignees": [f"member{number}@twake.test" for number in range(51)]},
            id="more than 50",
        ),
        pytest.param(
            {"assignees": ["alice@twake.test"], "notify": False}, id="a field it does not take"
        ),
    ],
)
async def test_an_invalid_assignment_is_refused_before_anything_is_written(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")

    response = await assign(client, task, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.writes == []


async def test_a_task_off_the_board_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks assigns an archived task too, or one in the trash, which the board hides
    board = website(boundary)
    archived = boundary.tasks.task(board, "Former task", hidden=True)
    unknown = TasksTask(tasks_id("unknown"), board.id, 2, "Unknown")

    for task in (archived, unknown):
        response = await assign(client, task, assignees=["alice@twake.test"])

        assert response.status_code == 404
        assert response.json()["code"] == "task_not_found"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Assigner la tâche WEB-1 « Fix the login page » du tableau « Website » :\n"
            "Désormais assignée à <mmaudet@twake.test>\n"
            "Plus assignée à <bob@twake.test>\n"
            "Toujours assignée à <alice@twake.test>\n"
            "Tasks prévient chaque nouveau responsable et ceux qui suivent la tâche, dans Tasks"
            " et par mail.",
        ),
        (
            "en",
            "Assign the task WEB-1 “Fix the login page” on the board “Website”:\n"
            "Assigned now to <mmaudet@twake.test>\n"
            "No longer assigned to <bob@twake.test>\n"
            "Still assigned to <alice@twake.test>\n"
            "Tasks tells each new assignee and the people who follow the task, in Tasks and by"
            " email.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_who_the_task_would_go_to_and_assigns_nobody(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page", assignees=[ALICE, BOB])

    response = await assign(
        client,
        task,
        asking_preview(language),
        assignees=["alice@twake.test", "mmaudet@twake.test"],
    )

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.tasks.writes == []
    assert task.assignees == [ALICE, BOB]


@pytest.mark.parametrize(
    ("assignees", "told"),
    [
        pytest.param(
            [],
            "No longer assigned to <alice@twake.test>, <bob@twake.test>\n"
            "Tasks tells the people who follow the task, in Tasks and by email.",
            id="nobody",
        ),
        pytest.param(
            ["bob@twake.test", "alice@twake.test"],
            "The task WEB-1 “Fix the login page” on the board “Website” is assigned so already:"
            " nothing changes.",
            id="the same members",
        ),
    ],
)
async def test_a_preview_says_who_is_no_longer_assigned_or_that_nothing_changes(
    client: AsyncClient, boundary: FakeBoundary, assignees: list[str], told: str
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page", assignees=[ALICE, BOB])

    summary, _ = preview_of(await assign(client, task, asking_preview("en"), assignees=assignees))

    assert summary.endswith(told)


async def test_the_owner_who_allowed_what_they_were_shown_assigns_the_task(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")
    _, digest = preview_of(
        await assign(client, task, asking_preview("fr"), assignees=["alice@twake.test"])
    )

    response = await assign(client, task, allowed_after(digest), assignees=["alice@twake.test"])

    assert response.status_code == 200, response.text
    assert task.assignees == [ALICE]


async def test_a_task_assigned_otherwise_since_the_preview_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")
    _, digest = preview_of(
        await assign(client, task, asking_preview("fr"), assignees=["alice@twake.test"])
    )
    # A member assigns it to Bob before the owner says yes: Alice would take his place
    task.assignees = [BOB]

    response = await assign(client, task, allowed_after(digest), assignees=["alice@twake.test"])

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.tasks.writes == []
    assert task.assignees == [BOB]
