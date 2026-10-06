from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import (
    INBOX,
    MMAUDET,
    TRASH,
    FakeBoundary,
    MethodCall,
    StoredMailbox,
    email_of,
)

PAUL = {"name": "Paul Martin", "email": "paul.martin@twake.test"}


def thread_of(thread_id: str) -> str:
    return f"/contracts/v1/mail/threads/{thread_id}"


def ids(answer: dict[str, Any]) -> list[str]:
    return [email["id"] for email in answer["emails"]]


def texts_fetched(boundary: FakeBoundary) -> list[MethodCall]:
    """The calls of Email/get that asked TMail for the text of emails."""
    return [
        call
        for call in boundary.tmail.calls
        if call.name == "Email/get" and call.arguments.get("fetchTextBodyValues")
    ]


def share_a_mailbox_with_mmaudet(boundary: FakeBoundary) -> None:
    boundary.tmail.mailboxes["mbx-boss"] = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 1, "unreadEmails": 1},
        shared_with={MMAUDET},
    )


async def test_the_user_reads_a_conversation_oldest_first(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    share_a_mailbox_with_mmaudet(boundary)
    tmail = boundary.tmail
    tmail.deliver("email-reply", TRASH, threadId="thread-a", receivedAt="2026-10-06T08:00:00Z")
    tmail.deliver("email-first", INBOX, threadId="thread-a", receivedAt="2026-10-05T08:00:00Z")
    # In a mailbox shared with the user: not theirs to read
    tmail.deliver("email-boss", "mbx-boss", threadId="thread-a", receivedAt="2026-10-05T12:00:00Z")

    response = await client.get(thread_of("thread-a"), headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["thread_id"] == "thread-a"
    assert ids(answer) == ["email-first", "email-reply"]
    assert answer["emails"][0] == {
        "id": "email-first",
        "thread_id": "thread-a",
        "mailbox_ids": [INBOX],
        "received_at": "2026-10-05T08:00:00Z",
        "unread": True,
        "flagged": False,
        "has_attachment": False,
        "external_sender": False,
        "reply_to_differs": False,
        "body_truncated": False,
        "body_unreadable": False,
        "recipients_truncated": False,
        "untrusted": {
            "from": [PAUL],
            "to": [{"name": "Michel-Marie", "email": MMAUDET}],
            "cc": [],
            "reply_to": [],
            "subject": "Budget Q4",
            "body": "Hello,\n\nhere is the budget.",
        },
    }


async def test_a_long_conversation_gives_its_last_emails(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    for day in range(1, 6):
        boundary.tmail.deliver(
            f"email-{day}", INBOX, threadId="thread-a", receivedAt=f"2026-10-0{day}T08:00:00Z"
        )

    response = await client.get(thread_of("thread-a"), params={"limit": 2}, headers=AS_MMAUDET)

    assert ids(response.json()) == ["email-4", "email-5"]
    [get] = texts_fetched(boundary)
    assert get.arguments["ids"] == ["email-4", "email-5"]
    assert get.arguments["maxBodyValueBytes"] == 8_192


async def test_the_last_emails_of_a_conversation_are_the_users_own(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    share_a_mailbox_with_mmaudet(boundary)
    tmail = boundary.tmail
    tmail.deliver("email-1", INBOX, threadId="thread-a", receivedAt="2026-10-05T08:00:00Z")
    tmail.deliver("email-2", INBOX, threadId="thread-a", receivedAt="2026-10-05T09:00:00Z")
    # The newest, in a mailbox shared with the user
    tmail.deliver("email-boss", "mbx-boss", threadId="thread-a", receivedAt="2026-10-05T10:00:00Z")

    last = await client.get(thread_of("thread-a"), params={"limit": 1}, headers=AS_MMAUDET)
    both = await client.get(thread_of("thread-a"), params={"limit": 2}, headers=AS_MMAUDET)

    assert ids(last.json()) == ["email-2"]
    assert ids(both.json()) == ["email-1", "email-2"]
    # The text of an email that is not the user's own is never fetched
    assert [get.arguments["ids"] for get in texts_fetched(boundary)] == [
        ["email-2"],
        ["email-1", "email-2"],
    ]


@pytest.mark.parametrize("thread_id", ["thread-boss", "thread-unknown"], ids=["shared", "unknown"])
async def test_a_conversation_outside_the_users_own_mailboxes_is_not_found(
    client: AsyncClient, boundary: FakeBoundary, thread_id: str
) -> None:
    share_a_mailbox_with_mmaudet(boundary)
    boundary.tmail.deliver("email-boss", "mbx-boss", threadId="thread-boss")

    response = await client.get(thread_of(thread_id), headers=AS_MMAUDET)

    assert response.status_code == 404
    assert response.json() == {
        "type": "urn:twake:problem:thread_not_found",
        "title": "Conversation not found",
        "status": 404,
        "detail": f"The user has no conversation {thread_id} in their own mailboxes.",
        "code": "thread_not_found",
    }


@pytest.mark.parametrize("limit", [0, 21])
async def test_a_number_of_emails_out_of_bounds_is_an_invalid_request(
    client: AsyncClient, limit: int
) -> None:
    response = await client.get(thread_of("thread-a"), params={"limit": limit}, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
