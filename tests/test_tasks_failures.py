from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, tasks_id, tasks_member

BOARD = tasks_id("Website")
TASK = f"/contracts/v1/tasks/boards/{BOARD}/tasks/{tasks_id('WEB-1')}"
# Each operation's method, path, query and body
OPERATIONS = [
    pytest.param("POST", "/contracts/v1/tasks/boards/open", {}, None, id="open_boards"),
    pytest.param("GET", "/contracts/v1/tasks/projects", {}, None, id="list_projects"),
    pytest.param(
        "POST",
        "/contracts/v1/tasks/projects",
        {},
        {"name": "Q4 launch", "key_prefix": "LAUNCH"},
        id="create_project",
    ),
    pytest.param(
        "GET", "/contracts/v1/tasks/mine", {"zone": "Europe/Paris"}, None, id="list_my_tasks"
    ),
    pytest.param(
        "GET",
        "/contracts/v1/tasks/mine",
        {"zone": "Europe/Paris", "due": "today"},
        None,
        id="list_my_tasks due",
    ),
    pytest.param("GET", "/contracts/v1/tasks/search", {"q": "release"}, None, id="search_tasks"),
    pytest.param("GET", TASK, {}, None, id="read_task"),
    pytest.param(
        "POST",
        f"/contracts/v1/tasks/boards/{BOARD}/tasks",
        {},
        {"title": "Plan the launch"},
        id="create_task",
    ),
    pytest.param("PATCH", TASK, {}, {"priority": 1}, id="update_task"),
    pytest.param("POST", f"{TASK}/complete", {}, None, id="complete_task"),
]
WRITES = [
    each
    for each in OPERATIONS
    if each.id in ("create_project", "create_task", "update_task", "complete_task")
]
PARAMETERS = ("method", "path", "params", "body")


@pytest.fixture(autouse=True)
def website(boundary: FakeBoundary) -> None:
    board = boundary.tasks.board("Website", "WEB", tasks_member("mmaudet"))
    boundary.tasks.task(board, "Write the release notes")


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_token_tasks_refuses_is_named_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    boundary.tasks.refused_tokens = True

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_refused_token"


@pytest.mark.parametrize(
    ("failure", "told"), [("down", "Tasks answered 503"), ("unreachable", "Tasks did not answer")]
)
@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_an_unavailable_tasks_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
    failure: str,
    told: str,
) -> None:
    setattr(boundary.tasks, failure, True)

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_unavailable"
    assert response.json()["detail"].startswith(told)


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_an_answer_of_an_unknown_form_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    boundary.tasks.unexpected = True

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_unavailable"


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_tasks_are_called_only_with_the_user_token(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    response = await client.request(method, path, params=params, json=body)

    assert response.status_code == 401
    assert boundary.tasks.requests == []


@pytest.mark.parametrize(PARAMETERS, [each for each in OPERATIONS if each.id != "open_boards"])
async def test_only_open_boards_opens_tasks(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # Tasks lists boards only as opening its web app does, which sets up the Inbox and accepts
    # invitations: a read never acts for the user, and a write does only what it says
    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code < 500, response.text
    asked = [(request.method, request.url.path) for request in boundary.requests]
    assert ("GET", "/api/boards") not in asked


@pytest.mark.parametrize(PARAMETERS, WRITES)
async def test_a_write_tasks_fails_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # Tasks shows the board, then fails the write itself: a new task was not created
    boundary.tasks.failing = {method: 503}

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_unavailable"
    written, at, _ = boundary.tasks.writes[0]
    assert response.json()["detail"] == f"Tasks answered 503 to {written} {at}."
