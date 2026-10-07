import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, TasksBoard, TasksTask, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
TO_DO, DONE, SHIPPED = (tasks_id(name) for name in ("to do", "done", "shipped"))


def inbox(boundary: FakeBoundary) -> TasksBoard:
    return boundary.tasks.board("Inbox", "INBOX", tasks_member("mmaudet", "admin"), inbox=True)


def website(boundary: FakeBoundary, *sections: dict[str, str]) -> TasksBoard:
    return boundary.tasks.board(
        "Website",
        "WEB",
        MMAUDET,
        sections=list(sections)
        or [
            {"id": TO_DO, "name": "To do", "category": "unstarted"},
            {"id": DONE, "name": "Done", "category": "completed"},
            {"id": SHIPPED, "name": "Shipped", "category": "completed"},
        ],
    )


async def complete(
    client: AsyncClient, task: TasksTask, headers: dict[str, str] | None = None
) -> Response:
    return await client.post(
        f"/contracts/v1/tasks/boards/{task.board}/tasks/{task.id}/complete",
        headers=AS_MMAUDET | (headers or {}),
    )


async def test_a_task_outside_sections_is_completed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = inbox(boundary)
    task = boundary.tasks.task(board, "Call the plumber")

    response = await complete(client, task)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["key"], answer["state"], answer["next_due_date"]) == (
        "INBOX-1",
        "completed",
        None,
    )
    assert boundary.tasks.writes == [
        ("POST", f"/api/boards/{board.id}/tasks/{task.id}/complete", {"state": "completed"})
    ]


async def test_a_task_in_a_section_moves_to_the_first_completed_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # As the Tasks web app completes it: Tasks completes only tasks outside sections
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)

    response = await complete(client, task)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["state"], answer["section_id"], answer["untrusted"]["section_name"]) == (
        "completed",
        DONE,
        "Done",
    )
    assert boundary.tasks.writes == [
        ("POST", f"/api/boards/{board.id}/tasks/{task.id}/move", {"sectionId": DONE})
    ]


async def test_a_board_without_a_completed_section_is_refused(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary, {"id": TO_DO, "name": "To do", "category": "unstarted"})
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)

    response = await complete(client, task)

    assert response.status_code == 409
    assert response.json()["code"] == "no_completed_section"
    assert boundary.tasks.writes == []


async def test_a_recurring_task_says_it_moved_to_its_next_due_date(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Completed for its due date, it stays open: the answer says so, lest the agent try again
    task = boundary.tasks.task(
        inbox(boundary),
        "Weekly report",
        due_date="2026-10-09",
        recurrence={"every": 1, "unit": "weeks", "fromCompletion": False},
    )

    response = await complete(client, task)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert (answer["state"], answer["recurring"], answer["next_due_date"]) == (
        "open",
        True,
        "2026-10-16",
    )
    assert answer["due_date"] == "2026-10-16"


async def test_a_completed_task_is_left_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(inbox(boundary), "Call the plumber", state="completed")

    response = await complete(client, task)

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "completed"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Terminer la tâche WEB-1 « Fix the login page » du tableau « Website » : elle passe"
            " dans la section « Done »\n"
            "Ses 2 sous-tâches ouvertes sont terminées avec elle.\n"
            "Tasks prévient ceux qui suivent la tâche, dans Tasks et par mail.",
        ),
        (
            "en",
            "Complete the task WEB-1 “Fix the login page” on the board “Website”: it moves to the"
            " section “Done”\n"
            "Its 2 open subtasks are completed with it.\n"
            "Tasks tells the people who follow the task, in Tasks and by email.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_completing_would_do_and_completes_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)
    subtask = boundary.tasks.task(board, "Reproduce it", parent_id=task.id)
    boundary.tasks.task(board, "Write a test", parent_id=subtask.id)
    boundary.tasks.task(board, "Ask the users", parent_id=task.id, state="completed")

    told, _ = preview_of(await complete(client, task, asking_preview(language)))

    assert told == summary
    assert boundary.tasks.writes == []
    assert (task.state, task.section_id) == ("open", TO_DO)


async def test_a_preview_says_a_recurring_task_stays_open(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(
        inbox(boundary),
        "Weekly report",
        due_date="2026-10-09",
        recurrence={"every": 1, "unit": "weeks", "fromCompletion": False},
    )

    told, _ = preview_of(await complete(client, task, asking_preview("fr")))

    assert told == (
        "Terminer la tâche INBOX-1 « Weekly report » du tableau « Inbox » pour son échéance du"
        " vendredi 9 octobre 2026 : elle se répète, et reste ouverte pour la suivante\n"
        "Tasks prévient ceux qui suivent la tâche, dans Tasks et par mail."
    )
    assert task.due_date == "2026-10-09"


async def test_a_preview_says_a_completed_task_stays_as_it_is(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(inbox(boundary), "Call the plumber", state="completed")

    told, _ = preview_of(await complete(client, task, asking_preview("en")))

    assert told == (
        "The task INBOX-1 “Call the plumber” on the board “Inbox” is completed already: nothing"
        " changes."
    )


async def test_a_preview_refuses_what_completing_would_refuse(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary, {"id": TO_DO, "name": "To do", "category": "unstarted"})
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)

    response = await complete(client, task, asking_preview("fr"))

    assert response.status_code == 409
    assert response.json()["code"] == "no_completed_section"
    assert boundary.tasks.writes == []


async def test_the_owner_who_allowed_what_they_were_shown_completes_the_task(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page", section_id=TO_DO)
    _, digest = preview_of(await complete(client, task, asking_preview("fr")))

    response = await complete(client, task, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert (task.state, task.section_id) == ("completed", DONE)


async def test_a_task_moved_since_the_preview_is_not_completed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)
    _, digest = preview_of(await complete(client, task, asking_preview("fr")))
    # A member ships it before the owner says yes
    task.section_id, task.state = SHIPPED, "completed"

    response = await complete(client, task, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.tasks.writes == []


async def test_subtasks_the_board_lists_otherwise_since_the_preview_change_nothing(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)
    boundary.tasks.task(board, "Reproduce it", parent_id=task.id)
    boundary.tasks.task(board, "Write a test", parent_id=task.id)
    _, digest = preview_of(await complete(client, task, asking_preview("fr")))
    # Tasks lists the same tasks in another order
    boundary.tasks.tasks = dict(reversed(boundary.tasks.tasks.items()))

    response = await complete(client, task, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert (task.state, task.section_id) == ("completed", DONE)
