"""The Twake Space backend (0.1.18), called with the API token of Space the user made for their
assistant: Space acts for the token, and shows it the spaces it reaches."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, Header

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import Problem, invalid_request

# The ids of spaces, people and items of a feed, as Space writes them: a pattern any OpenAPI
# validator checks
SPACE_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
API_TOKEN_PREFIX = "tws_"
"""What Space's API tokens start with, by which Space tells them from the tokens of a person's
session."""

Scope = Literal["space:read", "feed:read"]
"""What an API token of Space may do that the contracts need, by Space's names: read the spaces,
and read their feeds."""

AT_ONCE = 5
"""The most calls to Space a contract makes at a time, when it reads several spaces."""


async def for_each[Item, Result](
    items: Iterable[Item], call: Callable[[Item], Coroutine[Any, Any, Result]]
) -> list[Result]:
    """What the call gives for each item, in their order, made for AT_ONCE items at a time at
    most. The first call to fail stops the others, and raises what it raised."""
    turns = asyncio.Semaphore(AT_ONCE)

    async def take_turn(item: Item) -> Result:
        async with turns:
            return await call(item)

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(take_turn(item)) for item in items]
    except ExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return [task.result() for task in tasks]


def _unavailable(detail: str) -> Problem:
    return Problem(status=502, code="space_unavailable", title="Space unavailable", detail=detail)


def space_not_found(space_id: str) -> Problem:
    return Problem(
        status=404,
        code="space_not_found",
        title="Space not found",
        detail=f"The user is a member of no space {space_id} their API token of Space reaches: "
        "list_spaces gives the spaces it reaches.",
    )


def feed_item_not_found(space_id: str, item_id: str) -> Problem:
    return Problem(
        status=404,
        code="feed_item_not_found",
        title="Feed item not found",
        detail=f"The feed of space {space_id} has no item {item_id}.",
    )


@dataclass(frozen=True)
class SpaceOwner:
    """The user, with the API token of Space they made for their assistant, which the token broker
    holds for them, never stored here."""

    user: User
    token: str = field(repr=False)
    """Out of the owner's representation, so that no trace or log shows it."""


SpaceOwnerDependency = Callable[..., Awaitable[SpaceOwner]]


def space_owner_dependency(caller: CallerDependency) -> SpaceOwnerDependency:
    """The dependency that gives a Space route the user and their API token of Space, as APISIX
    attached them."""

    async def space_owner(
        user: Annotated[User, Depends(caller)],
        x_twake_space_token: Annotated[str | None, Header(include_in_schema=False)] = None,
    ) -> SpaceOwner:
        """The user's API token of Space, from the header the gateway sets with what the token
        broker gives, once it removed the one an agent sent.

        Left out of the OpenAPI document, as the user's token is.
        """
        token = (x_twake_space_token or "").strip()
        # Anything else, such as the token LemonLDAP-NG gave the broker, never reaches Space
        if not token.startswith(API_TOKEN_PREFIX):
            raise Problem(
                status=401,
                code="missing_space_token",
                title="Missing Space token",
                detail="The request must carry the user's API token of Space in "
                "X-Twake-Space-Token.",
            )
        return SpaceOwner(user, token)

    return space_owner


def _text(value: Any) -> str:
    """A text of Space's answer. Raises TypeError for any other value."""
    if not isinstance(value, str):
        raise TypeError(f"not a text: {type(value).__name__}")
    return value


def _optional_text(value: Any) -> str | None:
    """A text of Space's answer, or null. Raises TypeError for any other value."""
    return None if value is None else _text(value)


def _error_of(response: httpx.Response) -> str | None:
    """The code Space names its refusal with, in {"error": code}."""
    try:
        found = response.json()
    except ValueError:
        return None
    error = found.get("error") if isinstance(found, dict) else None
    return error if isinstance(error, str) else None


@dataclass(frozen=True)
class SpaceSummary:
    """A space the user is a member of, as Space lists it. What people wrote of it comes as they
    wrote it."""

    space_id: str
    name: str
    description: str
    role: str
    """The user's: viewer, editor or admin."""
    member_count: int


@dataclass(frozen=True)
class Member:
    """A member of a space: a person of the organization, by the user id Space knows them by,
    the LDAP entryUUID, and their role there."""

    user_id: str
    username: str
    email: str
    display_name: str | None
    role: str


@dataclass(frozen=True)
class Group:
    """A group of the organization linked to a space, whose people are members with its role."""

    group_id: str
    name: str
    role: str


@dataclass(frozen=True)
class SpaceDetail:
    """A space the user is a member of, as Space shows it to them."""

    space_id: str
    name: str
    description: str
    role: str
    """The user's: viewer, editor or admin."""
    created_at: datetime
    apps: tuple[str, ...]
    """The tabs the space shows, among chat, tasks, drive, mail and calendar."""
    members: tuple[Member, ...]
    """By username."""
    groups: tuple[Group, ...]
    resources: dict[str, str | None]
    """What each app linked to the space, by kind: project, matrix_space, mailbox, calendar and
    drive. None while the app still prepares it."""

    def user_id_of(self, email: str) -> str | None:
        """The user id of the one member who has that email, whatever its case, whom the contracts
        take for the user, as Space says of no member that they are; None if no member has it, or
        several do."""
        found = [member.user_id for member in self.members if member.email.lower() == email.lower()]
        return found[0] if len(found) == 1 else None


