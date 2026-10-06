import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import (
    INBOX,
    MMAUDET,
    SPAM,
    TRASH,
    FakeBoundary,
    FakeClock,
    StoredMailbox,
    account_of,
    email_of,
)

MAILBOXES = "/contracts/v1/mail/mailboxes"


async def test_the_mailbox_of_another_account_is_never_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # TMail opened the account of another address than the token's subject
    boundary.tmail.usernames[MMAUDET] = email_of("someone.else")

    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "mail_account_mismatch"
    assert boundary.tmail.calls == []


async def test_an_account_delegated_to_the_user_is_never_read(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The boss delegated their account to the user: the session lists it first
    boundary.tmail.delegations[MMAUDET] = [email_of("boss")]
    boundary.tmail.mailboxes["mbx-boss-inbox"] = StoredMailbox(
        email_of("boss"),
        {"name": "INBOX", "parentId": None, "role": "inbox", "totalEmails": 9, "unreadEmails": 9},
    )

    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert [mailbox["id"] for mailbox in response.json()["mailboxes"]] == [INBOX, TRASH, SPAM]
    assert {call.arguments["accountId"] for call in boundary.tmail.calls} == {account_of(MMAUDET)}


async def test_the_session_is_kept_five_minutes_at_most(
    client: AsyncClient, boundary: FakeBoundary, clock: FakeClock
) -> None:
    await client.get(MAILBOXES, headers=AS_MMAUDET)
    clock.now += 299
    await client.get(MAILBOXES, headers=AS_MMAUDET)
    assert boundary.tmail.sessions == 1

    clock.now += 2
    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert response.status_code == 200
    assert boundary.tmail.sessions == 2


async def test_a_token_tmail_refuses_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.refused_tokens = True

    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "mail_refused"


@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        ("down", "Mail answered 503 to GET /jmap/session."),
        ("method error", "Mail answered serverFail to Mailbox/get."),
    ],
)
async def test_an_unavailable_tmail_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary, failure: str, detail: str
) -> None:
    if failure == "down":
        boundary.tmail.down = True
    else:
        boundary.tmail.failing_method = "Mailbox/get"

    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert response.status_code == 502
    assert response.json()["code"] == "mail_unavailable"
    assert response.json()["detail"] == detail
