"""What the Mail contracts that move several emails of the user at once share: one call for up to
50 emails rather than one call per email, and an answer that tells what became of each."""

from collections import Counter
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.mail.tmail import JMAP_ID, Mailbox, Placement, Placements, TMail
from twake_space_agent_contracts.problems import Problem

MOST_EMAILS = 50
"""How many emails a call moves at most: one Email/get reads them all and one Email/set moves
them, TMail taking 500 in each by default."""

OTHER_ID = "1b6e4d20-a2b1-11f0-8de9-0242ac120002"
"""The id of another email, for the worked calls."""


def _once(email_ids: list[str]) -> list[str]:
    """The emails, each once, in the order given: an email given twice is moved once."""
    return list(dict.fromkeys(email_ids))


EmailIds = Annotated[
    list[Annotated[str, StringConstraints(pattern=JMAP_ID)]],
    Field(
        min_length=1,
        max_length=MOST_EMAILS,
        description="The ids of the emails, as list_emails or search_emails gives them: 1 to "
        f"{MOST_EMAILS}.",
    ),
    AfterValidator(_once),
]
"""The emails a call moves, each once, in the order given."""


class Emails(BaseModel):
    """Emails of the user."""

    model_config = ConfigDict(extra="forbid")

    email_ids: EmailIds


Outcome = Literal["moved", "already_there", "not_found", "refused"]


class EmailOutcome(BaseModel):
    """What became of one of the emails."""

    email_id: str
    outcome: Outcome
    code: str | None = None
    """Why an email was refused, as the code of a problem; null for the others."""
    detail: str | None = None
    """What the agent does about an email refused; null for the others."""


class Counts(BaseModel):
    """How many of the emails had each outcome."""

    moved: int
    already_there: int
    not_found: int
    refused: int


class MovedEmails(BaseModel):
    """Emails of the user, the one of their own mailboxes they go to, and what became of each."""

    mailbox_id: str
    mailbox_name: str
    counts: Counts
    emails: list[EmailOutcome]
    """In the order the call gave them."""


OUTCOMES = (
    "emails tells what became of each email, in the order given: moved; already_there, when it "
    "was in that mailbox only; not_found, when the user has no such email in their own mailboxes; "
    "or refused, with the code and the detail of why. counts sums them up: tell the user of each "
    "email not moved."
)
"""What a description tells the model of the answer of a call that moves several emails."""


def in_one_call(single: str) -> str:
    """What a description tells the model to do to move several emails: one call for all of them,
    rather than one call of the contract that moves a single email per email."""
    return (
        f"call it once with all their ids, up to {MOST_EMAILS}, rather than {single} once per "
        f"email; beyond {MOST_EMAILS}, call it again for the others"
    )


def _sorted_out(
    placements: Placements, email_ids: list[str], mailbox: Mailbox, in_spam: Problem | None
) -> tuple[list[Placement], dict[str, EmailOutcome]]:
    """The emails that move to the mailbox, and what becomes of the others, by id: an email in
    that mailbox alone is there already, and one not found, or in spam for a move that keeps
    emails there, stays where it is."""
    moving: list[Placement] = []
    left: dict[str, EmailOutcome] = {}
    for email_id in email_ids:
        placement = placements.found.get(email_id)
        if placement is None:
            left[email_id] = EmailOutcome(email_id=email_id, outcome="not_found")
        elif in_spam is not None and placement.in_spam:
            left[email_id] = EmailOutcome(
                email_id=email_id, outcome="refused", code=in_spam.code, detail=in_spam.detail
            )
        elif placement.mailbox_ids == [mailbox.id]:
            left[email_id] = EmailOutcome(email_id=email_id, outcome="already_there")
        else:
            moving.append(placement)
    return moving, left


def _written(email_id: str, refused: str | None) -> EmailOutcome:
    """What became of an email Email/set was to move, from the type of the error TMail answered
    for it, if any."""
    if refused is None:
        return EmailOutcome(email_id=email_id, outcome="moved")
    # Gone since it was read
    if refused == "notFound":
        return EmailOutcome(email_id=email_id, outcome="not_found")
    return EmailOutcome(
        email_id=email_id,
        outcome="refused",
        code="mail_unavailable",
        detail=f"Mail answered {refused} to Email/set.",
    )


async def moved_emails(
    tmail: TMail,
    user: User,
    placements: Placements,
    email_ids: list[str],
    mailbox: Mailbox,
    in_spam: Problem | None = None,
) -> MovedEmails:
    """Moves the emails to the mailbox, each out of the user's other mailboxes, all those that
    have to move with one Email/set, and tells what became of each of them, none left out. A move
    that keeps emails in spam refuses each email there with the problem `in_spam`."""
    moving, outcomes = _sorted_out(placements, email_ids, mailbox, in_spam)
    refusals = await tmail.move_all(user, moving, mailbox) if moving else {}
    for placement in moving:
        outcomes[placement.email_id] = _written(
            placement.email_id, refusals.get(placement.email_id)
        )
    emails = [outcomes[email_id] for email_id in email_ids]
    counted = Counter(email.outcome for email in emails)
    return MovedEmails(
        mailbox_id=mailbox.id,
        mailbox_name=mailbox.name,
        counts=Counts(
            moved=counted["moved"],
            already_there=counted["already_there"],
            not_found=counted["not_found"],
            refused=counted["refused"],
        ),
        emails=emails,
    )