ActorKind = Literal["user", "token", "deleted_user"]
ItemKind = Literal["card", "post"]


@dataclass(frozen=True)
class Actor:
    """Who made the latest activity of a card, or wrote a post, as Space names them."""

    kind: ActorKind
    user_id: str | None
    """A user's id, as Space knows the members of the space; None for someone outside it, or for
    any other kind."""
    name: str | None
    """The name the space knows a user by, the name of a token; None for anyone else."""


@dataclass(frozen=True)
class Reaction:
    key: str
    user_ids: tuple[str, ...]
    """Who reacted so, in the order they did."""


@dataclass(frozen=True)
class FeedItem:
    """An item of a space's feed: a card, the latest activity of an app on one object, or a post
    of a member. What people wrote comes as they wrote it."""

    item_id: str
    kind: ItemKind
    category: str
    time: datetime
    updated_at: datetime
    by: Actor | None
    reactions: tuple[Reaction, ...]
    event_type: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    title: str | None = None
    container_kind: str | None = None
    container_id: str | None = None
    preview: str | None = None
    state: Any = None
    """What the app tells of the object, such as an event's times, as the app sent it."""
    body: str | None = None
    edited_at: datetime | None = None


def _actor(value: Any) -> Actor | None:
    """An actor as Space gives it. Raises KeyError, TypeError or ValueError for any other form."""
    if value is None:
        return None
    kind = _text(value["type"])
    if kind == "user":
        return Actor("user", _optional_text(value["id"]), _optional_text(value["name"]))
    if kind == "token":
        return Actor("token", None, _text(value["name"]))
    if kind == "deleted_user":
        return Actor("deleted_user", None, None)
    raise ValueError(f"an actor of an unknown type: {kind}")


def _time(value: Any) -> datetime:
    return datetime.fromisoformat(_text(value))


def _feed_item(value: Any) -> FeedItem:
    """An item as Space serializes it. Raises KeyError, TypeError or ValueError for any other
    form."""
    item_id = _text(value["id"])
    category = _text(value["category"])
    time, updated_at = _time(value["time"]), _time(value["updatedAt"])
    reactions = tuple(
        Reaction(_text(reaction["key"]), tuple(_text(user) for user in reaction["userIds"]))
        for reaction in value["reactions"]
    )
    kind = value["kind"]
    if kind == "post":
        edited = value["editedAt"]
        return FeedItem(
            item_id,
            "post",
            category,
            time,
            updated_at,
            by=_actor(value["author"]),
            reactions=reactions,
            body=_text(value["body"]),
            edited_at=None if edited is None else _time(edited),
        )
    if kind != "card":
        raise ValueError(f"an item of an unknown kind: {kind}")
    found = value["object"]
    container = found["container"]
    return FeedItem(
        item_id,
        "card",
        category,
        time,
        updated_at,
        by=_actor(value["actor"]),
        reactions=reactions,
        event_type=_text(value["type"]),
        object_type=_text(found["type"]),
        object_id=_text(found["id"]),
        title=_text(found["title"]),
        container_kind=None if container is None else _text(container["kind"]),
        container_id=None if container is None else _text(container["id"]),
        preview=_optional_text(value["preview"]),
        state=value["state"],
    )


def _member(item: Any) -> Member:
    """A member as Space gives them. Raises KeyError, TypeError or ValueError for any other
    form."""
    return Member(
        user_id=_text(item["id"]),
        username=_text(item["username"]),
        email=_text(item["email"]),
        display_name=_optional_text(item["displayName"]),
        role=_text(item["role"]),
    )


