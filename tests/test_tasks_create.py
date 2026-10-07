from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, TasksBoard, TasksMember, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")
BACKLOG, TO_DO, IN_PROGRESS, DONE = (
    tasks_id(name) for name in ("backlog", "to do", "in progress", "done")
)
# A board's sections as Tasks creates them, with a backlog put first
SECTIONS = [
    {"id": BACKLOG, "name": "Backlog", "category": "backlog"},
    {"id": TO_DO, "name": "To do", "category": "unstarted"},
    {"id": IN_PROGRESS, "name": "In progress", "category": "started"},
    {"id": DONE, "name": "Done", "category": "completed"},
]


def website(boundary: FakeBoundary, *members: TasksMember, **more: Any) -> TasksBoard:
    return boundary.tasks.board(
        "Website", "WEB", *(members or (MMAUDET, ALICE)), **({"sections": SECTIONS} | more)
    )


async def create(
    client: AsyncClient, board: TasksBoard, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.post(
        f"/contracts/v1/tasks/boards/{board.id}/tasks",
        json=body,
        headers=AS_MMAUDET | (headers or {}),
    )


async def test_a_task_goes_to_the_first_unstarted_section(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)

    response = await create(client, board, title="Send the Q4 budget")

    assert response.status_code == 201, response.text
    assert response.json() == {
        "board_id": board.id,
        "task_id": tasks_id("WEB-1"),
        "key": "WEB-1",
        "parent_id": None,
        "section_id": TO_DO,
        "state": "open",
        "priority": None,
        "due_date": None,
        "due_time": None,
        "due_zone": None,
        "deadline": None,
        "assignees": [],
        "assigned_to_me": False,
        "section_category": "unstarted",
        "recurring": False,
        "untrusted": {
            "title": "Send the Q4 budget",
            "board_name": "Website",
            "labels": [],
            "section_name": "To do",
        },
    }
    assert boundary.tasks.writes == [
        (
            "POST",
            f"/api/boards/{board.id}/tasks",
            {"sectionId": TO_DO, "title": "Send the Q4 budget"},
        )
    ]


async def test_its_priority_and_due_date_are_set_once_it_exists(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)

    response = await create(
        client,
        board,
        title="Send the Q4 budget",
        priority=2,
        due_date="2026-10-09",
        due_time="17:00",
        due_zone="Europe/Paris",
    )

    assert response.status_code == 201, response.text
    answer = response.json()
    assert [answer[name] for name in ("priority", "due_date", "due_time", "due_zone")] == [
        2,
        "2026-10-09",
        "17:00",
        "Europe/Paris",
    ]
    assert boundary.tasks.writes == [
        (
            "POST",
            f"/api/boards/{board.id}/tasks",
            {"sectionId": TO_DO, "title": "Send the Q4 budget"},
        ),
        (
            "PATCH",
            f"/api/boards/{board.id}/tasks/{answer['task_id']}",
            {"priority": 2, "dueDate": "2026-10-09", "dueTime": "17:00", "dueZone": "Europe/Paris"},
        ),
    ]


async def test_a_board_without_sections_takes_it_outside_them(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    inbox = boundary.tasks.board("Inbox", "INBOX", tasks_member("mmaudet", "admin"), inbox=True)

    response = await create(client, inbox, title="Call the plumber")

    assert response.status_code == 201, response.text
    assert (response.json()["key"], response.json()["section_id"]) == ("INBOX-1", None)
    assert boundary.tasks.writes == [
        ("POST", f"/api/boards/{inbox.id}/tasks", {"sectionId": None, "title": "Call the plumber"})
    ]


async def test_the_section_given_is_taken(client: AsyncClient, boundary: FakeBoundary) -> None:
    board = website(boundary)

    response = await create(client, board, title="Send the Q4 budget", section_id=IN_PROGRESS)

    assert response.status_code == 201, response.text
    assert response.json()["section_category"] == "started"
    assert boundary.tasks.writes[0][2] == {"sectionId": IN_PROGRESS, "title": "Send the Q4 budget"}


async def test_a_board_without_an_unstarted_section_lists_those_to_choose_from(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary, sections=[SECTIONS[0], SECTIONS[3]])

    response = await create(client, board, title="Send the Q4 budget")

    assert response.status_code == 409
    assert response.json()["code"] == "section_required"
    assert response.json()["sections"] == [
        {"section_id": BACKLOG, "category": "backlog", "untrusted": {"name": "Backlog"}},
        {"section_id": DONE, "category": "completed", "untrusted": {"name": "Done"}},
    ]
    assert boundary.tasks.writes == []


async def test_a_subtask_is_created_under_its_parent(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    parent = boundary.tasks.task(board, "Prepare the Q4 budget", section_id=TO_DO)

    response = await create(client, board, title="Collect the figures", parent_id=parent.id)

    assert response.status_code == 201, response.text
    assert (response.json()["parent_id"], response.json()["section_id"]) == (parent.id, None)
    assert boundary.tasks.writes == [
        (
            "POST",
            f"/api/boards/{board.id}/tasks",
            {"parentId": parent.id, "title": "Collect the figures"},
        )
    ]


async def test_a_parent_off_the_board_answers_like_an_unknown_task(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    archived = boundary.tasks.task(board, "Former task", hidden=True)

    for parent_id in (archived.id, tasks_id("unknown")):
        response = await create(client, board, title="Collect the figures", parent_id=parent_id)

        assert response.status_code == 404
        assert response.json()["code"] == "task_not_found"
    assert boundary.tasks.writes == []


async def test_a_subtask_nested_too_deep_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks nests subtasks four deep at most, and tells it when it refuses: nothing is created
    board = website(boundary)
    parent = None
    for level in range(4):
        parent = boundary.tasks.task(
            board, f"Level {level}", parent_id=parent.id if parent else None
        )
    assert parent is not None

    response = await create(client, board, title="Level 4", parent_id=parent.id)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "too_deep" in response.json()["detail"]
    assert len(boundary.tasks.tasks) == 4


async def test_a_task_created_then_left_unfinished_says_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks has no idempotency key: told of the task created, the agent does not create another
    board = website(boundary)
    boundary.tasks.failing = {"PATCH": 503}

    response = await create(client, board, title="Send the Q4 budget", priority=2)

    assert response.status_code == 502
    problem = response.json()
    assert problem["code"] == "task_created_partially"
    assert (problem["board_id"], problem["task_id"], problem["key"]) == (
        board.id,
        tasks_id("WEB-1"),
        "WEB-1",
    )
    assert [method for method, _, _ in boundary.tasks.writes] == ["POST", "PATCH"]


async def test_a_task_created_in_full_is_not_called_partial_when_unread(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Created and given its priority, the task is whole: only reading it again failed
    board = website(boundary)
    boundary.tasks.unreadable_after_write = True

    response = await create(client, board, title="Send the Q4 budget", priority=2)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_unavailable"
    assert boundary.tasks.tasks[tasks_id("WEB-1")].priority == 2


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"title": "  "}, id="a blank title"),
        pytest.param({"title": "t" * 501}, id="a title over 500 characters"),
        pytest.param({"title": "Plan", "priority": 5}, id="a priority over 4"),
        pytest.param({"title": "Plan", "due_date": "09/10/2026"}, id="a date in another format"),
        pytest.param(
            {"title": "Plan", "due_date": "2026-10-09", "due_time": "5 pm"},
            id="a time in another format",
        ),
        pytest.param({"title": "Plan", "due_time": "17:00"}, id="a time without a date"),
        pytest.param(
            {"title": "Plan", "due_date": "2026-10-09", "due_zone": "Europe/Paris"},
            id="a zone without a time",
        ),
        pytest.param(
            {
                "title": "Plan",
                "due_date": "2026-10-09",
                "due_time": "17:00",
                "due_zone": "Europe/Pari",
            },
            id="a zone no one knows",
        ),
        pytest.param(
            {"title": "Plan", "section_id": TO_DO, "parent_id": tasks_id("WEB-1")},
            id="a section and a parent",
        ),
        pytest.param(
            {"title": "Plan", "section_id": tasks_id("elsewhere")}, id="a section of no board"
        ),
        pytest.param({"title": "Plan", "description": "Details"}, id="a description"),
        pytest.param(
            {"title": "Plan", "assignees": ["alice@twake.test"]}, id="a field it does not take"
        ),
    ],
)
async def test_an_invalid_task_is_refused_before_anything_is_created(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    response = await create(client, website(boundary), **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Créer la tâche « Send the Q4 budget » dans le tableau « Website », section « To do »\n"
            "Priorité : 2\n"
            "Échéance : vendredi 9 octobre 2026 à 17 h (Europe/Paris)",
        ),
        (
            "en",
            "Create the task “Send the Q4 budget” on the board “Website”, in the section “To do”\n"
            "Priority: 2\n"
            "Due: Friday 9 October 2026 at 17:00 (Europe/Paris)",
        ),
    ],
)
async def test_a_preview_tells_the_owner_where_the_task_would_go_and_creates_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    board = website(boundary)

    response = await create(
        client,
        board,
        asking_preview(language),
        title="Send the Q4 budget",
        priority=2,
        due_date="2026-10-09",
        due_time="17:00",
        due_zone="Europe/Paris",
    )

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.tasks.writes == []
    assert boundary.tasks.tasks == {}


