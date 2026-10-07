"""mail.email.move.v1: emails of the user moved to another of their own mailboxes, or archived,
one at a time or several at once."""

from typing import Annotated, Literal, Self

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import EXAMPLE_ID, EmailId, people
from twake_space_agent_contracts.mail.batch import (
    OTHER_ID,
    OUTCOMES,
    EmailIds,
    Emails,
    MovedEmails,
    in_one_call,
    moved_emails,
)
from twake_space_agent_contracts.mail.tmail import (
    JMAP_ID,
    SPAM_ROLES,
    Mailbox,
    Moved,
    OwnMailboxes,
    Placement,
    TMail,
)
from twake_space_agent_contracts.previews import (
    Language,
    Preview,
    Previewing,
    digest_of,
    one_line,
    quoted,
)
from twake_space_agent_contracts.problems import Problem

# What TMail does with an email moved into spam or out of it: it reports it to the rspamd filter
# that all users share, as spam or as ham. That is for report_spam and report_not_spam, later
# contracts of high risk, and never for a move
SHARED_FILTER = "the spam filter that all users share"


def _special(trash: str) -> dict[str, str]:
    """The special mailboxes an email is not moved to, by role, and why: `trash` names the
    contract that puts emails in the trash, trash_email for one email, trash_emails for several."""
    return {
        **dict.fromkeys(
            ("drafts", "sent", "outbox", "templates"), "it holds what the user writes and sends"
        ),
        "trash": f"{trash} puts emails there",
        **dict.fromkeys(
            SPAM_ROLES,
            f"TMail would report it as spam to {SHARED_FILTER}, which takes report_spam, a"
            " high-risk contract not offered yet",
        ),
    }


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

    def mailbox(self, mailboxes: OwnMailboxes, trash: str) -> Mailbox:
        """The one of the user's own mailboxes it names, unless an email is not moved there:
        `trash` names the contract that puts emails in the trash instead."""
        match self.mailbox_id, self.mailbox_name:
            case str() as mailbox_id, None:
                mailbox = mailboxes.with_id(mailbox_id)
            case None, str() as name:
                mailbox = mailboxes.named(name)
            case _:
                raise AssertionError("A destination names exactly one mailbox, as validated")
        special = _special(trash)
        if mailbox.role in special:
            raise Problem(
                status=409,
                code="mailbox_forbidden",
                title="Mailbox forbidden",
                detail=f"An email is not moved to the {mailbox.role} mailbox: "
                f"{special[mailbox.role]}.",
            )
        return mailbox


class EmailsTo(Destination):
    """Emails of the user, and one of their own mailboxes: exactly one of mailbox_id and
    mailbox_name."""

    email_ids: EmailIds


Move = Literal["move", "archive", "trash"]

# What a preview of each move tells the owner, in each language
_MOVES: dict[Move, dict[Language, str]] = {
    "move": {
        "fr": "Déplacer {email} : il va dans le dossier {mailbox}",
        "en": "Move {email}: it goes to the folder {mailbox}",
    },
    "archive": {
        "fr": "Archiver {email} : il va dans le dossier {mailbox}",
        "en": "Archive {email}: it goes to the folder {mailbox}",
    },
    "trash": {
        "fr": "Mettre à la corbeille {email} : il va dans le dossier {mailbox}, d'où il peut être"
        " ressorti",
        "en": "Trash {email}: it goes to the folder {mailbox}, from which it can be moved back",
    },
}
_EMAIL: dict[Language, tuple[str, str, str]] = {
    "fr": ("le mail {subject}", "le mail sans objet", "{email} de {senders}"),
    "en": ("the email {subject}", "the email with no subject", "{email} from {senders}"),
}


def _summary(move: Move, placement: Placement, mailbox: Mailbox, language: Language) -> str:
    """What the move does, as the owner reads it: which email, by its subject and its senders,
    who wrote them, goes to which of their mailboxes."""
    titled, untitled, sent = _EMAIL[language]
    subject = one_line(placement.subject)
    email = titled.format(subject=quoted(subject, language)) if subject else untitled
    senders = people(placement.senders, len(placement.senders), language)
    if senders:
        email = sent.format(email=email, senders=senders)
    folder = quoted(one_line(mailbox.name), language)
    return _MOVES[move][language].format(email=email, mailbox=folder)


async def moved(
    tmail: TMail,
    user: User,
    placement: Placement,
    mailbox: Mailbox,
    move: Move,
    preview: Preview,
) -> Moved | JSONResponse:
    """Moves the email to the mailbox; or tells the owner what that would do, when the harness
    asks, and moves nothing."""
    # What the owner allows: that email, from where it is, to that mailbox
    digest = digest_of(placement.email_id, sorted(placement.mailbox_ids), mailbox.id, mailbox.name)
    if preview.asked:
        return preview.answer(_summary(move, placement, mailbox, preview.language), digest)
    preview.check(digest)
    return await tmail.move(user, placement, mailbox)


