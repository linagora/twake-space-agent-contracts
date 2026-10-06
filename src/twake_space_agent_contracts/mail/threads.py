"""mail.threads.read.v1: a conversation of the user, from their own mailboxes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import UNTRUSTED
from twake_space_agent_contracts.mail.tmail import JMAP_ID, Email, TMail


class Thread(BaseModel):
    thread_id: str
    emails: list[Email]


def router(tmail: TMail, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/mail", tags=["mail.threads.read.v1"])

    @routes.get(
        "/threads/{thread_id}",
        operation_id="read_thread",
        summary="Read a conversation of the user",
        description=(
            "Reads the last emails of a conversation of the user you act for, at most limit, "
            "oldest first, from their own mailboxes only: the others are left out. Each email "
            "comes as read_email gives it, its text cut after 8 KiB. "
            f"{UNTRUSTED} Example, for the conversation of an email that list_emails gave with "
            "the thread_id 3c1d5e7f-a2b1-11f0-8de9-0242ac120002: "
            "thread_id=3c1d5e7f-a2b1-11f0-8de9-0242ac120002, limit=10."
        ),
    )
    async def read_thread(
        thread_id: Annotated[
            str,
            Path(
                pattern=JMAP_ID,
                description="The id of the conversation, the thread_id of one of its emails.",
            ),
        ],
        user: Annotated[User, Depends(caller)],
        limit: Annotated[
            int,
            Query(ge=1, le=20, description="How many of its last emails to return, 10 by default."),
        ] = 10,
    ) -> Thread:
        return Thread(thread_id=thread_id, emails=await tmail.thread(user, thread_id, limit))

    return routes
