import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, TasksBoard, TasksTask, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")
TO_DO = tasks_id("to do")


def website(boundary: FakeBoundary) -> TasksBoard:
    return boundary.tasks.board(
        "Website",
        "WEB",
        MMAUDET,
        ALICE,
        sections=[{"id": TO_DO, "name": "To do", "category": "unstarted"}],
    )


async def delete(
    client: AsyncClient, task: TasksTask, headers: dict[str, str] | None = None
) -> Response:
    return await client.delete(
        f"/contracts/v1/tasks/boards/{task.board}/tasks/{task.id}",
        headers=AS_MMAUDET | (headers or {}),
    )


async def test_a_task_goes_to_the_trash_with_its_subtasks(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(
        board, "Fix the login page", section_id=TO_DO, priority=2, assignees=[ALICE]
    )
    subtask = boundary.tasks.task(board, "Reproduce it", parent_id=task.id)
    under = boundary.tasks.task(board, "Write a test", parent_id=subtask.id, state="completed")
    other = boundary.tasks.task(board, "Update the FAQ", section_id=TO_DO)

    response = await delete(client, task)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "board_id": board.id,
        "task_id": task.id,
        "key": "WEB-1",
        "parent_id": None,
        "section_id": TO_DO,
        "state": "open",
        "priority": 2,
        "due_date": None,
        "due_time": None,
        "due_zone": None,
        "deadline": None,
        "assignees": ["alice@twake.test"],
        "assigned_to_me": False,
        "section_category": "unstarted",
        "recurring": False,
        "deleted_subtasks": 2,
        "untrusted": {
            "title": "Fix the login page",
            "board_name": "Website",
            "labels": [],
            "section_name": "To do",
        },
    }
    assert boundary.tasks.writes == [("DELETE", f"/api/boards/{board.id}/tasks/{task.id}", None)]
    assert [each.hidden for each in (task, subtask, under, other)] == [True, True, True, False]


async def test_a_task_off_the_board_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Archived, or in the trash already: the board does not show it
    board = website(boundary)
    archived = boundary.tasks.task(board, "Former task", hidden=True)
    unknown = TasksTask(tasks_id("unknown"), board.id, 2, "Unknown")

    for task in (archived, unknown):
        response = await delete(client, task)

        assert response.status_code == 404
        assert response.json()["code"] == "task_not_found"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Supprimer la tâche WEB-1 « Fix the login page » du tableau « Website » : elle passe"
            " dans la corbeille du tableau, d'où un éditeur ou un administrateur du tableau peut la"
            " restaurer pendant 30 jours, avant que Tasks la supprime définitivement\n"
            "Ses 2 sous-tâches partent avec elle.",
        ),
        (
            "en",
            "Delete the task WEB-1 “Fix the login page” from the board “Website”: it goes to the"
            " board's trash, where an editor or an admin of the board can restore it for 30 days,"
            " before Tasks deletes it for good\n"
            "Its 2 subtasks go with it.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_deleting_would_do_and_deletes_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)
    subtask = boundary.tasks.task(board, "Reproduce it", parent_id=task.id)
    boundary.tasks.task(board, "Write a test", parent_id=subtask.id, state="completed")

    told, _ = preview_of(await delete(client, task, asking_preview(language)))

    assert told == summary
    assert boundary.tasks.writes == []
    assert task.hidden is False


@pytest.mark.parametrize(("subtasks", "told"), [(0, None), (1, "Sa sous-tâche part avec elle.")])
async def test_a_preview_counts_the_subtasks_that_go_with_the_task(
    client: AsyncClient, boundary: FakeBoundary, subtasks: int, told: str | None
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)
    for number in range(subtasks):
        boundary.tasks.task(board, f"Step {number}", parent_id=task.id)

    summary, _ = preview_of(await delete(client, task, asking_preview("fr")))

    assert summary.splitlines()[1:] == ([told] if told else [])


async def test_the_owner_who_allowed_what_they_were_shown_deletes_the_task(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page", section_id=TO_DO)
    _, digest = preview_of(await delete(client, task, asking_preview("fr")))

    response = await delete(client, task, allowed_after(digest))

    assert response.status_code == 200, response.text
    assert task.hidden is True


async def test_a_subtask_added_since_the_preview_keeps_the_task(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page", section_id=TO_DO)
    _, digest = preview_of(await delete(client, task, asking_preview("fr")))
    # A member adds a subtask, which would go to the trash with it, before the owner says yes
    boundary.tasks.task(board, "Reproduce it", parent_id=task.id)

    response = await delete(client, task, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.tasks.writes == []
    assert task.hidden is False
