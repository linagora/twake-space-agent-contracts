"""space.spaces.read.v1: the spaces the user is a member of in Twake Space, and one space with its
members and what its apps linked to it."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.space import EXAMPLE_SPACE, UNTRUSTED, SpaceId, line
from twake_space_agent_contracts.space.backend import Member, SpaceDetail, TwakeSpace

LONGEST_NAME = 255
LONGEST_DESCRIPTION = 1000
MOST_SPACES = 100
ROLE = (
    "viewer, editor or admin. A viewer reads and reacts; an editor posts too; an admin also adds, "
    "changes and removes members."
)


class SpaceText(BaseModel):
    """What people wrote of a space: its name and its description."""

    name: str | None
    description: str | None


class ListedSpace(BaseModel):
    space_id: str
    role: str = Field(description=f"The user's role in the space: {ROLE}")
    member_count: int
    untrusted: SpaceText


class SpaceList(BaseModel):
    spaces: list[ListedSpace]
    truncated: bool = Field(
        description=f"Whether the user has more spaces than the {MOST_SPACES} the list holds."
    )


class MemberText(BaseModel):
    """The name a member goes by, which they or their organization wrote."""

    display_name: str | None


class SpaceMember(BaseModel):
    user_id: str = Field(
        description="Who the member is in Space, as update_space_member and remove_space_member "
        "take it."
    )
    username: str
    email: str
    role: str = Field(description=f"Their role in the space: {ROLE}")
    you: bool = Field(description="Whether the member is the user you act for.")
    untrusted: MemberText


class GroupText(BaseModel):
    name: str | None


class SpaceGroup(BaseModel):
    """A group of the organization linked to the space: its people are members, with its role."""

    group_id: str
    role: str
    untrusted: GroupText


class Space(BaseModel):
    """A space the user is a member of, with its members and what its apps linked to it."""

    space_id: str
    role: str = Field(description=f"The user's role in the space: {ROLE}")
    created_at: datetime
    apps: list[str] = Field(
        description="The tabs the space shows, among chat, tasks, drive, mail and calendar."
    )
    tasks_project_id: str | None = Field(
        description="Its project in Twake Tasks, whose boards open_boards gives with this "
        "project_id; null while Tasks prepares it, or without one."
    )
    chat_room_id: str | None = Field(
        description="Its room in Twake Chat, as the room_id of the chat contracts; null while "
        "Chat prepares it, or without one."
    )
    mailbox_id: str | None = Field(
        description="Its shared mailbox in Twake Mail; null while Mail prepares it, or without one."
    )
    calendar_id: str | None = Field(
        description="Its calendar in Twake Calendar; null while Calendar prepares it, or without "
        "one."
    )
    drive_id: str | None = Field(
        description="Its files in Twake Drive; null while Drive prepares it, or without one."
    )
    members: list[SpaceMember] = Field(description="By username.")
    groups: list[SpaceGroup]
    untrusted: SpaceText


def space_member(member: Member, user: User) -> SpaceMember:
    """A member as the contracts give them, to the user."""
    return SpaceMember(
        user_id=member.user_id,
        username=member.username,
        email=member.email,
        role=member.role,
        you=member.email.lower() == user.email,
        untrusted=MemberText(display_name=line(member.display_name, LONGEST_NAME)),
    )


def space_of(detail: SpaceDetail, user: User) -> Space:
    """A space as read_space gives it, to the user."""
    linked = detail.resources
    return Space(
        space_id=detail.space_id,
        role=detail.role,
        created_at=detail.created_at,
        apps=list(detail.apps),
        tasks_project_id=linked.get("project"),
        chat_room_id=linked.get("matrix_space"),
        mailbox_id=linked.get("mailbox"),
        calendar_id=linked.get("calendar"),
        drive_id=linked.get("drive"),
        members=[space_member(member, user) for member in detail.members],
        groups=[
            SpaceGroup(
                group_id=group.group_id,
                role=group.role,
                untrusted=GroupText(name=line(group.name, LONGEST_NAME)),
            )
            for group in detail.groups
        ],
        untrusted=SpaceText(
            name=line(detail.name, LONGEST_NAME),
            description=line(detail.description, LONGEST_DESCRIPTION),
        ),
    )


def router(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.spaces.read.v1"])

    @routes.get(
        "/spaces",
        operation_id="list_spaces",
        summary="List the user's spaces in Twake Space",
        description=(
            "Lists the spaces of Twake Space the user you act for is a member of, by name, "
            f"{MOST_SPACES} at most, with their role in each and how many members each has. Give "
            "space_id to read_space for its members and the apps linked to it, or to "
            f"list_feed_items for its feed. {UNTRUSTED} Example: (no parameters)."
        ),
    )
    async def list_spaces(user: Annotated[User, Depends(caller)]) -> SpaceList:
        found = await space.spaces(user)
        return SpaceList(
            spaces=[
                ListedSpace(
                    space_id=summary.space_id,
                    role=summary.role,
                    member_count=summary.member_count,
                    untrusted=SpaceText(
                        name=line(summary.name, LONGEST_NAME),
                        description=line(summary.description, LONGEST_DESCRIPTION),
                    ),
                )
                for summary in found[:MOST_SPACES]
            ],
            truncated=len(found) > MOST_SPACES,
        )

    @routes.get(
        "/spaces/{space_id}",
        operation_id="read_space",
        summary="Read one of the user's spaces in Twake Space",
        description=(
            "Reads a space the user you act for is a member of, by the space_id list_spaces "
            "gives: the user's role there, its members with theirs, by username, you telling "
            "which one is the user, the groups linked to it, and what its apps linked to it: its "
            "Tasks project, Chat room, shared mailbox, calendar and files. A space the user is "
            f"not a member of answers like an unknown one. {UNTRUSTED} Example: {EXAMPLE_SPACE}."
        ),
    )
    async def read_space(space_id: SpaceId, user: Annotated[User, Depends(caller)]) -> Space:
        return space_of(await space.space(user, space_id), user)

    return routes
