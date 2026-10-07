"""mail.email.trash.v1: emails of the user put in their trash, one at a time or several at once,
from which they can be moved back."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import EXAMPLE_ID, EmailId
from twake_space_agent_contracts.mail.batch import (
    OTHER_ID,
    OUTCOMES,
    Emails,
    MovedEmails,
    for_several,
    in_one_call,
    moved_emails,
)
from twake_space_agent_contracts.mail.move import moved
from twake_space_agent_contracts.mail.tmail import Moved, TMail
from twake_space_agent_contracts.previews import Previewing


def router(tmail: TMail, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/mail/emails", tags=["mail.email.trash.v1"])

    @routes.post(
        "/{email_id}/trash",
        operation_id="trash_email",
        summary="Put an email of the user in their trash",
        description=(
            "Puts an email of the user you act for in their trash, their mailbox whose role is "
            "trash, out of the others it is in, spam included. "
            f"{for_several('trash_emails', 'trash_email')} It is not deleted: move_email can "
            "move it back. A user without a trash is answered mailbox_not_found. Example, for an "
            f"email that list_emails gave with the id {EXAMPLE_ID}: email_id={EXAMPLE_ID}."
        ),
        response_model=Moved,
        # The email can be moved back: the owner's consent to write in Mail covers it. It tells
        # what it would do, for when the owner is asked.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def trash_email(
        email_id: EmailId, user: Annotated[User, Depends(caller)], preview: Previewing
    ) -> Moved | JSONResponse:
        placement = await tmail.placement(user, email_id)
        return await moved(tmail, user, placement, placement.trash(), "trash", preview)

    @routes.post(
        "/trash",
        operation_id="trash_emails",
        summary="Put several emails of the user in their trash in one call",
        description=(
            "Puts several emails of the user you act for in their trash in one call, their "
            "mailbox whose role is trash, each out of the others it is in, spam included: to "
            f"trash several emails, {in_one_call('trash_email')}. None is deleted: move_emails "
            "can move them back. A user without a trash is answered mailbox_not_found, and no "
            f"email moves. {OUTCOMES} Example, for two emails that list_emails gave with the ids "
            f"{EXAMPLE_ID} and {OTHER_ID}: "
            f'body={{"email_ids": ["{EXAMPLE_ID}", "{OTHER_ID}"]}}.'
        ),
        response_model=MovedEmails,
        # The emails can be moved back: the owner's consent to write in Mail covers it, as it
        # covers trashing them one at a time. It tells what it would do, for when the owner is
        # asked.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def trash_emails(
        emails: Emails, user: Annotated[User, Depends(caller)], preview: Previewing
    ) -> MovedEmails | JSONResponse:
        placements = await tmail.placements(user, emails.email_ids)
        trash = placements.trash()
        return await moved_emails(
            tmail, user, placements, emails.email_ids, trash, "trash", preview
        )

    return routes
