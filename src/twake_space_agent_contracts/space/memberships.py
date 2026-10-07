"""space.member.add.v1, space.member.update.v1 and space.member.remove.v1: an admin of one of the
user's spaces in Twake Space adds people of their organization to it, changes the role of its
members and removes them."""

from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import Previewing, digest_of
from twake_space_agent_contracts.problems import Problem
from twake_space_agent_contracts.space import (
    EXAMPLE_MEMBER,
    EXAMPLE_SPACE,
    ROLES,
    UNTRUSTED,
    Role,
    SpaceId,
    UserId,
)
from twake_space_agent_contracts.space.backend import (
    Member,
    Person,
    SpaceDetail,
    TwakeSpace,
    group_member,
    member_exists,
    member_not_found,
    not_space_admin,
    person_not_found,
)
from twake_space_agent_contracts.space.spaces import Space, SpaceMember, space_member, space_of
from twake_space_agent_contracts.space.summaries import adding_members, changing_role, removing

MOST_PEOPLE = 20
"""The most people a call adds."""
MOST_PAGES = 5
"""How many pages of what the directory finds for a username are read to find its person: the
first 100 people it finds."""


class NewMembers(BaseModel):
    """People of the organization to add to the space, with one role."""

    model_config = ConfigDict(extra="forbid")

    usernames: list[
        Annotated[
            str,
            Field(
                min_length=2,
                max_length=255,
                description="A username, as search_organization_people gives it.",
            ),
        ]
    ] = Field(min_length=1, max_length=MOST_PEOPLE)
    role: Role = Field(description=f"Their role in the space: {ROLES}")


async def _administered(space: TwakeSpace, user: User, space_id: str) -> SpaceDetail:
    """The space, if the user is one of its admins: only they change its members."""
    detail = await space.space(user, space_id)
    if detail.role != "admin":
        raise not_space_admin(space_id)
    return detail


async def _person(space: TwakeSpace, user: User, username: str) -> Person | None:
    """The active person of the user's organization of that username, whatever its case, among
    those the directory finds for it; None if it finds none."""
    for page in range(1, MOST_PAGES + 1):
        found, more = await space.people(user, username, page)
        for person in found:
            if person.username.lower() == username.lower():
                return person
        if not more:
            break
    return None


async def _people(space: TwakeSpace, user: User, usernames: list[str]) -> list[Person]:
    """The people of these usernames, each once, as the directory names them: all of them, or
    the problem that names those it does not have."""
    found: dict[str, Person | None] = {}
    for username in usernames:
        if username.lower() not in found:
            found[username.lower()] = await _person(space, user, username)
    missing = [username for username, person in found.items() if person is None]
    if missing:
        raise person_not_found(missing)
    return [person for person in found.values() if person is not None]


