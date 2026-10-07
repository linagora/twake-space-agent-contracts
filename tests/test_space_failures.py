"""What every contract of Twake Space answers when Space refuses the user's token, does not answer,
or answers in a form the contracts do not know."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import FakeBoundary, SpacePost, space_id, space_person

MMAUDET = space_person("mmaudet")
SPACE = f"/contracts/v1/space/spaces/{space_id('Design')}"
ITEM = f"{SPACE}/feed/items/{space_id('Hello')}"
THUMBS_UP = {"key": "\N{THUMBS UP SIGN}"}
# Each operation's method, path, query and body
OPERATIONS = [
    pytest.param("GET", "/contracts/v1/space/spaces", {}, None, id="list_spaces"),
    pytest.param("GET", SPACE, {}, None, id="read_space"),
    pytest.param(
        "GET", "/contracts/v1/space/people", {"q": "martin"}, None, id="search_organization_people"
    ),
    pytest.param("GET", f"{SPACE}/feed", {}, None, id="list_feed_items"),
    pytest.param("GET", ITEM, {}, None, id="read_feed_item"),
    pytest.param("POST", f"{ITEM}/reactions", {}, THUMBS_UP, id="add_feed_reaction"),
    pytest.param("POST", f"{ITEM}/reactions/remove", {}, THUMBS_UP, id="remove_feed_reaction"),
]
PARAMETERS = ("method", "path", "params", "body")


@pytest.fixture(autouse=True)
def design(boundary: FakeBoundary) -> None:
    boundary.space.space("Design", {MMAUDET: "admin"})


def hello(boundary: FakeBoundary) -> SpacePost:
    """The post ITEM names, in the feed of the space SPACE names."""
    room = boundary.space.spaces[space_id("Design")]
    post = boundary.space.post(room, MMAUDET, "Hello", time="2026-10-06T08:30:00.000Z")
    post.id = space_id("Hello")
    boundary.space.posts = {post.id: post}
    return post


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_token_space_refuses_is_named_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    boundary.space.refused_tokens = True

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "space_refused"
    assert response.json()["detail"].startswith("Space answered 401 to GET /")


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_user_space_knows_in_no_organization_is_refused(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # Space serves the members of an organization: it answers 403 to every call of a token whose
    # user LemonLDAP-NG gives no org_id
    boundary.space.organizations[MMAUDET.email] = None

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "space_refused"
    assert "no organization" in response.json()["detail"]
    assert "org_id" in response.json()["detail"]


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

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

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

    response = await client.request(method, path, params=params, json=body, headers=AS_MMAUDET)

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


@pytest.mark.parametrize(
    ("method", "path", "body", "reacted"),
    [
        pytest.param("PUT", "/reactions", THUMBS_UP, False, id="add_feed_reaction"),
        # With the reaction to take back
        pytest.param("DELETE", "/reactions/remove", THUMBS_UP, True, id="remove_feed_reaction"),
    ],
)
async def test_a_write_space_fails_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    body: Any,
    reacted: bool,
) -> None:
    post = hello(boundary)
    if reacted:
        boundary.space.react(post, MMAUDET, THUMBS_UP["key"])
    boundary.space.failing = {method: (500, "internal")}

    response = await client.post(f"{ITEM}{path}", json=body, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "space_unavailable"
    written, at, _ = boundary.space.writes[0]
    assert response.json()["detail"].startswith(f"Space answered 500 to {method} /spaces/")
    assert (written, post.id in at) == (method, True)
