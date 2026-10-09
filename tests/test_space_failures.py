"""How every contract of Twake Space calls Space, with the owner's API token of Space, and what it
answers when that token is missing or refused, when Space does not answer, or answers in a form the
contracts do not know."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.fakes import (
    MMAUDET_SPACE_TOKEN,
    FakeBoundary,
    as_space_owner,
    space_person,
    space_uuid,
)

MMAUDET = space_person("mmaudet")
SPACE = f"/contracts/v1/space/spaces/{space_uuid('Design')}"
ITEM = f"{SPACE}/feed/items/{space_uuid('Hello')}"
# Each operation's method, path, query and body
OPERATIONS = [
    pytest.param("GET", "/contracts/v1/space/spaces", {}, None, id="list_spaces"),
    pytest.param("GET", SPACE, {}, None, id="read_space"),
    pytest.param("GET", f"{SPACE}/feed", {}, None, id="list_feed_items"),
    pytest.param("GET", ITEM, {}, None, id="read_feed_item"),
]
PARAMETERS = ("method", "path", "params", "body")


@pytest.fixture(autouse=True)
def design(boundary: FakeBoundary) -> None:
    """The space SPACE names, with the post ITEM names in its feed."""
    room = boundary.space.space("Design", {MMAUDET: "admin"})
    post = boundary.space.post(room, MMAUDET, "Hello", time="2026-10-06T08:30:00.000Z")
    post.id = space_uuid("Hello")
    boundary.space.posts = {post.id: post}


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_space_is_called_with_the_owners_space_token_alone(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # The token LemonLDAP-NG gave the broker names the owner to the contracts, and never reaches
    # Space: the API token of Space the owner made for their assistant does
    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 200, response.text
    sent = [
        request.headers.get("authorization")
        for request in boundary.requests
        if request.url.host == "space.test"
    ]
    assert sent
    assert set(sent) == {f"Bearer {MMAUDET_SPACE_TOKEN}"}


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_token_space_refuses_is_named_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    boundary.space.tokens.clear()

    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 502
    assert response.json()["code"] == "space_refused"
    assert response.json()["detail"].startswith("Space answered 401 to GET /")


@pytest.mark.parametrize(
    ("failure", "told"), [("down", "Space answered 503"), ("unreachable", "Space did not answer")]
)
@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_an_unavailable_space_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
    failure: str,
    told: str,
) -> None:
    setattr(boundary.space, failure, True)

    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 502
    assert response.json()["code"] == "space_unavailable"
    assert response.json()["detail"].startswith(told)


@pytest.mark.parametrize("unexpected", [{"items": []}, "<html>Bad gateway</html>"])
@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_an_answer_of_an_unknown_form_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
    unexpected: object,
) -> None:
    boundary.space.unexpected = unexpected

    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 502
    assert response.json()["code"] == "space_unavailable"


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_space_is_called_only_with_the_user_token(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    response = await client.request(method, path, params=params, json=body)

    assert response.status_code == 401
    assert boundary.space.requests == []