def _add(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.member.add.v1"])

    @routes.post(
        "/spaces/{space_id}/members",
        operation_id="add_space_members",
        summary="Add people of the organization to one of the user's spaces in Twake Space",
        description=(
            "Adds people of the organization of the user you act for, by the usernames "
            "search_organization_people gives, to a space where the user is an admin, all with "
            f"one role, {MOST_PEOPLE} at most: they see the space, its feed and what its apps "
            "hold, such as its chat room, tasks and files. Call it only once the user asked to "
            "add these very people; they confirm each call. It answers the space, as read_space "
            f"gives it. {UNTRUSTED} Example, to add a colleague who will post: {EXAMPLE_SPACE}, "
            'body={"usernames": ["jmartin"], "role": "editor"}.'
        ),
        response_model=Space,
        # The people see all the space holds, and its members see them: the owner confirms each
        # call, shown whom it would add
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def add_space_members(
        space_id: SpaceId,
        new: NewMembers,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> Space | JSONResponse:
        detail = await _administered(space, user, space_id)
        people = await _people(space, user, new.usernames)
        members = {member.username.lower(): member for member in detail.members}
        already = [members[key] for person in people if (key := person.username.lower()) in members]
        # Space would refuse them, as ldap-rest does: their role is changed, not added again
        others = [member for member in already if member.role != new.role]
        if others:
            raise member_exists(space_id, others)
        added = [person for person in people if person.username.lower() not in members]
        left = [person for person in people if person.username.lower() in members]
        # What the owner allows: the people, and who of them are members already
        digest = digest_of(
            space_id,
            new.role,
            sorted([person.username.lower(), person.email] for person in added),
            sorted([person.username.lower(), person.email] for person in left),
        )
        if preview.asked:
            summary = adding_members(detail.name, new.role, added, left, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        if not added:
            return space_of(detail, user)
        await space.add_members(user, space_id, [person.username for person in added], new.role)
        return space_of(await space.space(user, space_id), user)

    return routes


class NewRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role = Field(description=f"The member's role in the space: {ROLES}")


async def _not_direct(
    space: TwakeSpace, user: User, detail: SpaceDetail, user_id: str, problem: Problem
) -> Problem:
    """What a member that ldap-rest's member routes do not find answers: group_member when the
    space, which links groups, lists them still, as their people; the problem as it is when the
    member is gone."""
    if detail.groups and (await space.space(user, detail.space_id)).member(user_id) is not None:
        return group_member(detail.space_id, user_id)
    return problem


def _member(detail: SpaceDetail, user_id: str) -> Member:
    """The member of that user id, if the space has them."""
    member = detail.member(user_id)
    if member is None:
        raise member_not_found(detail.space_id, user_id)
    return member


def _update(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.member.update.v1"])

    @routes.patch(
        "/spaces/{space_id}/members/{user_id}",
        operation_id="update_space_member",
        summary="Change the role of a member of one of the user's spaces in Twake Space",
        description=(
            "Changes the role of a member of a space where the user you act for is an admin, by "
            f"the user_id read_space gives: {ROLES} Someone the space lists through a linked "
            "group has the group's role, which this does not change: it answers group_member. "
            "Call it only once the user asked for this "
            "very change; they confirm each call. It answers the member, as read_space gives "
            f"them. {UNTRUSTED} Example, to let a member post: {EXAMPLE_MEMBER}, "
            'body={"role": "editor"}.'
        ),
        response_model=SpaceMember,
        # What the member may do in the space changes, admins adding or removing others: the
        # owner confirms each change, shown the role it would give
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def update_space_member(
        space_id: SpaceId,
        user_id: UserId,
        changed: NewRole,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceMember | JSONResponse:
        detail = await _administered(space, user, space_id)
        member = _member(detail, user_id)
        me = detail.user_id_of(user.email)
        # What the owner allows: the member as they are
        digest = digest_of(space_id, user_id, member.email, member.role)
        if preview.asked:
            summary = changing_role(member, changed.role, detail.name, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        if member.role == changed.role:
            return space_member(member, me)
        try:
            await space.set_role(user, space_id, user_id, changed.role)
        except Problem as problem:
            if problem.code != "member_not_found":
                raise
            raise await _not_direct(space, user, detail, user_id, problem) from problem
        return space_member(replace(member, role=changed.role), me)

    return routes


def _remove(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.member.remove.v1"])

    @routes.delete(
        "/spaces/{space_id}/members/{user_id}",
        operation_id="remove_space_member",
        summary="Remove a member from one of the user's spaces in Twake Space",
        description=(
            "Removes a member from a space where the user you act for is an admin, by the user_id "
            "read_space gives: they no longer see the space, nor what its apps hold. A member "
            "through a linked group only stays one while the group is linked. Call it only once "
            "the user asked to remove this very member; they confirm each call. It answers the "
            f"member as they were. {UNTRUSTED} Example: {EXAMPLE_MEMBER}."
        ),
        response_model=SpaceMember,
        # The member loses what the space holds: the owner confirms each removal, shown whom it
        # would remove
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def remove_space_member(
        space_id: SpaceId,
        user_id: UserId,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceMember | JSONResponse:
        detail = await _administered(space, user, space_id)
        member = _member(detail, user_id)
        removed = space_member(member, detail.user_id_of(user.email))
        # What the owner allows: the member as they are
        digest = digest_of(space_id, user_id, member.email, member.role)
        if preview.asked:
            summary = removing(member, removed.you, detail.name, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        await space.remove_member(user, space_id, user_id)
        return removed

    return routes


def routers(space: TwakeSpace, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the three contracts, one each."""
    return [_add(space, caller), _update(space, caller), _remove(space, caller)]
