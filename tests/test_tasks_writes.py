"""What each write of Tasks refuses before writing: a board the user cannot write on."""

from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, TasksMember, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")
WRITES = [
    pytest.param("POST", "", {"title": "Plan the launch"}, id="create_task"),
    pytest.param("PATCH", "/{task}", {"priority": 1}, id="update_task"),
    pytest.param("POST", "/{task}/complete", None, id="complete_task"),
    pytest.param("DELETE", "/{task}", None, id="delete_task"),
    pytest.param("PUT", "/{task}/assignees", {"assignees": ["alice@twake.test"]}, id="assign_task"),
]


def write_on(boundary: FakeBoundary, *members: TasksMember, **more: Any) -> tuple[str, str]:
    """A board and one of its tasks, by their ids."""
    board = boundary.tasks.board("Website", "WEB", *members, **more)
    return board.id, boundary.tasks.task(board, "Write the release notes").id


async def write(
    client: AsyncClient, method: str, path: str, body: Any, board_id: str, task_id: str
) -> Response:
    return await client.request(
        method,
        f"/contracts/v1/tasks/boards/{board_id}/tasks" + path.format(task=task_id),
        json=body,
        headers=AS_MMAUDET,
    )


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_a_viewer_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary, method: str, path: str, body: Any
) -> None:
    ids = write_on(boundary, tasks_member("mmaudet", "viewer"), ALICE)

    response = await write(client, method, path, body, *ids)

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden_role"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_an_archived_board_is_refused(
    client: AsyncClient, boundary: FakeBoundary, method: str, path: str, body: Any
) -> None:
    ids = write_on(boundary, MMAUDET, ALICE, archived=True)

    response = await write(client, method, path, body, *ids)

    assert response.status_code == 409
    assert response.json()["code"] == "board_archived"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_a_board_the_user_is_not_a_member_of_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary, method: str, path: str, body: Any
) -> None:
    others = write_on(boundary, ALICE)
    unknown = (tasks_id("no board"), tasks_id("unknown"))

    for ids in (others, unknown):
        response = await write(client, method, path, body, *ids)

        assert response.status_code == 404
        assert response.json()["code"] == "board_not_found"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
@pytest.mark.parametrize(
    "members",
    [
        pytest.param(
            [TasksMember(MMAUDET.user_id, "michel@twake.test"), ALICE], id="under another email"
        ),
        pytest.param(
            [MMAUDET, TasksMember(tasks_id("former account"), MMAUDET.email), ALICE], id="twice"
        ),
    ],
)
async def test_a_board_that_does_not_name_the_user_once_by_email_is_refused(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    body: Any,
    members: list[TasksMember],
) -> None:
    ids = write_on(boundary, *members)

    response = await write(client, method, path, body, *ids)

    assert response.status_code == 409
    assert response.json()["code"] == "owner_not_member"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
async def test_ids_that_are_not_uuids_are_invalid(
    client: AsyncClient, boundary: FakeBoundary, method: str, path: str, body: Any
) -> None:
    response = await write(client, method, path, body, "WEB", "WEB-1")

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.requests == []


@pytest.mark.parametrize(
    ("method", "path", "body", "gone"),
    [
        pytest.param("POST", "", {"title": "Plan the launch"}, "board_not_found", id="create_task"),
        pytest.param("PATCH", "/{task}", {"priority": 1}, "task_not_found", id="update_task"),
        pytest.param("POST", "/{task}/complete", None, "task_not_found", id="complete_task"),
        pytest.param("DELETE", "/{task}", None, "task_not_found", id="delete_task"),
        pytest.param(
            "PUT",
            "/{task}/assignees",
            {"assignees": ["alice@twake.test"]},
            "task_not_found",
            id="assign_task",
        ),
    ],
)
@pytest.mark.parametrize(
    ("status", "code"),
    [
        pytest.param(403, "forbidden_role", id="now a viewer"),
        pytest.param(409, "board_archived", id="now archived"),
        pytest.param(404, None, id="now gone"),
    ],
)
async def test_a_board_that_changes_before_the_write_is_named_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    body: Any,
    gone: str,
    status: int,
    code: str | None,
) -> None:
    # Tasks showed the board, then refuses the write: it changed in between
    ids = write_on(boundary, MMAUDET, ALICE)
    boundary.tasks.failing = {method: status}

    response = await write(client, method, path, body, *ids)

    assert response.status_code == status
    assert response.json()["code"] == (code or gone)
