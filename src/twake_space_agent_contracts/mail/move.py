"""mail.email.move.v1: an email of the user moved to another of their own mailboxes, or archived."""

from typing import Annotated, Self

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import EXAMPLE_ID, EmailId
from twake_space_agent_contracts.mail.tmail import (
    JMAP_ID,
    SPAM_ROLES,
    Mailbox,
    Moved,
    Placement,
    TMail,
)
from twake_space_agent_contracts.problems import Problem

# What TMail does with an email moved into spam or out of it: it reports it to the rspamd filter
# that all users share, as spam or as ham. That is for report_spam and report_not_spam, later
# contracts of high risk, and never for a move
SHARED_FILTER = "the spam filter that all users share"
SPECIAL = {
    **dict.fromkeys(
        ("drafts", "sent", "outbox", "templates"), "it holds what the user writes and sends"
    ),
    "trash": "trash_email puts emails there",
    **dict.fromkeys(
        SPAM_ROLES,
        f"TMail would report it as spam to {SHARED_FILTER}, which takes report_spam, a"
        " high-risk contract not offered yet",
    ),
}
"""The special mailboxes an email is not moved to, by role, and why."""


class Destination(BaseModel):
    """One of the user's own mailboxes: exactly one of mailbox_id and mailbox_name."""

    model_config = ConfigDict(
        extra="forbid",
        # So that the gateway, which checks each body against the document, refuses the others too
        json_schema_extra={"oneOf": [{"required": ["mailbox_id"]}, {"required": ["mailbox_name"]}]},
    )

    # Left out rather than null: the document does not offer null
    mailbox_id: (
        Annotated[
            str,
            Field(
                pattern=JMAP_ID, description="The id of the mailbox, as list_mailboxes gives it."
            ),
        ]
        | SkipJsonSchema[None]
    ) = None
    mailbox_name: (
        Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description="The name of the mailbox, whatever its case, such as Projects.",
            ),
        ]
        | SkipJsonSchema[None]
    ) = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.mailbox_id is None) == (self.mailbox_name is None):
            raise PydanticCustomError(
                "exactly_one", "give exactly one of mailbox_id and mailbox_name"
            )
        return self

    def mailbox(self, placement: Placement) -> Mailbox:
        """The one of the user's own mailboxes it names."""
        match self.mailbox_id, self.mailbox_name:
            case str() as mailbox_id, None:
                return placement.with_id(mailbox_id)
            case None, str() as name:
                return placement.named(name)
        raise AssertionError("A destination names exactly one mailbox, as validated")


def out_of_spam(placement: Placement) -> None:
    """Refuses to move an email out of spam, which TMail would report as ham."""
    if placement.in_spam:
        raise Problem(
            status=409,
            code="email_in_spam",
            title="Email in spam",
            detail=f"The email is in spam: TMail would report it as not spam to {SHARED_FILTER},"
            " which takes report_not_spam, a high-risk contract not offered yet, rather than a"
            " move. trash_email can still put it in the trash, which reports nothing.",
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
        placement = await tmail.placement(user, email_id)
        out_of_spam(placement)
        mailbox = destination.mailbox(placement)
        if mailbox.role in SPECIAL:
            raise Problem(
                status=409,
                code="mailbox_forbidden",
                title="Mailbox forbidden",
                detail=f"An email is not moved to the {mailbox.role} mailbox: "
                f"{SPECIAL[mailbox.role]}.",
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
