"""mail.email.move.v1: an email of the user moved to another of their own mailboxes, or archived."""

from functools import partial
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import EXAMPLE_ID, EmailId
from twake_space_agent_contracts.mail.tmail import JMAP_ID, SPAM_ROLES, Moved, Placement, TMail
from twake_space_agent_contracts.problems import Problem, invalid_request

# The special mailboxes an email is not moved to: trash_email puts an email in the trash, and the
# user moves emails to the others in Twake Mail
SPECIAL = {"drafts", "sent", "outbox", "templates", "trash", *SPAM_ROLES}


class Destination(BaseModel):
    """One of the user's own mailboxes, by its id or by its name."""

    mailbox_id: Annotated[
        str | None,
        Field(pattern=JMAP_ID, description="The id of the mailbox, as list_mailboxes gives it."),
    ] = None
    mailbox_name: Annotated[
        str | None,
        Field(
            min_length=1,
            max_length=200,
            description="The name of the mailbox, whatever its case, such as Projects.",
        ),
    ] = None


def out_of_spam(placement: Placement) -> None:
    """Refuses to take an email out of spam, which tells TMail that it is not spam."""
    if placement.in_spam:
        raise Problem(
            status=409,
            code="email_in_spam",
            title="Email in spam",
            detail="The email is in spam: taking it out tells Mail that it is not spam, which the"
            " user does in Twake Mail. trash_email can still put it in the trash.",
        )


def router(tmail: TMail, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/mail/emails", tags=["mail.email.move.v1"])

    @routes.post(
        "/{email_id}/move",
        operation_id="move_email",
        summary="Move an email of the user to another of their mailboxes",
        description=(
            "Moves an email of the user you act for to another of their own mailboxes, out of the "
            "others it is in. Name the mailbox by mailbox_id, its id as list_mailboxes gives it, "
            "or by mailbox_name, its name whatever its case: a name that several of their "
            "mailboxes have is refused, and you ask the user which one they mean. An email is not "
            "moved to drafts, sent, outbox, templates, trash or spam: trash_email puts it in the "
            "trash. An email in spam is not taken out of it. The email can be moved back. "
            f"Example, for an email that list_emails gave with the id {EXAMPLE_ID}, to the user's "
            f"mailbox named Projects: email_id={EXAMPLE_ID}, "
            'body={"mailbox_name": "Projects"}.'
        ),
        # The email can be moved back: the owner's consent to write in Mail covers it
        openapi_extra={"x-twake-risk": "low"},
    )
    async def move_email(
        email_id: EmailId,
        destination: Destination,
        user: Annotated[User, Depends(caller)],
    ) -> Moved:
        match destination.mailbox_id, destination.mailbox_name:
            case str() as mailbox_id, None:
                find = partial(Placement.with_id, mailbox_id=mailbox_id)
            case None, str() as name:
                find = partial(Placement.named, name=name)
            case _:
                raise invalid_request(
                    "body: give mailbox_id or mailbox_name, and only one of them."
                )
        placement = await tmail.placement(user, email_id)
        out_of_spam(placement)
        mailbox = find(placement)
        if mailbox.role in SPECIAL:
            how = "trash_email puts emails there" if mailbox.role == "trash" else "the user does"
            raise Problem(
                status=409,
                code="mailbox_forbidden",
                title="Mailbox forbidden",
                detail=f"An email is not moved to the {mailbox.role} mailbox: {how}.",
            )
        return await tmail.move(user, placement, mailbox)

    @routes.post(
        "/{email_id}/archive",
        operation_id="archive_email",
        summary="Archive an email of the user",
        description=(
            "Moves an email of the user you act for to their archive, their mailbox whose role is "
            "archive, out of the others it is in. A user without an archive is answered "
            "mailbox_not_found. An email in spam is not taken out of it. The email can be moved "
            "back with move_email. Example, for an email that list_emails gave with the id "
            f"{EXAMPLE_ID}: email_id={EXAMPLE_ID}."
        ),
        # The email can be moved back: the owner's consent to write in Mail covers it
        openapi_extra={"x-twake-risk": "low"},
    )
    async def archive_email(email_id: EmailId, user: Annotated[User, Depends(caller)]) -> Moved:
        placement = await tmail.placement(user, email_id)
        out_of_spam(placement)
        return await tmail.move(user, placement, placement.with_role("archive"))

    return routes
