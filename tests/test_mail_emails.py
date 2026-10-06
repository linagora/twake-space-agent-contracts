from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import INBOX, MMAUDET, SPAM, TRASH, FakeBoundary, StoredMailbox, email_of

EMAILS = "/contracts/v1/mail/emails"
SEARCH = "/contracts/v1/mail/search"


def listed(email_id: str, **fields: Any) -> dict[str, Any]:
    """An email delivered with the fake's defaults, as the contracts list it, with these fields."""
    return {
        "id": email_id,
        "thread_id": f"thread-{email_id}",
        "mailbox_ids": [INBOX],
        "received_at": "2026-10-06T09:00:00Z",
        "unread": True,
        "flagged": False,
        "has_attachment": False,
        "untrusted": {
            "from": [{"name": "Paul Martin", "email": "paul.martin@twake.test"}],
            "subject": "Budget Q4",
            "preview": "Hello, here is the budget",
        },
    } | fields


def ids(answer: dict[str, Any]) -> list[str]:
    return [email["id"] for email in answer["emails"]]


async def emails(client: AsyncClient, path: str = EMAILS, **params: Any) -> dict[str, Any]:
    response = await client.get(path, params=params, headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


def share_a_mailbox_with_mmaudet(boundary: FakeBoundary) -> None:
    boundary.tmail.mailboxes["mbx-boss"] = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 1, "unreadEmails": 1},
        shared_with={MMAUDET},
    )