def _email_in_spam(trash: str) -> Problem:
    """An email in spam, which a move does not take out of it, since TMail would report it as ham:
    `trash` names the contract that can still put it in the trash, for one email or several."""
    return Problem(
        status=409,
        code="email_in_spam",
        title="Email in spam",
        detail=f"The email is in spam: TMail would report it as not spam to {SHARED_FILTER},"
        " which takes report_not_spam, a high-risk contract not offered yet, rather than a"
        f" move. {trash} can still put it in the trash, which reports nothing.",
    )


def out_of_spam(placement: Placement) -> None:
    """Refuses to move an email out of spam, which TMail would report as ham."""
    if placement.in_spam:
        raise _email_in_spam("trash_email")


IN_SPAM = _email_in_spam("trash_emails")
"""Why an email that several are moved with stays in spam."""


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
        response_model=Moved,
        # The email can be moved back: the owner's consent to write in Mail covers it. It tells
        # what it would do, for when the owner is asked.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def move_email(
        email_id: EmailId,
        destination: Destination,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> Moved | JSONResponse:
        placement = await tmail.placement(user, email_id)
        out_of_spam(placement)
        mailbox = destination.mailbox(placement, "trash_email")
        return await moved(tmail, user, placement, mailbox, "move", preview)

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
        response_model=Moved,
        # The email can be moved back: the owner's consent to write in Mail covers it. It tells
        # what it would do, for when the owner is asked.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def archive_email(
        email_id: EmailId, user: Annotated[User, Depends(caller)], preview: Previewing
    ) -> Moved | JSONResponse:
        placement = await tmail.placement(user, email_id)
        out_of_spam(placement)
        return await moved(tmail, user, placement, placement.archive(), "archive", preview)

    @routes.post(
        "/move",
        operation_id="move_emails",
        summary="Move several emails of the user to another of their mailboxes in one call",
        description=(
            "Moves several emails of the user you act for to another of their own mailboxes in "
            "one call, each out of the others it is in: to move several emails, "
            f"{in_one_call('move_email')}. Name the mailbox as move_email does, by mailbox_id or "
            "by mailbox_name: a name that several of their mailboxes have is refused, and you ask "
            "the user which one they mean. No email is moved to drafts, sent, outbox, templates, "
            "trash or spam: trash_emails puts emails in the trash. An email in spam is not taken "
            f"out of it, but refused with the code email_in_spam. {OUTCOMES} The emails can be "
            "moved back. Example, for two emails that list_emails gave with the ids "
            f"{EXAMPLE_ID} and {OTHER_ID}, to the user's mailbox named Projects: "
            f'body={{"email_ids": ["{EXAMPLE_ID}", "{OTHER_ID}"], "mailbox_name": "Projects"}}.'
        ),
        response_model=MovedEmails,
        # The emails can be moved back: the owner's consent to write in Mail covers it, as it
        # covers moving them one at a time
        openapi_extra={"x-twake-risk": "low"},
    )
    async def move_emails(emails: EmailsTo, user: Annotated[User, Depends(caller)]) -> MovedEmails:
        placements = await tmail.placements(user, emails.email_ids)
        mailbox = emails.mailbox(placements, "trash_emails")
        return await moved_emails(tmail, user, placements, emails.email_ids, mailbox, IN_SPAM)

    @routes.post(
        "/archive",
        operation_id="archive_emails",
        summary="Archive several emails of the user in one call",
        description=(
            "Moves several emails of the user you act for to their archive in one call, their "
            "mailbox whose role is archive, each out of the others it is in: to archive several "
            f"emails, {in_one_call('archive_email')}. A user without an archive is answered "
            "mailbox_not_found, and no email moves. An email in spam is not taken out of it, but "
            f"refused with the code email_in_spam. {OUTCOMES} The emails can be moved back with "
            "move_emails. Example, for two emails that list_emails gave with the ids "
            f"{EXAMPLE_ID} and {OTHER_ID}: "
            f'body={{"email_ids": ["{EXAMPLE_ID}", "{OTHER_ID}"]}}.'
        ),
        response_model=MovedEmails,
        # The emails can be moved back: the owner's consent to write in Mail covers it, as it
        # covers archiving them one at a time
        openapi_extra={"x-twake-risk": "low"},
    )
    async def archive_emails(emails: Emails, user: Annotated[User, Depends(caller)]) -> MovedEmails:
        placements = await tmail.placements(user, emails.email_ids)
        return await moved_emails(
            tmail, user, placements, emails.email_ids, placements.archive(), IN_SPAM
        )

    return routes
