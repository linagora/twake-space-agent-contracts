"""mail.emails.read.v1: the user's emails, listed, searched and read, from their own mailboxes."""

import base64
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime, BaseModel

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import UNTRUSTED
from twake_space_agent_contracts.mail.tmail import (
    JMAP_ID,
    Email,
    EmailSummary,
    Search,
    TMail,
)
from twake_space_agent_contracts.problems import Problem, invalid_request

LONGEST_LIST = 500
"""How far a list goes, cursor after cursor."""

PAGES = (
    "An answer holds at most limit emails. When its next_cursor is not null, more follow: call "
    "again with the same parameters and cursor set to next_cursor, up to "
    f"{LONGEST_LIST} emails."
)

OwnMailbox = Annotated[
    str | None,
    Query(
        pattern=JMAP_ID,
        description="The id of one of the user's mailboxes, as list_mailboxes gives it. Without "
        "it, all their mailboxes but trash and spam.",
    ),
]
After = Annotated[
    AwareDatetime | None,
    Query(
        description="Keep the emails received at this time or later, an RFC 3339 time with its "
        "offset, such as 2026-10-05T00:00:00+02:00."
    ),
]
Before = Annotated[
    AwareDatetime | None,
    Query(
        description="Keep the emails received before this time, an RFC 3339 time with its "
        "offset, such as 2026-10-06T00:00:00+02:00."
    ),
]
Limit = Annotated[int, Query(ge=1, le=100, description="How many emails to return, 20 by default.")]
Cursor = Annotated[
    str | None,
    Query(
        max_length=100,
        description="The next_cursor of the previous answer, as it is, to read the emails that "
        "follow.",
    ),
]


class EmailList(BaseModel):
    emails: list[EmailSummary]
    next_cursor: str | None


def _not_a_cursor() -> Problem:
    return invalid_request("cursor: pass the next_cursor of a previous answer, as it is.")


def _position(cursor: str | None) -> int:
    """Where a list goes on from, as the cursor a previous answer gave says."""
    if cursor is None:
        return 0
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        position = json.loads(base64.urlsafe_b64decode(padded))["position"]
    except (ValueError, KeyError, TypeError) as error:
        raise _not_a_cursor() from error
    if not isinstance(position, int) or not 0 < position < LONGEST_LIST:
        raise _not_a_cursor()
    return position


def _cursor(position: int) -> str:
    """An opaque cursor, which lets the contract change how it goes on without the agents
    knowing."""
    return (
        base64.urlsafe_b64encode(json.dumps({"position": position}).encode()).decode().rstrip("=")
    )


def router(tmail: TMail, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/mail", tags=["mail.emails.read.v1"])

    async def page(user: User, search: Search, limit: int, cursor: str | None) -> EmailList:
        if search.after and search.before and search.before <= search.after:
            raise invalid_request("before must be later than after.")
        position = _position(cursor)
        emails, full = await tmail.emails(user, search, position, limit)
        following = position + limit
        more = full and following < LONGEST_LIST
        return EmailList(emails=emails, next_cursor=_cursor(following) if more else None)

    @routes.get(
        "/emails",
        operation_id="list_emails",
        summary="List the user's emails, newest first",
        description=(
            "Lists the emails of the user you act for, newest first, from their own mailboxes "
            "only. Without mailbox, the emails in trash and spam are left out. unread, flagged, "
            f"from, after and before keep the emails that match them all. {PAGES} {UNTRUSTED} "
            "Example, for the unread emails Paul Martin sent to the inbox since Monday 5 October "
            "2026 in Paris, the inbox having the id 8f2d3c4b-1a5e-4f60-9b7c-2d1e0f3a4b5c in "
            "list_mailboxes: mailbox=8f2d3c4b-1a5e-4f60-9b7c-2d1e0f3a4b5c, unread=true, "
            "from=paul.martin@example.com, after=2026-10-05T00:00:00+02:00."
        ),
    )
    async def list_emails(
        user: Annotated[User, Depends(caller)],
        mailbox: OwnMailbox = None,
        unread: Annotated[
            bool, Query(description="true keeps the emails the user has not read yet.")
        ] = False,
        flagged: Annotated[bool, Query(description="true keeps the flagged emails.")] = False,
        sender: Annotated[
            str | None,
            Query(
                alias="from",
                min_length=2,
                max_length=200,
                description="Keep the emails from senders whose address or name holds this "
                "text, such as paul.martin@example.com or Paul.",
            ),
        ] = None,
        after: After = None,
        before: Before = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> EmailList:
        search = Search(
            mailbox=mailbox,
            sender=sender,
            unread=unread,
            flagged=flagged,
            after=after,
            before=before,
        )
        return await page(user, search, limit, cursor)

    @routes.get(
        "/search",
        operation_id="search_emails",
        summary="Search the user's emails by words",
        description=(
            "Searches the emails of the user you act for, from their own mailboxes only, for "
            "words in their addresses, subject, text and attachments, newest first. Without "
            "mailbox, trash and spam are left out. after and before keep the emails received in "
            f"a period. {PAGES} {UNTRUSTED} Example, for the emails about the budget received "
            "in September 2026, in Paris: text=budget, after=2026-09-01T00:00:00+02:00, "
            "before=2026-10-01T00:00:00+02:00."
        ),
    )
    async def search_emails(
        user: Annotated[User, Depends(caller)],
        text: Annotated[
            str,
            Query(
                min_length=2,
                max_length=200,
                description="The words to look for, such as budget or Paul Martin.",
            ),
        ],
        mailbox: OwnMailbox = None,
        after: After = None,
        before: Before = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> EmailList:
        search = Search(mailbox=mailbox, text=text, after=after, before=before)
        return await page(user, search, limit, cursor)

    @routes.get(
        "/emails/{email_id}",
        operation_id="read_email",
        summary="Read one email of the user",
        description=(
            "Reads one email of the user you act for, from their own mailboxes only, as text: "
            "who it is from and to, its subject and its text, cut after 32 KiB when "
            "body_truncated is true. body_unreadable is true when TMail could not decode the "
            "text, which may then read wrong. external_sender is true when it is from an address "
            "outside the user's domain, and reply_to_differs when a reply would go to another "
            "address than the one it is from. Reading does not mark the email as read. "
            f"{UNTRUSTED} Example, for an email that list_emails gave with the id "
            "0f9c7a50-a2b1-11f0-8de9-0242ac120002: "
            "email_id=0f9c7a50-a2b1-11f0-8de9-0242ac120002."
        ),
    )
    async def read_email(
        email_id: Annotated[
            str,
            Path(
                pattern=JMAP_ID,
                description="The id of the email, as list_emails or search_emails gives it.",
            ),
        ],
        user: Annotated[User, Depends(caller)],
    ) -> Email:
        return await tmail.email(user, email_id)

    return routes