async def test_a_preview_names_the_task_a_subtask_would_go_under(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    parent = boundary.tasks.task(board, "Launch the website", section_id=TO_DO)

    response = await create(
        client, board, asking_preview("fr"), title="Write the release notes", parent_id=parent.id
    )

    told, _ = preview_of(response)
    assert told == (
        "Créer la sous-tâche « Write the release notes » de la tâche WEB-1 « Launch the"
        " website », dans le tableau « Website »"
    )
    assert boundary.tasks.writes == []


async def test_a_preview_refuses_what_creating_would_refuse(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary, sections=[SECTIONS[0], SECTIONS[3]])

    response = await create(client, board, asking_preview("fr"), title="Send the Q4 budget")

    assert response.status_code == 409
    assert response.json()["code"] == "section_required"
    assert boundary.tasks.writes == []


async def test_the_owner_who_allowed_what_they_were_shown_gets_the_task(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    _, digest = preview_of(
        await create(client, board, asking_preview("fr"), title="Send the Q4 budget")
    )

    response = await create(client, board, allowed_after(digest), title="Send the Q4 budget")

    assert response.status_code == 201, response.text
    assert response.json()["section_id"] == TO_DO


async def test_a_task_whose_section_changed_since_the_preview_is_not_created(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    _, digest = preview_of(
        await create(client, board, asking_preview("fr"), title="Send the Q4 budget")
    )
    # A member renames the section the task would go to before the owner says yes
    board.sections = [SECTIONS[0], SECTIONS[1] | {"name": "Someday"}, *SECTIONS[2:]]

    response = await create(client, board, allowed_after(digest), title="Send the Q4 budget")

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.tasks.writes == []
