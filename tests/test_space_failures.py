"""How every contract of Twake Space calls Space, with the owner's API token of Space, and what it
answers when that token is missing or refused, when Space does not answer, fails a write, or answers
in a form the contracts do not know."""

from typing import Any

import pytest
from httpx import AsyncClient

from tests.fakes import (
    MMAUDET_SPACE_TOKEN,
    FakeBoundary,
    SpaceToken,
    as_space_owner,
    space_person,
    space_uuid,
)

MMAUDET = space_person("mmaudet")
BOB = space_person("bob")
JEANNE = space_person("jmartin")
SPACE = f"/contracts/v1/space/spaces/{space_uuid('Design')}"
ITEM = f"{SPACE}/feed/items/{space_uuid('Hello')}"
FEEDS = "/contracts/v1/space/feed"
DESIGN = {"space_id": space_uuid("Design")}
PEOPLE = "/contracts/v1/space/people"
MEMBERS = f"{SPACE}/members"
MEMBER = f"{MEMBERS}/{BOB.user_id}"
# Each write's method, path, query and body
WRITES = [
    pytest.param(
        "POST", MEMBERS, {}, {"usernames": ["jmartin"], "role": "editor"}, id="add_space_members"
    ),
    pytest.param("PATCH", MEMBER, {}, {"role": "editor"}, id="update_space_member"),
    pytest.param("DELETE", MEMBER, {}, None, id="remove_space_member"),
]
# Each operation's method, path, query and body
OPERATIONS = [
    pytest.param("GET", "/contracts/v1/space/spaces", {}, None, id="list_spaces"),
    pytest.param("GET", SPACE, {}, None, id="read_space"),
    pytest.param("GET", PEOPLE, {"q": "mm"}, None, id="search_space_people"),
    pytest.param("GET", FEEDS, DESIGN, None, id="list_feed_items"),
    pytest.param("GET", FEEDS, {}, None, id="list_feed_items, all spaces"),
    pytest.param("GET", ITEM, {}, None, id="read_feed_item"),
    pytest.param("POST", "/contracts/v1/space/spaces", {}, {"name": "Launch"}, id="create_space"),
    pytest.param("PATCH", SPACE, {}, {"name": "Brand design"}, id="rename_space"),
    *WRITES,
]
PARAMETERS = ("method", "path", "params", "body")
MISSING_SPACE_TOKEN = {
    "type": "urn:twake:problem:missing_space_token",
    "title": "Missing Space token",
    "status": 401,
    "detail": "The request must carry the user's API token of Space in X-Twake-Space-Token.",
    "code": "missing_space_token",
}


@pytest.fixture(autouse=True)
def design(boundary: FakeBoundary) -> None:
    """The space SPACE names, with Bob, whom MEMBER names, among its members, and the post ITEM
    names in its feed; Jeanne, whom add_space_members adds, is in the organization."""
    room = boundary.space.space("Design", {MMAUDET: "admin", BOB: "viewer"})
    boundary.space.people[JEANNE.user_id] = JEANNE
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

    assert response.is_success, response.text
    sent = [
        request.headers.get("authorization")
        for request in boundary.requests
        if request.url.host == "space.test"
    ]
    assert sent
    assert set(sent) == {f"Bearer {MMAUDET_SPACE_TOKEN}"}


