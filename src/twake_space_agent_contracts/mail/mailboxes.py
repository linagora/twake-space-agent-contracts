"""mail.mailboxes.read.v1: the user's own mailboxes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail.tmail import Mailbox, TMail


class MailboxList(BaseModel):
    mailboxes: list[Mailbox]


def router(tmail: TMail, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/mail", tags=["mail.mailboxes.read.v1"])

    @routes.get(
        "/mailboxes",
        operation_id="list_mailboxes",
        summary="List the user's mailboxes",
        description=(
            "Lists the mailboxes of the user you act for: their own only, never one shared with "
            "them or delegated to them. role names the special ones, such as inbox, sent, "
            "drafts, archive, trash and spam; parent_id is the mailbox a mailbox sits in. Pass a "
            "mailbox's id as mailbox to list_emails or search_emails, such as the id of the one "
            "whose role is inbox to list the inbox. Example: (no parameters)."
        ),
    )
    async def list_mailboxes(user: Annotated[User, Depends(caller)]) -> MailboxList:
        return MailboxList(mailboxes=await tmail.mailboxes(user))

    return routes
