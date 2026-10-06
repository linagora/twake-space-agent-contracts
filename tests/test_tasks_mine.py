from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import TASKS_TODAY, FakeBoundary, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")
PARIS = {"zone": "Europe/Paris"}


def arrange_week(boundary: FakeBoundary) -> None:
    """The user's week, today being 2026-10-07: tasks of their own and of others, in their Inbox,
    on a shared board and on a space's board."""
    inbox = boundary.tasks.board("Inbox", "INBOX", tasks_member("mmaudet", "admin"), inbox=True)
    website = boundary.tasks.board("Website", "WEB", MMAUDET, ALICE)
    space = boundary.tasks.board("Roadmap", "MAP", MMAUDET, ALICE, managed=True)
    boundary.tasks.task(website, "Fix the login page", due_date="2026-10-05", assignees=[MMAUDET])
    boundary.tasks.task(
        website, "Write the release notes", due_date=TASKS_TODAY, assignees=[MMAUDET]
    )
    boundary.tasks.task(
        website, "Plan the launch", due_date="2026-10-10", assignees=[MMAUDET, ALICE]
    )
    boundary.tasks.task(website, "Book the venue", due_date="2026-10-20", assignees=[MMAUDET])
    boundary.tasks.task(website, "Update the FAQ", assignees=[MMAUDET])
    boundary.tasks.task(website, "Review the design", due_date=TASKS_TODAY, assignees=[ALICE])
    boundary.tasks.task(
        website, "Fix the footer", due_date="2026-10-06", assignees=[MMAUDET], state="completed"
    )
    # Unassigned: the user's own in their Inbox, nobody's yet on a space's board
    boundary.tasks.task(inbox, "Call the plumber", due_date=TASKS_TODAY)
    boundary.tasks.task(space, "Choose the next topics", due_date=TASKS_TODAY)


async def my_tasks(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get(
        "/contracts/v1/tasks/mine", params=PARIS | params, headers=AS_MMAUDET
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def keys(answer: dict[str, Any]) -> list[str]:
    return [task["key"] for task in answer["tasks"]]


async def test_overdue_tasks_are_those_due_before_today(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    arrange_week(boundary)

    answer = await my_tasks(client, due="overdue")

    assert keys(answer) == ["WEB-1"]
    assert boundary.tasks.requests == [("/api/agenda", {"zone": "Europe/Paris", "days": "1"})]


async def test_tasks_due_today_count_the_users_own_unassigned_ones(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    arrange_week(boundary)

    answer = await my_tasks(client, due="today")

    assert [(task["key"], task["assigned_to_me"]) for task in answer["tasks"]] == [
        ("INBOX-1", False),
        ("WEB-2", True),
    ]


async def test_upcoming_tasks_are_due_from_today_within_the_days_asked(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    arrange_week(boundary)

    answer = await my_tasks(client, due="upcoming", days="7")

    assert keys(answer) == ["INBOX-1", "WEB-2", "WEB-3"]
    assert boundary.tasks.requests == [("/api/agenda", {"zone": "Europe/Paris", "days": "7"})]


async def test_all_is_every_open_task_assigned_to_the_user(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    arrange_week(boundary)

    answer = await my_tasks(client, due="all")

    assert keys(answer) == ["WEB-1", "WEB-2", "WEB-3", "WEB-4", "WEB-5"]
    assert boundary.tasks.requests == [("/api/my-tasks", {})]


async def test_a_task_comes_with_what_members_wrote_apart(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    website = boundary.tasks.board(
        "Website", "WEB", MMAUDET, ALICE, labels={"label-urgent": "Urgent"}
    )
    task = boundary.tasks.task(
        website,
        "Write the release notes",
        assignees=[MMAUDET, ALICE],
        priority=1,
        due_date=TASKS_TODAY,
        due_time="14:00",
        due_zone="Europe/Paris",
        deadline="2026-10-09",
        labels=["label-urgent"],
    )

    answer = await my_tasks(client, due="all")

    assert answer == {
        "tasks": [
            {
                "board_id": website.id,
                "task_id": task.id,
                "key": "WEB-1",
                "parent_id": None,
                "state": "open",
                "priority": 1,
                "due_date": TASKS_TODAY,
                "due_time": "14:00",
                "due_zone": "Europe/Paris",
                "deadline": "2026-10-09",
                "assignees": ["alice@twake.test", "mmaudet@twake.test"],
                "assigned_to_me": True,
                "untrusted": {
                    "title": "Write the release notes",
                    "board_name": "Website",
                    "labels": ["Urgent"],
                },
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize(("limit", "truncated"), [("2", True), ("5", False)])
async def test_the_list_says_when_it_holds_less_than_all(
    client: AsyncClient, boundary: FakeBoundary, limit: str, truncated: bool
) -> None:
    arrange_week(boundary)

    answer = await my_tasks(client, due="all", limit=limit)

    assert keys(answer) == ["WEB-1", "WEB-2", "WEB-3", "WEB-4", "WEB-5"][: int(limit)]
    assert answer["truncated"] is truncated


async def test_the_list_holds_twenty_tasks_by_default(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    website = boundary.tasks.board("Website", "WEB", MMAUDET)
    for number in range(25):
        boundary.tasks.task(website, f"Task {number}", assignees=[MMAUDET])

    answer = await my_tasks(client)

    assert len(answer["tasks"]) == 20
    assert answer["truncated"] is True


async def test_a_time_zone_tasks_does_not_know_is_an_invalid_request(
    client: AsyncClient,
) -> None:
    response = await client.get(
        "/contracts/v1/tasks/mine",
        params={"due": "today", "zone": "Mars/Olympus_Mons"},
        headers=AS_MMAUDET,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert "Mars/Olympus_Mons" in response.json()["detail"]


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"due": "today"}, id="without a zone"),
        pytest.param(PARIS | {"due": "tomorrow"}, id="an unknown due"),
        pytest.param({"zone": "Europe/Paris?days=60"}, id="a zone that is not a name"),
        pytest.param(PARIS | {"days": "0"}, id="no day"),
        pytest.param(PARIS | {"days": "32"}, id="more than 31 days"),
        pytest.param(PARIS | {"limit": "0"}, id="no task"),
        pytest.param(PARIS | {"limit": "101"}, id="more than 100 tasks"),
    ],
)
async def test_a_request_out_of_bounds_is_invalid(
    client: AsyncClient, boundary: FakeBoundary, params: dict[str, str]
) -> None:
    response = await client.get("/contracts/v1/tasks/mine", params=params, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.requests == []
