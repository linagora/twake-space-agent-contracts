from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET
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


async def complete(client: AsyncClient, task: TasksTask) -> Response:
    return await client.post(
        f"/contracts/v1/tasks/boards/{task.board}/tasks/{task.id}/complete", headers=AS_MMAUDET
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
