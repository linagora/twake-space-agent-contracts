"""mail.email.trash.v1: an email of the user put in their trash, from which it can be moved back."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import EXAMPLE_ID, EmailId
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
            "trash, out of the others it is in, spam included. It is not deleted: move_email can "
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

    return routes