async def test_the_owner_is_the_member_their_own_token_names_whoever_the_space_token_acts_as(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Space tells no member that they are the account of the token, and a token of the
    # organization has none: the owner is the member who has the email of the token LemonLDAP-NG
    # gave the broker
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(None, role="viewer")
    boundary.space.react(boundary.space.posts[space_uuid("Hello")], MMAUDET, "👍")

    space = await client.get(SPACE, headers=as_space_owner())
    item = await client.get(ITEM, headers=as_space_owner())

    assert space.status_code == 200, space.text
    assert [(member["user_id"], member["you"]) for member in space.json()["members"]] == [
        (BOB.user_id, False),
        (MMAUDET.user_id, True),
    ]
    assert item.status_code == 200, item.text
    assert item.json()["by"]["you"] is True
    assert item.json()["reactions"][0]["mine"] is True


@pytest.mark.parametrize("token", [None, " "], ids=["missing", "blank"])
@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_request_without_the_space_token_is_refused(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
    token: str | None,
) -> None:
    headers = as_space_owner()
    if token is None:
        del headers["X-Twake-Space-Token"]
    else:
        headers["X-Twake-Space-Token"] = token

    response = await client.request(method, path, params=params, json=body, headers=headers)

    assert response.status_code == 401
    assert response.json() == MISSING_SPACE_TOKEN
    assert boundary.space.requests == []


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_the_users_own_token_never_reaches_space(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # Whatever sits in the header but an API token of Space, such as the token LemonLDAP-NG gave
    # the broker, stays with the contracts
    headers = as_space_owner()
    headers["X-Twake-Space-Token"] = headers["Authorization"].removeprefix("Bearer ")

    response = await client.request(method, path, params=params, json=body, headers=headers)

    assert response.status_code == 401
    assert response.json() == MISSING_SPACE_TOKEN
    assert boundary.space.requests == []


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_token_space_refuses_is_named_so(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # Space knows the token no longer: the owner revoked it, it expired, or their account left the
    # organization
    boundary.space.tokens.clear()

    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 401
    assert response.json()["code"] == "space_token_rejected"


@pytest.mark.parametrize(
    ("path", "params", "held", "missing"),
    [
        pytest.param(
            "/contracts/v1/space/spaces", {}, {"feed:read"}, "space:read", id="list_spaces"
        ),
        pytest.param(SPACE, {}, {"feed:read"}, "space:read", id="read_space"),
        pytest.param(PEOPLE, {"q": "mm"}, {"feed:read"}, "space:read", id="search_space_people"),
        pytest.param(FEEDS, DESIGN, {"space:read"}, "feed:read", id="list_feed_items"),
        pytest.param(FEEDS, {}, {"space:read"}, "feed:read", id="list_feed_items, all spaces"),
        pytest.param(ITEM, {}, {"space:read"}, "feed:read", id="read_feed_item"),
        # A feed is read once the spaces are, to tell which member the user is
        pytest.param(FEEDS, DESIGN, {"feed:read"}, "space:read", id="list_feed_items, space"),
        pytest.param(
            FEEDS, {}, {"feed:read"}, "space:read", id="list_feed_items, all spaces, spaces"
        ),
        pytest.param(ITEM, {}, {"feed:read"}, "space:read", id="read_feed_item, space"),
    ],
)
async def test_a_token_without_a_scope_the_contract_needs_is_refused_with_its_name(
    client: AsyncClient,
    boundary: FakeBoundary,
    path: str,
    params: dict[str, str],
    held: set[str],
    missing: str,
) -> None:
    # The owner chose what the token may do when they made it in Space
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(MMAUDET.user_id, frozenset(held))

    response = await client.get(path, params=params, headers=as_space_owner())

    assert response.status_code == 403
    assert (response.json()["code"], response.json()["scope"]) == ("space_scope_missing", missing)


@pytest.mark.parametrize(
    ("held", "missing"),
    [
        pytest.param({"space:read", "feed:read"}, "members:write", id="members:write"),
        # A member changes once the space is read, to tell whether the user is one of its admins
        pytest.param({"members:write"}, "space:read", id="space:read"),
    ],
)
@pytest.mark.parametrize(PARAMETERS, WRITES)
async def test_a_token_without_a_scope_a_write_needs_changes_no_member(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
    held: set[str],
    missing: str,
) -> None:
    # The owner chose whether the token changes members when they made it in Space
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(MMAUDET.user_id, frozenset(held))

    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 403
    assert (response.json()["code"], response.json()["scope"]) == ("space_scope_missing", missing)
    assert boundary.space.writes == []


async def test_a_space_out_of_the_reach_of_the_token_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The owner chose the spaces the token reaches when they made it in Space
    roadmap = boundary.space.space("Roadmap", {MMAUDET: "viewer"})
    boundary.space.tokens[MMAUDET_SPACE_TOKEN] = SpaceToken(
        MMAUDET.user_id, space_ids=frozenset({roadmap.id})
    )

    listed = await client.get("/contracts/v1/space/spaces", headers=as_space_owner())
    responses = [
        await client.get(path, params=params, headers=as_space_owner())
        for path, params in ((SPACE, {}), (FEEDS, DESIGN), (ITEM, {}))
    ]

    assert [space["space_id"] for space in listed.json()["spaces"]] == [roadmap.id]
    assert [response.status_code for response in responses] == [404, 404, 404]
    assert {response.json()["code"] for response in responses} == {"space_not_found"}
    assert responses[0].json()["detail"] == (
        f"The user is a member of no space {space_uuid('Design')} their API token of Space "
        "reaches: list_spaces gives the spaces it reaches."
    )


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


@pytest.mark.parametrize(
    "failure", [(500, "internal_error"), (400, "invalid_request")], ids=["failed", "refused"]
)
@pytest.mark.parametrize(PARAMETERS, WRITES)
async def test_a_write_space_fails_or_refuses_for_a_reason_it_does_not_name_is_a_bad_gateway(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
    failure: tuple[int, str],
) -> None:
    # The contracts check what Space checks of a write before they send it: a write Space refuses
    # as invalid tells they no longer agree
    boundary.space.failing = {method: failure}

    response = await client.request(
        method, path, params=params, json=body, headers=as_space_owner()
    )

    assert response.status_code == 502
    assert response.json()["code"] == "space_unavailable"
    assert response.json()["detail"].startswith(f"Space answered {failure[0]} to {method} /spaces/")


@pytest.mark.parametrize(PARAMETERS, OPERATIONS)
async def test_a_space_contract_needs_the_users_token(
    client: AsyncClient,
    boundary: FakeBoundary,
    method: str,
    path: str,
    params: dict[str, str],
    body: Any,
) -> None:
    # The API token of Space alone names no one to the contracts
    headers = as_space_owner()
    del headers["Authorization"]

    response = await client.request(method, path, params=params, json=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"
    assert boundary.space.requests == []
