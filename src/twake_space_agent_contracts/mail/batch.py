"""What the Mail contracts that move several emails of the user at once share: one call for up to
50 emails rather than one call per email, an answer that tells what became of each, and a preview
that names them."""

from collections import Counter
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.mail import Move, email_named
from twake_space_agent_contracts.mail.tmail import JMAP_ID, Mailbox, Placement, Placements, TMail
from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    Preview,
    digest_of,
    one_line,
    quoted,
    shown_size,
)
from twake_space_agent_contracts.problems import Problem

MOST_EMAILS = 50
"""How many emails a call moves at most: one Email/get reads them all and one Email/set moves
them, TMail taking 500 in each by default."""

SHOWN_EMAILS = 10
"""How many of the emails a preview names at most, as it names ten people of a header at most."""

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


def for_several(batch: str, single: str) -> str:
    """What the description of a move of one email tells the model to do to move several: one call
    of the batch for all of them, rather than one call per email."""
    return (
        f"For several emails, call {batch} once with all their ids, up to {MOST_EMAILS}, rather "
        f"than {single} once per email."
    )


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


@dataclass(frozen=True)
class _Words:
    """What a preview of several emails moved tells the owner, in one language: the words for one
    email, then those for several, which say how many."""

    verbs: dict[Move, str]
    moving: tuple[str, str]
    """The emails that go to the folder."""
    back: tuple[str, str]
    """That the emails put in the trash can be moved back."""
    none: str
    """That no email goes to the folder."""
    others: tuple[str, str]
    """How many of the emails that go to the folder the summary does not name."""
    there: tuple[str, str]
    not_found: tuple[str, str]
    in_spam: tuple[str, str]


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        verbs={"move": "Déplacer", "archive": "Archiver", "trash": "Mettre à la corbeille"},
        moving=(
            "{verb} 1 mail : il va dans le dossier {folder}",
            "{verb} {count} mails : ils vont dans le dossier {folder}",
        ),
        back=(", d'où il peut être ressorti", ", d'où ils peuvent être ressortis"),
        none="Aucun mail ne va dans le dossier {folder}",
        others=("- et 1 autre", "- et {count} autres"),
        there=("1 mail y est déjà", "{count} mails y sont déjà"),
        not_found=(
            "1 mail n'est pas dans tes dossiers",
            "{count} mails ne sont pas dans tes dossiers",
        ),
        in_spam=("1 mail est en spam : il y reste", "{count} mails sont en spam : ils y restent"),
    ),
    "en": _Words(
        verbs={"move": "Move", "archive": "Archive", "trash": "Trash"},
        moving=(
            "{verb} 1 email: it goes to the folder {folder}",
            "{verb} {count} emails: they go to the folder {folder}",
        ),
        back=(", from which it can be moved back", ", from which they can be moved back"),
        none="No email goes to the folder {folder}",
        others=("- and 1 other", "- and {count} others"),
        there=("1 email is there already", "{count} emails are there already"),
        not_found=("1 email is not in your folders", "{count} emails are not in your folders"),
        in_spam=(
            "1 email is in spam: it stays there",
            "{count} emails are in spam: they stay there",
        ),
    ),
}


def _counted(forms: tuple[str, str], count: int, **values: str) -> str:
    """The words for that many emails: those for one, or those for several, which say how many."""
    one, several = forms
    return (one if count == 1 else several).format(count=count, **values)


def _summary(
    move: Move,
    moving: list[Placement],
    left: dict[str, EmailOutcome],
    mailbox: Mailbox,
    language: Language,
) -> str:
    """What the call does, as the owner reads it: how many emails go to which of their mailboxes,
    each named on a line of its own by its subject and its senders, who wrote them, ten at most and
    as many as fit in the summary, then how many others; and how many stay where they are, and
    why."""
    words = _WORDS[language]
    folder = quoted(one_line(mailbox.name), language)
    if moving:
        head = _counted(words.moving, len(moving), verb=words.verbs[move], folder=folder)
        if move == "trash":
            head += _counted(words.back, len(moving))
    else:
        head = words.none.format(folder=folder)
    stay = Counter(outcome.outcome for outcome in left.values())
    # Why the others stay where they are: the only emails refused before the call are in spam
    why: list[tuple[Outcome, tuple[str, str]]] = [
        ("already_there", words.there),
        ("not_found", words.not_found),
        ("refused", words.in_spam),
    ]
    tail = [_counted(forms, stay[outcome]) for outcome, forms in why if stay[outcome]]

    def others(shown: int) -> list[str]:
        """The line that counts the emails that move but go unnamed, once some are named."""
        unnamed = len(moving) - shown
        return [_counted(words.others, unnamed)] if shown and unnamed else []

    named: list[str] = []
    for placement in moving[:SHOWN_EMAILS]:
        line = f"- {email_named(placement, language)}"
        if shown_size("\n".join([head, *named, line, *others(len(named) + 1), *tail])) > BUDGET:
            break
        named.append(line)
    return "\n".join([head, *named, *others(len(named)), *tail])


def _digest(placements: Placements, email_ids: list[str], mailbox: Mailbox) -> str:
    """The digest of what the call acts on: each of the emails, in the order given, the user's
    own mailboxes it is in, sorted, or null when it is not found, and the mailbox they go to."""
    where = [
        [email_id, sorted(found.mailbox_ids) if (found := placements.found.get(email_id)) else None]
        for email_id in email_ids
    ]
    return digest_of(where, mailbox.id, mailbox.name)


async def moved_emails(
    tmail: TMail,
    user: User,
    placements: Placements,
    email_ids: list[str],
    mailbox: Mailbox,
    move: Move,
    preview: Preview,
    in_spam: Problem | None = None,
) -> MovedEmails | JSONResponse:
    """Moves the emails to the mailbox, each out of the user's other mailboxes, all those that
    have to move with one Email/set, and tells what became of each of them, none left out; or
    tells the owner what that would do, when the harness asks, and moves nothing. A move that
    keeps emails in spam refuses each email there with the problem `in_spam`."""
    moving, outcomes = _sorted_out(placements, email_ids, mailbox, in_spam)
    # What the owner allows: those emails, from where each of them is, to that mailbox
    digest = _digest(placements, email_ids, mailbox)
    if preview.asked:
        return preview.answer(_summary(move, moving, outcomes, mailbox, preview.language), digest)
    preview.check(digest)
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
