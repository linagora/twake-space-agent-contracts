"""The Twake Space backend (0.1.9), called as the user with their own token: Space acts for the uuid
of the token's user in their org_id, and shows them the spaces they are a member of."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote

import httpx

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem, invalid_request

# The ids of spaces, people and items of a feed, as Space writes them: a pattern any OpenAPI
# validator checks
SPACE_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def _space_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


def _unavailable(detail: str) -> Problem:
    return _space_problem("space_unavailable", "Space unavailable", detail)


def space_not_found(space_id: str) -> Problem:
    return Problem(
        status=404,
        code="space_not_found",
        title="Space not found",
        detail=f"The user is a member of no space {space_id}: list_spaces gives theirs.",
    )


def forbidden_role(space_id: str) -> Problem:
    return Problem(
        status=403,
        code="forbidden_role",
        title="Role forbids writing",
        detail=f"The user is a viewer of space {space_id}: they read its feed and react, but do"
        " not post.",
    )


def not_author(space_id: str, item_id: str) -> Problem:
    return Problem(
        status=403,
        code="not_author",
        title="Not the author",
        detail=f"The user did not write post {item_id} of space {space_id}: only its author"
        " edits or deletes it.",
    )


def not_a_post(space_id: str, item_id: str) -> Problem:
    return Problem(
        status=409,
        code="not_a_post",
        title="Not a post",
        detail=f"Item {item_id} of the feed of space {space_id} is a card, which shows what an"
        " app did: only posts are edited or deleted.",
    )


def not_space_admin(space_id: str) -> Problem:
    return Problem(
        status=403,
        code="not_space_admin",
        title="Not an admin of the space",
        detail=f"The user is not an admin of space {space_id}: only its admins add, change and"
        " remove its members, which the user asks one of them to do.",
    )


def person_not_found(usernames: list[str]) -> Problem:
    return Problem(
        status=404,
        code="person_not_found",
        title="Person not found",
        detail="The user's organization has no active person whose username is"
        f" {', '.join(usernames)}: search_organization_people finds its people.",
        extensions={"usernames": usernames},
    )


def member_exists(space_id: str, members: "list[Member]") -> Problem:
    return Problem(
        status=409,
        code="member_exists",
        title="Member exists",
        detail=f"Some of these people are members of space {space_id} already, with another"
        " role, which update_space_member changes: nobody was added. members names them, when"
        " the contract tells.",
        extensions={
            "members": [{"user_id": member.user_id, "role": member.role} for member in members]
        },
    )


def member_not_found(space_id: str, user_id: str) -> Problem:
    return Problem(
        status=404,
        code="member_not_found",
        title="Member not found",
        detail=f"Space {space_id} has no member {user_id}: read_space gives its members.",
    )


def last_admin(space_id: str) -> Problem:
    return Problem(
        status=409,
        code="last_admin",
        title="Last admin",
        detail=f"Space {space_id} would be left without an admin, which it keeps at least one"
        " of: make another member an admin first.",
    )


def feed_item_not_found(space_id: str, item_id: str) -> Problem:
    return Problem(
        status=404,
        code="feed_item_not_found",
        title="Feed item not found",
        detail=f"The feed of space {space_id} has no item {item_id}.",
    )


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

    def member(self, user_id: str) -> Member | None:
        """The member of that user id; None if the space has none."""
        return next((member for member in self.members if member.user_id == user_id), None)

    @property
    def audience(self) -> list[str]:
        """Who reads what is written in the space, by user id: its members, sorted, so that the
        same members make the same digest."""
        return sorted(member.user_id for member in self.members)


@dataclass(frozen=True)
class Person:
    """A person of the organization, as its directory gives them."""

    username: str
    email: str
    display_name: str | None


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

    def reacted(self, user_id: str | None, key: str) -> bool:
        """Whether the member of that user id reacted to the item so; False for None, whom the
        contract cannot tell."""
        return any(
            reaction.key == key and user_id in reaction.user_ids for reaction in self.reactions
        )


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
    """The Twake Space backend, called as the user with their own token."""

    def __init__(self, url: str, http: httpx.AsyncClient) -> None:
        self._url = url
        self._http = http

    async def _call(
        self, user: User, method: str, path: str, *, params: Any = None, body: Any = None
    ) -> httpx.Response:
        """Space's answer, once Space answered and took the user's token."""
        try:
            response = await self._http.request(
                method,
                self._url + path,
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {user.token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as error:
            raise _unavailable(f"Space did not answer {method} {path}.") from error
        if response.status_code == 401:
            raise _space_problem(
                "space_refused",
                "Space refused the user's token",
                f"Space answered 401 to {method} {path}.",
            )
        # What Space answers every call of a user it knows in no organization
        if response.status_code == 403 and _error_of(response) == "forbidden":
            raise _space_problem(
                "space_refused",
                "Space refused the user's token",
                f"Space answered 403 forbidden to {method} {path}: it knows the user in no"
                " organization, as when their token gives it no org_id.",
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
        user: User,
        path: str,
        params: dict[str, str | int] | None = None,
        *,
        missing_ok: bool = False,
        invalid: Problem | None = None,
    ) -> Any:
        """Space's JSON answer; None when what is asked for is missing and missing_ok is set:
        Space answers 404 not_found for what does not exist and for what the user does not
        reach alike. A refused request raises `invalid` when given: Space checks a parameter
        the contract cannot."""
        response = await self._call(user, "GET", path, params=params)
        if missing_ok and response.status_code == 404 and _error_of(response) == "not_found":
            return None
        if invalid is not None and response.status_code == 400:
            raise invalid
        return self._json(response, "GET", path)

    async def _write(
        self,
        user: User,
        method: str,
        path: str,
        *,
        missing: Problem,
        refusals: dict[str, Problem] | None = None,
        body: Any = None,
    ) -> httpx.Response:
        """Space's answer to a write it took; `missing` when what the write acts on is gone, or no
        longer the user's, since the contract read it, and each of the `refusals` for the error
        Space names it with, as when the user's role changed meanwhile."""
        response = await self._call(user, method, path, body=body)
        error = _error_of(response)
        if response.status_code == 404 and error == "not_found":
            raise missing
        if not response.is_success and error in (refusals or {}):
            raise (refusals or {})[error]
        # Space checks what the contract cannot
        if response.status_code == 400:
            raise invalid_request(f"Space refused {method} {path}: {error}.")
        if not response.is_success:
            raise _unavailable(f"Space answered {response.status_code} to {method} {path}.")
        return response

    async def spaces(self, user: User) -> list[SpaceSummary]:
        """The spaces the user is a member of, by name."""
        found = await self._get(user, "/spaces")
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

    async def space(self, user: User, space_id: str) -> SpaceDetail:
        """The space, if the user is a member of it: Space answers for any other as for an
        unknown one."""
        found = await self._get(user, f"/spaces/{space_id}", missing_ok=True)
        if found is None:
            raise space_not_found(space_id)
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

    async def people(self, user: User, words: str | None, page: int) -> tuple[list[Person], bool]:
        """The active people of the user's organization whose name, username or email holds the
        words, if any, by pages of 20; whether more pages follow comes with them."""
        params: dict[str, str | int] = {"page": page} | ({"search": words} if words else {})
        found = await self._get(user, "/organization/members", params)
        try:
            people = [
                Person(
                    username=_text(person["username"]),
                    email=_text(person["email"]),
                    display_name=_optional_text(person.get("displayName")),
                )
                for person in found["members"]
            ]
            more = found["hasNextPage"]
        except (KeyError, TypeError, AttributeError) as error:
            raise _unavailable("Space gave the people in an unexpected form.") from error
        if not isinstance(more, bool):
            raise _unavailable("Space gave the people in an unexpected form.")
        return people, more

    async def feed(
        self,
        user: User,
        space_id: str,
        *,
        category: str | None,
        limit: int,
        before: str | None,
    ) -> tuple[list[FeedItem], str | None]:
        """A page of the feed of the space, newest first, and the cursor of the next one; None
        after the last."""
        params: dict[str, str | int] = {"limit": limit}
        if category is not None:
            params["category"] = category
        if before is not None:
            params["before"] = before
        found = await self._get(
            user,
            f"/spaces/{space_id}/feed",
            params,
            missing_ok=True,
            invalid=invalid_request(
                "before: Space does not know this cursor: pass the next a list_feed_items answer"
                " gave."
            ),
        )
        if found is None:
            raise space_not_found(space_id)
        try:
            items = [_feed_item(item) for item in found["items"]]
            following = _optional_text(found["next"])
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the feed in an unexpected form.") from error
        return items, following

    async def item(self, user: User, space_id: str, item_id: str) -> FeedItem | None:
        """The item of the feed of the space; None if the feed has no such item, or if the
        space is not the user's."""
        found = await self._get(user, f"/spaces/{space_id}/feed/items/{item_id}", missing_ok=True)
        if found is None:
            return None
        try:
            return _feed_item(found)
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the item in an unexpected form.") from error

    async def react(self, user: User, space_id: str, item_id: str, key: str) -> None:
        """Adds the user's reaction to the item, which Space keeps once."""
        path = f"/spaces/{space_id}/feed/items/{item_id}/reactions/{quote(key, safe='')}"
        await self._write(user, "PUT", path, missing=feed_item_not_found(space_id, item_id))

    async def unreact(self, user: User, space_id: str, item_id: str, key: str) -> None:
        """Takes the user's reaction to the item back."""
        path = f"/spaces/{space_id}/feed/items/{item_id}/reactions/{quote(key, safe='')}"
        await self._write(user, "DELETE", path, missing=feed_item_not_found(space_id, item_id))

    async def post(self, user: User, space_id: str, text: str) -> FeedItem:
        """Posts the text in the feed of the space, as the user: the post as Space keeps it."""
        path = f"/spaces/{space_id}/feed/posts"
        response = await self._write(
            user,
            "POST",
            path,
            missing=space_not_found(space_id),
            refusals={"cannot_post": forbidden_role(space_id)},
            body={"body": text},
        )
        try:
            return _feed_item(response.json())
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the new post in an unexpected form.") from error

    async def edit(self, user: User, space_id: str, item_id: str, text: str) -> FeedItem:
        """Changes the text of the user's post: the post as Space keeps it."""
        path = f"/spaces/{space_id}/feed/posts/{item_id}"
        response = await self._write(
            user,
            "PATCH",
            path,
            missing=feed_item_not_found(space_id, item_id),
            refusals={"not_author": not_author(space_id, item_id)},
            body={"body": text},
        )
        try:
            return _feed_item(response.json())
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Space gave the post in an unexpected form.") from error

    async def delete(self, user: User, space_id: str, item_id: str) -> None:
        """Deletes the user's post, and its reactions, for good."""
        await self._write(
            user,
            "DELETE",
            f"/spaces/{space_id}/feed/posts/{item_id}",
            missing=feed_item_not_found(space_id, item_id),
            refusals={"not_author": not_author(space_id, item_id)},
        )

    async def add_members(self, user: User, space_id: str, usernames: list[str], role: str) -> None:
        """Adds people of the user's organization to the space, by username, with one role."""
        await self._write(
            user,
            "POST",
            f"/spaces/{space_id}/members",
            missing=space_not_found(space_id),
            # ldap-rest's refusals, which Space passes on: someone made a member, or whose
            # account was disabled, since the contract read them
            refusals={
                "not_space_admin": not_space_admin(space_id),
                "MEMBER_EXISTS": member_exists(space_id, []),
                "USER_NOT_FOUND": person_not_found(usernames),
            },
            body={"usernames": usernames, "role": role},
        )

    async def set_role(self, user: User, space_id: str, user_id: str, role: str) -> None:
        """Changes the role of a member of the space."""
        await self._write(
            user,
            "PATCH",
            f"/spaces/{space_id}/members/{user_id}",
            missing=member_not_found(space_id, user_id),
            # ldap-rest's refusals, which Space passes on
            refusals={
                "not_space_admin": not_space_admin(space_id),
                "LAST_ADMIN": last_admin(space_id),
                "MEMBER_NOT_FOUND": member_not_found(space_id, user_id),
            },
            body={"role": role},
        )

    async def remove_member(self, user: User, space_id: str, user_id: str) -> None:
        """Removes a member from the space: Space takes one another admin removed first for
        removed."""
        await self._write(
            user,
            "DELETE",
            f"/spaces/{space_id}/members/{user_id}",
            missing=member_not_found(space_id, user_id),
            refusals={
                "not_space_admin": not_space_admin(space_id),
                "LAST_ADMIN": last_admin(space_id),
            },
        )
