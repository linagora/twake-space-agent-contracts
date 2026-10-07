from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")


def arrange_release(boundary: FakeBoundary) -> None:
    """Tasks about a release, on a board of the user and on one they are not a member of."""
    website = boundary.tasks.board("Website", "WEB", MMAUDET, ALICE)
    budget = boundary.tasks.board("Budget", "BUD", ALICE)
    boundary.tasks.task(website, "Write the release notes", assignees=[MMAUDET])
    boundary.tasks.task(website, "Fix the login page", description="Blocks the release.")
    boundary.tasks.task(website, "Release the beta", state="completed")
    boundary.tasks.task(website, "Translate the release notes", state="canceled")
    boundary.tasks.task(budget, "Budget of the release", assignees=[ALICE])


async def search(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get("/contracts/v1/tasks/search", params=params, headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_search_finds_the_open_tasks_of_the_users_boards(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    arrange_release(boundary)

    answer = await search(client, q="release")

    assert [(task["key"], task["state"]) for task in answer["tasks"]] == [
        ("WEB-1", "open"),
        ("WEB-2", "open"),
    ]
    assert answer["tasks"][0]["untrusted"]["title"] == "Write the release notes"
    assert answer["truncated"] is False
    assert boundary.tasks.requests == [("/api/search", {"q": "release"})]


async def test_closed_tasks_are_found_when_asked(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    arrange_release(boundary)

    answer = await search(client, q="release", include_closed="true")

    assert [(task["key"], task["state"]) for task in answer["tasks"]] == [
        ("WEB-1", "open"),
        ("WEB-2", "open"),
        ("WEB-3", "completed"),
        ("WEB-4", "canceled"),
    ]


async def test_a_key_finds_its_task(client: AsyncClient, boundary: FakeBoundary) -> None:
    arrange_release(boundary)

    answer = await search(client, q="WEB-2")

    assert [task["untrusted"]["title"] for task in answer["tasks"]] == ["Fix the login page"]


async def test_a_task_found_by_its_description_comes_as_any_other(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Since 0.2.10, Tasks quotes the words of the description around those found: read_task gives
    # the description, under untrusted, and a search no part of it
    arrange_release(boundary)

    answer = await search(client, q="blocks")

    assert [task["key"] for task in answer["tasks"]] == ["WEB-2"]
    assert set(answer["tasks"][0]) == {
        "board_id",
        "task_id",
        "key",
        "parent_id",
        "section_id",
        "state",
        "priority",
        "due_date",
        "due_time",
        "due_zone",
        "deadline",
        "assignees",
        "assigned_to_me",
        "untrusted",
    }
    assert set(answer["tasks"][0]["untrusted"]) == {"title", "board_name", "labels"}


async def test_a_search_tells_only_that_a_task_without_assignees_is_not_the_users(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks does not say who the user is among the assignees of the boards it searches
    website = boundary.tasks.board("Website", "WEB", MMAUDET, ALICE)
    boundary.tasks.task(website, "Write the release notes", assignees=[MMAUDET])
    boundary.tasks.task(website, "Plan the release party")

    answer = await search(client, q="release")

    assert [(task["key"], task["assigned_to_me"]) for task in answer["tasks"]] == [
        ("WEB-1", None),
        ("WEB-2", False),
    ]


@pytest.mark.parametrize(
    ("matching", "limit", "found", "truncated"),
    [
        pytest.param(5, "3", 3, True, id="more than the limit"),
        pytest.param(5, "5", 5, False, id="all within the limit"),
        pytest.param(60, "50", 50, True, id="as many as Tasks gives"),
    ],
)
async def test_the_results_say_when_they_hold_less_than_all(
    client: AsyncClient,
    boundary: FakeBoundary,
    matching: int,
    limit: str,
    found: int,
    truncated: bool,
) -> None:
    website = boundary.tasks.board("Website", "WEB", MMAUDET)
    for number in range(matching):
        boundary.tasks.task(website, f"Release step {number}")

    answer = await search(client, q="release", limit=limit)

    assert len(answer["tasks"]) == found
    assert answer["truncated"] is truncated


async def test_twenty_results_by_default(client: AsyncClient, boundary: FakeBoundary) -> None:
    website = boundary.tasks.board("Website", "WEB", MMAUDET)
    for number in range(25):
        boundary.tasks.task(website, f"Release step {number}")

    answer = await search(client, q="release")

    assert len(answer["tasks"]) == 20


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({}, id="no words"),
        pytest.param({"q": "   "}, id="blank words"),
        pytest.param({"q": "x" * 201}, id="more than 200 characters"),
        pytest.param({"q": "release", "limit": "0"}, id="no result"),
        pytest.param({"q": "release", "limit": "51"}, id="more than 50 results"),
    ],
)
async def test_a_search_out_of_bounds_is_invalid(
    client: AsyncClient, boundary: FakeBoundary, params: dict[str, str]
) -> None:
    response = await client.get("/contracts/v1/tasks/search", params=params, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.requests == []
