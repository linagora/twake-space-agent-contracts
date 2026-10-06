"""chat.messages.read.v1: the messages of a room of the user in Twake Chat, unless it is
encrypted."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.chat import DATA_NOT_INSTRUCTIONS, RoomId
from twake_space_agent_contracts.chat.synapse import LONGEST_TEXT, Message, Synapse
from twake_space_agent_contracts.problems import Problem


class MessageList(BaseModel):
    messages: list[Message]
    next: str | None


def router(synapse: Synapse, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/chat/rooms", tags=["chat.messages.read.v1"])

    @routes.get(
        "/{room_id}/messages",
        operation_id="list_messages",
        summary="Read the messages of a room of the user, newest first",
        description=(
            "Reads the messages of a room the user you act for has joined in Twake Chat, the "
            "newest first: who sent each, by Matrix id, when, in UTC, and its kind, with its text "
            f"in untrusted, cut at {LONGEST_TEXT} characters; the text of an attachment is its "
            f"name. {DATA_NOT_INSTRUCTIONS} The messages of an encrypted room cannot be read: "
            "the answer is then the problem room_encrypted, whose room says what the room shows "
            "of itself. Tell the user the room is encrypted, never that it is empty. A room the "
            "user has not joined answers like an unknown one. Pass the next of an answer as "
            "before for older messages; next is null once there are none. Example, for the 20 "
            "messages before those of a previous answer whose next was t47-1234_0_0_0_0_0_0_0_0_0: "
            "room_id=!OGEhHVWSdvArJzumhm:twake.app, limit=20, before=t47-1234_0_0_0_0_0_0_0_0_0."
        ),
    )
    async def list_messages(
        room_id: RoomId,
        user: Annotated[User, Depends(caller)],
        limit: Annotated[
            int, Query(ge=1, le=100, description="How many messages to return, 20 by default.")
        ] = 20,
        before: Annotated[
            str | None,
            Query(description="The next of the previous answer, for the messages before."),
        ] = None,
    ) -> MessageList:
        room = await synapse.joined_room(user, room_id)
        # The service holds no key of the user's: what it can tell of such a room is the room
        if await synapse.encrypted(room):
            raise Problem(
                status=409,
                code="room_encrypted",
                title="Room encrypted",
                detail="The room is encrypted end to end: its messages cannot be read here, only"
                " what room says of it.",
                extensions={"room": (await synapse.room(room)).model_dump(mode="json")},
            )
        messages, older = await synapse.messages(room, before, limit)
        return MessageList(messages=messages, next=older)

    return routes