async def test_the_user_lists_their_mail_newest_first(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-old", INBOX, receivedAt="2026-10-05T08:00:00Z")
    boundary.tmail.deliver(
        "email-new",
        INBOX,
        receivedAt="2026-10-06T08:00:00Z",
        keywords={"$seen": True, "$flagged": True},
        hasAttachment=True,
    )

    answer = await emails(client)

    assert answer == {
        "emails": [
            listed(
                "email-new",
                received_at="2026-10-06T08:00:00Z",
                unread=False,
                flagged=True,
                has_attachment=True,
            ),
            listed("email-old", received_at="2026-10-05T08:00:00Z"),
        ],
        "next_cursor": None,
    }


async def test_spam_and_trash_are_left_out_unless_asked_for(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-inbox", INBOX)
    boundary.tmail.deliver("email-trash", TRASH)
    boundary.tmail.deliver("email-spam", SPAM)

    assert ids(await emails(client)) == ["email-inbox"]
    assert ids(await emails(client, mailbox=TRASH)) == ["email-trash"]
    assert ids(await emails(client, mailbox=SPAM)) == ["email-spam"]


async def test_the_list_keeps_what_is_asked_for(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    flagged = {"$flagged": True}
    tmail = boundary.tmail
    tmail.deliver("email-wanted", INBOX, receivedAt="2026-10-05T10:00:00Z", keywords=flagged)
    tmail.deliver("email-elsewhere", TRASH, receivedAt="2026-10-05T10:00:00Z", keywords=flagged)
    tmail.deliver(
        "email-read",
        INBOX,
        receivedAt="2026-10-05T10:00:00Z",
        keywords={"$seen": True, "$flagged": True},
    )
    tmail.deliver("email-unflagged", INBOX, receivedAt="2026-10-05T10:00:00Z")
    tmail.deliver(
        "email-from-alice",
        INBOX,
        receivedAt="2026-10-05T10:00:00Z",
        keywords=flagged,
        **{"from": [{"name": "Alice", "email": "alice@twake.test"}]},
    )
    tmail.deliver("email-too-early", INBOX, receivedAt="2026-10-05T05:00:00Z", keywords=flagged)
    tmail.deliver("email-too-late", INBOX, receivedAt="2026-10-06T23:00:00Z", keywords=flagged)

    answer = await emails(
        client,
        mailbox=INBOX,
        unread="true",
        flagged="true",
        **{"from": "paul"},
        after="2026-10-05T08:00:00+02:00",
        before="2026-10-07T00:00:00+02:00",
    )

    assert ids(answer) == ["email-wanted"]
    # One condition: James refuses mailboxes inside a filter operator
    [query] = [call for call in tmail.calls if call.name == "Email/query"]
    assert query.arguments["filter"] == {
        "inMailbox": INBOX,
        "notKeyword": "$seen",
        "hasKeyword": "$flagged",
        "from": "paul",
        "after": "2026-10-05T06:00:00Z",
        "before": "2026-10-06T22:00:00Z",
    }


async def test_the_next_emails_follow_the_cursor(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    for day in (3, 4, 5):
        boundary.tmail.deliver(f"email-{day}", INBOX, receivedAt=f"2026-10-0{day}T08:00:00Z")

    first = await emails(client, limit=2)
    rest = await emails(client, limit=2, cursor=first["next_cursor"])

    assert ids(first) == ["email-5", "email-4"]
    assert ids(rest) == ["email-3"]
    assert rest["next_cursor"] is None


async def test_the_list_goes_no_further_than_five_hundred_emails(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    for minute in range(501):
        received_at = f"2026-10-06T{minute // 60:02}:{minute % 60:02}:00Z"
        boundary.tmail.deliver(f"email-{minute}", INBOX, receivedAt=received_at)

    seen: list[str] = []
    cursor: dict[str, str] = {}
    for _ in range(5):
        page = await emails(client, limit=100, **cursor)
        seen += ids(page)
        cursor = {"cursor": page["next_cursor"]} if page["next_cursor"] else {}

    assert len(set(seen)) == 500
    assert cursor == {}


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"limit": 0}, id="limit 0"),
        pytest.param({"limit": 101}, id="limit 101"),
        pytest.param({"cursor": "not-a-cursor"}, id="a cursor the contract did not give"),
        pytest.param({"from": "p"}, id="a sender of one character"),
        pytest.param({"mailbox": "mbx/inbox"}, id="a mailbox that is not an id"),
        pytest.param({"after": "2026-10-05T08:00:00"}, id="a time without offset"),
        pytest.param(
            {"after": "2026-10-06T00:00:00+02:00", "before": "2026-10-05T00:00:00+02:00"},
            id="before earlier than after",
        ),
    ],
)
async def test_a_list_that_cannot_be_read_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, params: dict[str, Any]
) -> None:
    response = await client.get(EMAILS, params=params, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tmail.calls == []


@pytest.mark.parametrize("mailbox", ["mbx-boss", "mbx-unknown"], ids=["shared", "unknown"])
async def test_a_mailbox_that_is_not_the_users_own_is_not_found(
    client: AsyncClient, boundary: FakeBoundary, mailbox: str
) -> None:
    share_a_mailbox_with_mmaudet(boundary)
    boundary.tmail.deliver("email-boss", "mbx-boss")

    response = await client.get(EMAILS, params={"mailbox": mailbox}, headers=AS_MMAUDET)

    assert response.status_code == 404
    assert response.json() == {
        "type": "urn:twake:problem:mailbox_not_found",
        "title": "Mailbox not found",
        "status": 404,
        "detail": f"The user has no mailbox {mailbox} of their own.",
        "code": "mailbox_not_found",
    }


@pytest.mark.parametrize(
    ("path", "params"), [(EMAILS, {}), (SEARCH, {"text": "budget"})], ids=["list", "search"]
)
async def test_an_email_outside_the_users_own_mailboxes_is_never_listed(
    client: AsyncClient, boundary: FakeBoundary, path: str, params: dict[str, str]
) -> None:
    share_a_mailbox_with_mmaudet(boundary)
    boundary.tmail.deliver("email-own", INBOX)
    boundary.tmail.deliver("email-boss", "mbx-boss")
    # Should TMail ever search the mailboxes shared with the user without being asked to
    boundary.tmail.searches_shared = True

    assert ids(await emails(client, path, **params)) == ["email-own"]


async def test_the_user_searches_their_mail_by_words(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    other = {"subject": "Lunch", "preview": "See you", "body": "See you at noon."}
    boundary.tmail.deliver("email-budget", INBOX)
    boundary.tmail.deliver("email-lunch", INBOX, **other)
    boundary.tmail.deliver("email-budget-spam", SPAM, subject="Budget offer")

    answer = await emails(client, SEARCH, text="budget")
    in_spam = await emails(client, SEARCH, text="budget", mailbox=SPAM)

    assert answer == {"emails": [listed("email-budget")], "next_cursor": None}
    assert ids(in_spam) == ["email-budget-spam"]
    query = next(call for call in boundary.tmail.calls if call.name == "Email/query")
    assert query.arguments["filter"] == {"text": "budget", "inMailboxOtherThan": [TRASH, SPAM]}


@pytest.mark.parametrize("text", ["b", "b" * 201], ids=["one character", "201 characters"])
async def test_search_words_out_of_bounds_are_an_invalid_request(
    client: AsyncClient, text: str
) -> None:
    response = await client.get(SEARCH, params={"text": text}, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
