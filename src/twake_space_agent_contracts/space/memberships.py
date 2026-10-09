"""space.member.add.v1, space.member.update.v1 and space.member.remove.v1: an admin of one of the
user's spaces in Twake Space adds people of their organization to it, changes the role of its
members and removes them."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.previews import Previewing, digest_of
from twake_space_agent_contracts.problems import Problem
from twake_space_agent_contracts.space import (
    EXAMPLE_MEMBER,
    EXAMPLE_SPACE,
    LONGEST_NAME,
    NO_POSTING,
    ROLES,
    UNTRUSTED,
    Role,
    SpaceId,
    UserId,
)
from twake_space_agent_contracts.space.backend import (
    Member,
    SpaceDetail,
    SpaceOwner,
    SpaceOwnerDependency,
    TwakeSpace,
    member_exists,
    member_not_found,
    not_space_admin,
    person_not_found,
)
from twake_space_agent_contracts.space.people import read_spaces
from twake_space_agent_contracts.space.spaces import Space, SpaceMember, space_member, space_of
from twake_space_agent_contracts.space.summaries import adding_members, changing_role, removing

MOST_PEOPLE = 20
"""The most people a call adds."""


class NewMembers(BaseModel):
    """People of the organization to add to the space, all with one role."""

    model_config = ConfigDict(extra="forbid")

    usernames: list[
        Annotated[
            str,
            Field(
                min_length=1,
                max_length=LONGEST_NAME,
                description="A username, as read_space and search_space_people give it, or as "
                "the user gave it.",
            ),
        ]
    ] = Field(min_length=1, max_length=MOST_PEOPLE)
    role: Role = Field(description=f"Their role in the space: {ROLES}")


async def _administered(space: TwakeSpace, owner: SpaceOwner, space_id: str) -> SpaceDetail:
    """The space, if the user is one of its admins: only they change its members."""
    detail = await space.space(owner, space_id)
    if detail.role != "admin":
        raise not_space_admin(space_id)
    return detail


def _each_once(usernames: list[str]) -> list[str]:
    """The usernames, each once whatever its case, as first written."""
    found: dict[str, str] = {}
    for username in usernames:
        found.setdefault(username.lower(), username)
    return list(found.values())


async def _known(space: TwakeSpace, owner: SpaceOwner, usernames: list[str]) -> list[Member | str]:
    """The people of these usernames, as the members of the user's first spaces name them; the
    username alone for anyone else: Space lets no API token search the directory of the
    organization."""
    if not usernames:
        return []
    details, _ = await read_spaces(space, owner)
    known = {member.username.lower(): member for detail in details for member in detail.members}
    return [known.get(username.lower(), username) for username in usernames]


async def _not_found(
    space: TwakeSpace, owner: SpaceOwner, before: SpaceDetail, sent: list[str]
) -> Problem:
    """Space's refusal of usernames, as ldap-rest refuses them all for one it does not have, once
    the space is read again: the usernames it does not list, and the people it lists of the others
    who it did not before, as when another admin added them meanwhile."""
    now = await space.space(owner, before.space_id)
    listed = {member.username.lower(): member for member in now.members}
    known = {member.user_id for member in before.members}
    entered = [
        listed[username.lower()]
        for username in sent
        if username.lower() in listed and listed[username.lower()].user_id not in known
    ]
    missing = [username for username in sent if username.lower() not in listed]
    return person_not_found(missing, entered)


def _add(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.member.add.v1"])

    @routes.post(
        "/spaces/{space_id}/members",
        operation_id="add_space_members",
        summary="Add people of the organization to one of the user's spaces in Twake Space",
        description=(
            "Adds people of the organization of the user you act for to a space where the "
            f"user is an admin, by their usernames, all with one role, {MOST_PEOPLE} at most: "
            "they then see the space, its feed and what its apps hold, such as its chat room, "
            "tasks and files. read_space and search_space_people give the usernames of the "
            "people of the user's spaces; Space does not let an assistant search the directory "
            "of the organization, so ask the user for the username of anyone else. A username "
            "the organization does not have answers person_not_found, and nobody is added. "
            "Someone the space lists through a linked group becomes a direct member, with this "
            "role; a direct member of another role answers member_exists, and nobody is added: "
            "update_space_member changes their role. Call it only once the user asked to add "
            "these very people; they confirm each call. It answers the space, as read_space "
            f"gives it. {NO_POSTING} {UNTRUSTED} Example, to add a colleague who will post: "
            f"{EXAMPLE_SPACE}, "
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
        owner: Annotated[SpaceOwner, Depends(owner_of)],
        preview: Previewing,
    ) -> Space | JSONResponse:
        detail = await _administered(space, owner, space_id)
        wanted = _each_once(new.usernames)
        members = {member.username.lower(): member for member in detail.members}
        added = [username for username in wanted if username.lower() not in members]
        listed = [members[username.lower()] for username in wanted if username.lower() in members]
        others = [member for member in listed if member.role != new.role]
        groups = bool(detail.groups)
        # A space without linked groups lists its direct members alone, whom ldap-rest refuses
        # with another role: update_space_member changes their role
        if others and not groups:
            raise member_exists(space_id, others)
        # What the owner allows: the people to add, and those of them the space lists, as what
        digest = digest_of(
            space_id,
            new.role,
            sorted(username.lower() for username in added),
            sorted([member.username.lower(), member.email, member.role] for member in listed),
        )
        if preview.asked:
            known = await _known(space, owner, added)
            summary = adding_members(detail.name, new.role, known, listed, groups, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        # One with linked groups may list people through them, whom ldap-rest, which sees the
        # direct members alone, makes direct members: those it lists are sent too
        sent = added + [member.username for member in listed] if groups else added
        if not sent:
            return space_of(detail, owner.user, space.feed_url(space_id))
        try:
            await space.add_members(owner, space_id, sent, new.role)
        except Problem as problem:
            # Direct members of another role, maybe added by another admin meanwhile
            if problem.code == "member_exists":
                raise member_exists(space_id, others) from problem
            if problem.code == "person_not_found":
                raise await _not_found(space, owner, detail, sent) from problem
            raise
        now = await space.space(owner, space_id)
        return space_of(now, owner.user, space.feed_url(space_id))

    return routes


class NewRole(BaseModel):
    """The role to give a member of the space."""

    model_config = ConfigDict(extra="forbid")

    role: Role = Field(description=f"The member's role in the space: {ROLES}")


def _member(detail: SpaceDetail, user_id: str) -> Member:
    """The member of that user id, if the space lists them."""
    member = detail.member(user_id)
    if member is None:
        raise member_not_found(detail.space_id, user_id)
    return member


def _update(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.member.update.v1"])

    @routes.patch(
        "/spaces/{space_id}/members/{user_id}",
        operation_id="update_space_member",
        summary="Change the role of a member of one of the user's spaces in Twake Space",
        description=(
            "Changes the role of a member of a space where the user you act for is an admin, by "
            f"the user_id read_space gives: {ROLES} Someone the space lists through a linked "
            "group gets a role of their own, and keeps the stronger of it and the group's. The "
            "space keeps one admin of its own at least, a group linked as admin counting for "
            "none: making its last one something else answers last_admin. Call it only once the "
            "user asked for this very change; they confirm each call. It answers the member, as "
            f"read_space then gives them. {NO_POSTING} {UNTRUSTED} Example, to let a member "
            f"post: {EXAMPLE_MEMBER}, "
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
        owner: Annotated[SpaceOwner, Depends(owner_of)],
        preview: Previewing,
    ) -> SpaceMember | JSONResponse:
        detail = await _administered(space, owner, space_id)
        member = _member(detail, user_id)
        # What the owner allows: the member as they are
        digest = digest_of(space_id, user_id, member.email, member.role)
        if preview.asked:
            groups = bool(detail.groups)
            summary = changing_role(member, changed.role, detail.name, groups, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        if member.role != changed.role:
            await space.set_role(owner, space_id, user_id, changed.role)
            # Someone the space lists through a linked group keeps the stronger role
            detail = await space.space(owner, space_id)
            member = _member(detail, user_id)
        return space_member(member, detail.user_id_of(owner.user.email))

    return routes


def _remove(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.member.remove.v1"])

    @routes.delete(
        "/spaces/{space_id}/members/{user_id}",
        operation_id="remove_space_member",
        summary="Remove a member from one of the user's spaces in Twake Space",
        description=(
            "Removes a member from a space where the user you act for is an admin, by the user_id "
            "read_space gives: they no longer see the space, nor what its apps hold, unless a "
            "group linked to it keeps them in, which the user changes in Twake Space. The space "
            "keeps one admin of its own at least: removing its last one answers last_admin. Call "
            "it only once the user asked to remove this very member; they confirm each call. It "
            f"answers the member as they were. {NO_POSTING} {UNTRUSTED} Example: "
            f"{EXAMPLE_MEMBER}."
        ),
        response_model=SpaceMember,
        # The member loses what the space holds: the owner confirms each removal, shown whom it
        # would remove
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def remove_space_member(
        space_id: SpaceId,
        user_id: UserId,
        owner: Annotated[SpaceOwner, Depends(owner_of)],
        preview: Previewing,
    ) -> SpaceMember | JSONResponse:
        detail = await _administered(space, owner, space_id)
        member = _member(detail, user_id)
        removed = space_member(member, detail.user_id_of(owner.user.email))
        # What the owner allows: the member as they are
        digest = digest_of(space_id, user_id, member.email, member.role)
        if preview.asked:
            groups = bool(detail.groups)
            summary = removing(member, removed.you, detail.name, groups, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        # Space takes the removal of someone ldap-rest has through a linked group alone for
        # done, though the group keeps them in
        await space.remove_member(owner, space_id, user_id)
        return removed

    return routes


def routers(space: TwakeSpace, owner_of: SpaceOwnerDependency) -> list[APIRouter]:
    """The routers of the contracts that change the members of a space, one each."""
    return [_add(space, owner_of), _update(space, owner_of), _remove(space, owner_of)]
