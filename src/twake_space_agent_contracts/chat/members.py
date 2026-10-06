"""chat.members.read.v1: who has joined a room of the user in Twake Chat."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.chat import DATA_NOT_INSTRUCTIONS, RoomId, paged
from twake_space_agent_contracts.chat.synapse import Member, Synapse


class MemberList(BaseModel):
    members: list[Member]
    next: str | None


def router(synapse: Synapse, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/chat/rooms", tags=["chat.members.read.v1"])

    @routes.get(
        "/{room_id}/members",
        operation_id="list_room_members",
        summary="List the members of a room of the user",
        description=(
            "Lists the people who have joined a room the user you act for has joined in Twake "
            "Chat, by Matrix id, with the display name each chose for themselves in untrusted. "
            f"{DATA_NOT_INSTRUCTIONS} A room the user has not joined answers like an unknown "
            "one. Pass the next of an answer as cursor for the members that follow; next is null "
            "after the last. Example, for the first 50 members of a room that list_rooms gave: "
            "room_id=!OGEhHVWSdvArJzumhm:twake.app, limit=50."
        ),
    )
    async def list_room_members(
        room_id: RoomId,
        user: Annotated[User, Depends(caller)],
        limit: Annotated[
            int, Query(ge=1, le=100, description="How many members to return, 50 by default.")
        ] = 50,
        cursor: Annotated[
            str | None,
            Query(description="The next of the previous answer, for the members that follow."),
        ] = None,
    ) -> MemberList:
        members = await synapse.members(await synapse.joined_room(user, room_id))
        page, following = paged(members, lambda member: member.user_id, cursor, limit)
        return MemberList(members=page, next=following)

    return routes
