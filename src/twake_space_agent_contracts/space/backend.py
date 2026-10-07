"""The Twake Space backend (0.1.9), called as the user with their own token: Space acts for the uuid
of the token's user in their org_id, and shows them the spaces they are a member of."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem

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

    def member_named(self, email: str) -> Member | None:
        """The one member of that email, whatever its case; None if no member has it, or
        several do."""
        found = [member for member in self.members if member.email.lower() == email.lower()]
        return found[0] if len(found) == 1 else None


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

    async def _call(self, user: User, method: str, path: str) -> httpx.Response:
        """Space's answer, once Space answered and took the user's token."""
        try:
            response = await self._http.request(
                method,
                self._url + path,
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

    async def _get(self, user: User, path: str, *, missing_ok: bool = False) -> Any:
        """Space's JSON answer; None when what is asked for is missing and missing_ok is set:
        Space answers 404 not_found for what does not exist and for what the user does not
        reach alike."""
        response = await self._call(user, "GET", path)
        if missing_ok and response.status_code == 404 and _error_of(response) == "not_found":
            return None
        return self._json(response, "GET", path)

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
