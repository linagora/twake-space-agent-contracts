from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, TasksBoard, TasksTask, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")
WEEKLY = {"every": 1, "unit": "weeks", "fromCompletion": False}


def website(boundary: FakeBoundary) -> TasksBoard:
    return boundary.tasks.board("Website", "WEB", MMAUDET, ALICE)


async def update(client: AsyncClient, task: TasksTask, **body: Any) -> Response:
    return await client.patch(
        f"/contracts/v1/tasks/boards/{task.board}/tasks/{task.id}", json=body, headers=AS_MMAUDET
    )


async def test_only_the_fields_given_change(client: AsyncClient, boundary: FakeBoundary) -> None:
    board = website(boundary)
    task = boundary.tasks.task(
        board,
        "Send the budget",
        priority=3,
        due_date="2026-10-09",
        deadline="2026-10-16",
        assignees=[MMAUDET],
    )

    response = await update(
        client,
        task,
        title="Send the Q4 budget",
        priority=1,
        due_time="17:00",
        due_zone="Europe/Paris",
    )

    assert response.status_code == 200, response.text
    answer = response.json()
    assert {name: answer[name] for name in ("priority", "due_date", "due_time", "due_zone")} == {
        "priority": 1,
        "due_date": "2026-10-09",
        "due_time": "17:00",
        "due_zone": "Europe/Paris",
    }
    assert (answer["deadline"], answer["assigned_to_me"]) == ("2026-10-16", True)
    assert answer["untrusted"]["title"] == "Send the Q4 budget"
    assert boundary.tasks.writes == [
        (
            "PATCH",
            f"/api/boards/{board.id}/tasks/{task.id}",
            {
                "title": "Send the Q4 budget",
                "priority": 1,
                "dueTime": "17:00",
                "dueZone": "Europe/Paris",
            },
        )
    ]


async def test_clearing_the_due_date_clears_its_time_and_recurrence(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(
        board,
        "Weekly report",
        priority=2,
        due_date="2026-10-09",
        due_time="09:00",
        due_zone="Europe/Paris",
        recurrence=WEEKLY,
    )

    response = await update(client, task, due_date=None, priority=None)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert [
        answer[name] for name in ("priority", "due_date", "due_time", "due_zone", "recurring")
    ] == [None, None, None, None, False]
    assert boundary.tasks.writes == [
        ("PATCH", f"/api/boards/{board.id}/tasks/{task.id}", {"dueDate": None, "priority": None})
    ]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="nothing to change"),
        pytest.param({"title": None}, id="no title"),
        pytest.param({"title": " "}, id="a blank title"),
        pytest.param({"priority": 0}, id="a priority under 1"),
        pytest.param({"deadline": "next Friday"}, id="a deadline that is not a date"),
        # With a field it changes, so that only the field list refuses them
        pytest.param({"priority": 1, "description": "Start over"}, id="the description"),
        pytest.param(
            {"priority": 1, "section_id": tasks_id("done")}, id="a field it does not change"
        ),
        pytest.param({"due_time": "17:00"}, id="a time on a task without a due date"),
        pytest.param(
            {"due_date": "2026-10-09", "due_zone": "Europe/Paris"},
            id="a zone on a task without a due time",
        ),
        pytest.param(
            {"due_date": "2026-10-09", "due_time": "17:00", "due_zone": "Europe/Pari"},
            id="a zone no one knows",
        ),
    ],
)
async def test_an_invalid_change_is_refused_before_anything_is_written(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    task = boundary.tasks.task(website(boundary), "Send the budget")

    response = await update(client, task, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.writes == []


async def test_a_task_off_the_board_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    archived = boundary.tasks.task(board, "Former task", hidden=True)
    unknown = TasksTask(tasks_id("unknown"), board.id, 2, "Unknown")

    for task in (archived, unknown):
        response = await update(client, task, priority=1)

        assert response.status_code == 404
        assert response.json()["code"] == "task_not_found"
    assert boundary.tasks.writes == []
