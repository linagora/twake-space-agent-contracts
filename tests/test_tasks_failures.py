import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, tasks_id, tasks_member

BOARD = tasks_id("Website")
OPERATIONS = [
    pytest.param("/contracts/v1/tasks/mine", {"zone": "Europe/Paris"}, id="list_my_tasks"),
    pytest.param(
        "/contracts/v1/tasks/mine", {"zone": "Europe/Paris", "due": "today"}, id="list_my_tasks due"
    ),
    pytest.param("/contracts/v1/tasks/search", {"q": "release"}, id="search_tasks"),
    pytest.param(
        f"/contracts/v1/tasks/boards/{BOARD}/tasks/{tasks_id('WEB-1')}", {}, id="read_task"
    ),
]


@pytest.fixture(autouse=True)
def website(boundary: FakeBoundary) -> None:
    board = boundary.tasks.board("Website", "WEB", tasks_member("mmaudet"))
    boundary.tasks.task(board, "Write the release notes")


@pytest.mark.parametrize(("path", "params"), OPERATIONS)
async def test_a_token_tasks_refuses_is_named_so(
    client: AsyncClient, boundary: FakeBoundary, path: str, params: dict[str, str]
) -> None:
    boundary.tasks.refused_tokens = True

    response = await client.get(path, params=params, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_refused_token"


@pytest.mark.parametrize(
    ("failure", "told"), [("down", "Tasks answered 503"), ("unreachable", "Tasks did not answer")]
)
@pytest.mark.parametrize(("path", "params"), OPERATIONS)
async def test_an_unavailable_tasks_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    path: str,
    params: dict[str, str],
    failure: str,
    told: str,
) -> None:
    setattr(boundary.tasks, failure, True)

    response = await client.get(path, params=params, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_unavailable"
    assert response.json()["detail"].startswith(told)


@pytest.mark.parametrize(("path", "params"), OPERATIONS)
async def test_an_answer_of_an_unknown_form_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary, path: str, params: dict[str, str]
) -> None:
    boundary.tasks.unexpected = True

    response = await client.get(path, params=params, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "tasks_unavailable"


@pytest.mark.parametrize(("path", "params"), OPERATIONS)
async def test_tasks_are_read_only_with_the_user_token(
    client: AsyncClient, boundary: FakeBoundary, path: str, params: dict[str, str]
) -> None:
    response = await client.get(path, params=params)

    assert response.status_code == 401
    assert boundary.tasks.requests == []