class TwakeSpace:
    """The Twake Space backend, called with the user's API token of Space, and its web app, where
    the contracts link the feeds."""

    def __init__(self, url: str, web_url: str, http: httpx.AsyncClient) -> None:
        self._url = url
        self._web_url = web_url
        self._http = http

    def feed_url(self, space_id: str) -> str:
        """The feed of the space in Space's web app, where the user posts and reacts."""
        return f"{self._web_url}/spaces/{space_id}/feed"

    async def _call(
        self, owner: SpaceOwner, method: str, path: str, *, scope: Scope, params: Any = None
    ) -> httpx.Response:
        """Space's answer, once Space answered and took the user's token with the scope the call
        needs."""
        try:
            response = await self._http.request(
                method,
                self._url + path,
                params=params,
                headers={"Authorization": f"Bearer {owner.token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise _unavailable(f"Space did not answer {method} {path}.") from error
        if response.status_code == 401:
            raise Problem(
                status=401,
                code="space_token_rejected",
                title="Space token rejected",
                detail=f"Space refused the user's API token of Space, answering 401 to {method}"
                f" {path}: the user revoked it, it expired, or their account left the"
                " organization.",
            )
        if response.status_code == 403 and _error_of(response) == "insufficient_scope":
            raise Problem(
                status=403,
                code="space_scope_missing",
                title="Space scope missing",
                detail=f"The user's API token of Space lacks {scope}, which {method} {path}"
                " needs: the user chose what it may do when they made it in Space.",
                extensions={"scope": scope},
            )
        return response

    def _json(self, response: httpx.Response, method: str, path: str) -> Any:
        """Space's JSON answer, if it says yes."""
        if not response.is_success:
            raise _unavailable(f"Space answered {response.status_code} to {method} {path}.")
        try:
            return response.json()
        except ValueError as error:
            raise _unavailable(f"Space did not answer {method} {path} in JSON.") from error

    async def _get(
        self,
        owner: SpaceOwner,
        path: str,
        params: dict[str, str | int] | None = None,
        *,
        scope: Scope,
        missing_ok: bool = False,
        invalid: Problem | None = None,
    ) -> Any:
        """Space's JSON answer; None when what is asked for is missing and missing_ok is set:
        Space answers 404 not_found for what does not exist and for what the token does not
        reach alike. A refused request raises `invalid` when given: Space checks a parameter
        the contract cannot."""
        response = await self._call(owner, "GET", path, scope=scope, params=params)
        if missing_ok and response.status_code == 404 and _error_of(response) == "not_found":
            return None
        if invalid is not None and response.status_code == 400:
            raise invalid
        return self._json(response, "GET", path)

    async def spaces(self, owner: SpaceOwner) -> list[SpaceSummary]:
        """The spaces the user is a member of, by name."""
        found = await self._get(owner, "/spaces", scope="space:read")
        try:
            return [
                SpaceSummary(
                    space_id=_text(space["id"]),
                    name=_text(space["name"]),
                    description=_text(space["description"]),
                    role=_text(space["role"]),
                    member_count=len(space["members"]),
                )
                for space in found["spaces"]
            ]
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the spaces in an unexpected form.") from error

    async def space(self, owner: SpaceOwner, space_id: str) -> SpaceDetail:
        """The space, if the user is a member of it: Space answers for any other as for an
        unknown one."""
        found = await self.found_space(owner, space_id)
        if found is None:
            raise space_not_found(space_id)
        return found

    async def found_space(self, owner: SpaceOwner, space_id: str) -> SpaceDetail | None:
        """The space; None if the user is not a member of it, or no longer is."""
        found = await self._get(owner, f"/spaces/{space_id}", scope="space:read", missing_ok=True)
        if found is None:
            return None
        try:
            return SpaceDetail(
                space_id=_text(found["id"]),
                name=_text(found["name"]),
                description=_text(found["description"]),
                role=_text(found["role"]),
                created_at=datetime.fromisoformat(_text(found["createdAt"])),
                apps=tuple(_text(app) for app in found["apps"]),
                members=tuple(_member(member) for member in found["members"]),
                groups=tuple(
                    Group(
                        group_id=_text(group["id"]),
                        name=_text(group["name"]),
                        role=_text(group["role"]),
                    )
                    for group in found["groups"]
                ),
                resources={
                    _text(resource["kind"]): _optional_text(resource["id"])
                    for resource in found["resources"]
                },
            )
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the space in an unexpected form.") from error

    async def feed(
        self,
        owner: SpaceOwner,
        space_id: str,
        *,
        category: str | None,
        limit: int,
        before: str | None,
    ) -> tuple[list[FeedItem], str | None]:
        """A page of the feed of the space, newest first, and the cursor of the next one; None
        after the last."""
        found = await self.found_feed(
            owner, space_id, category=category, limit=limit, before=before
        )
        if found is None:
            raise space_not_found(space_id)
        return found

    async def found_feed(
        self,
        owner: SpaceOwner,
        space_id: str,
        *,
        category: str | None,
        limit: int,
        before: str | None,
    ) -> tuple[list[FeedItem], str | None] | None:
        """A page of the feed of the space, as `feed` gives it; None if the user is not a member
        of the space, or no longer is."""
        params: dict[str, str | int] = {"limit": limit}
        if category is not None:
            params["category"] = category
        if before is not None:
            params["before"] = before
        found = await self._get(
            owner,
            f"/spaces/{space_id}/feed",
            params,
            scope="feed:read",
            missing_ok=True,
            invalid=invalid_request(
                "before: Space does not know this cursor: pass the next a list_feed_items answer"
                " gave."
            ),
        )
        if found is None:
            return None
        try:
            items = [_feed_item(item) for item in found["items"]]
            following = _optional_text(found["next"])
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the feed in an unexpected form.") from error
        return items, following

    async def item(self, owner: SpaceOwner, space_id: str, item_id: str) -> FeedItem | None:
        """The item of the feed of the space; None if the feed has no such item, or if the
        space is not the user's."""
        found = await self._get(
            owner, f"/spaces/{space_id}/feed/items/{item_id}", scope="feed:read", missing_ok=True
        )
        if found is None:
            return None
        try:
            return _feed_item(found)
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the item in an unexpected form.") from error
