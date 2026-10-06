from typing import Any

from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import INBOX, MMAUDET, FakeBoundary, StoredMailbox, email_of

PAUL = {"name": "Paul Martin", "email": "paul.martin@twake.test"}
# Invisible format characters too: a soft hyphen, an Arabic letter mark, a word joiner, an
# invisible plus and tags; and a surrogate left alone, which no text can hold
INVISIBLE = "".join(map(chr, [0x00AD, 0x061C, 0x2060, 0x2064, 0xE0001, 0xE0041, 0xE007F, 0xD800]))


async def read(client: AsyncClient, email_id: str) -> dict[str, Any]:
    response = await client.get(f"/contracts/v1/mail/emails/{email_id}", headers=AS_MMAUDET)
    assert response.status_code == 200, response.text
    email: dict[str, Any] = response.json()
    return email


async def test_the_user_reads_an_email_as_text(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.tmail.deliver(
        "email-1", INBOX, cc=[{"name": "Alice", "email": "alice@twake.test"}], replyTo=[PAUL]
    )

    email = await read(client, "email-1")

    assert email == {
        "id": "email-1",
        "thread_id": "thread-email-1",
        "mailbox_ids": [INBOX],
        "received_at": "2026-10-06T09:00:00Z",
        "unread": True,
        "flagged": False,
        "has_attachment": False,
        "external_sender": False,
        "reply_to_differs": False,
        "body_truncated": False,
        "body_unreadable": False,
        "untrusted": {
            "from": [PAUL],
            "to": [{"name": "Michel-Marie", "email": MMAUDET}],
            "cc": [{"name": "Alice", "email": "alice@twake.test"}],
            "reply_to": [PAUL],
            "subject": "Budget Q4",
            "body": "Hello,\n\nhere is the budget.",
        },
    }
    # Reading leaves the email as it was: unread
    assert [call.name for call in boundary.tmail.calls] == ["Mailbox/get", "Email/get"]


async def test_a_sender_from_elsewhere_is_flagged(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    outsider = {"name": "Paul Martin", "email": "paul.martin@elsewhere.test"}
    boundary.tmail.deliver("email-outside", INBOX, **{"from": [outsider]})
    boundary.tmail.deliver(
        "email-reply-elsewhere",
        INBOX,
        replyTo=[{"name": "Paul", "email": "paul@elsewhere.test"}],
    )

    outside = await read(client, "email-outside")
    reply_elsewhere = await read(client, "email-reply-elsewhere")

    assert (outside["external_sender"], outside["reply_to_differs"]) == (True, False)
    assert (reply_elsewhere["external_sender"], reply_elsewhere["reply_to_differs"]) == (
        False,
        True,
    )


async def test_what_others_wrote_loses_its_hidden_characters(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver(
        "email-1",
        INBOX,
        subject=f"{INVISIBLE}Budget\u202e Q4\u200b",
        preview=f"Hello,{INVISIBLE}\u2066 here is\nthe budget",
        body=f"Hello,{INVISIBLE}\u200b\r\n\r\n\r\n\r\n  here   is\tthe budget.\u0000\ufeff",
        **{"from": [{"name": f"Paul{INVISIBLE}\u2067 Martin", "email": "paul.martin@twake.test"}]},
    )

    email = await read(client, "email-1")
    listed = (await client.get("/contracts/v1/mail/emails", headers=AS_MMAUDET)).json()["emails"][0]

    assert email["untrusted"]["subject"] == "Budget Q4"
    assert email["untrusted"]["body"] == "Hello,\n\nhere is the budget."
    assert email["untrusted"]["from"] == [PAUL]
    assert listed["untrusted"] == {
        "from": [PAUL],
        "subject": "Budget Q4",
        "preview": "Hello, here is the budget",
    }


async def test_a_long_body_is_cut(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.tmail.deliver("email-1", INBOX, body="budget " * 6000)

    email = await read(client, "email-1")

    assert email["body_truncated"] is True
    assert 32_000 < len(email["untrusted"]["body"]) <= 32_768
    [get] = [call for call in boundary.tmail.calls if call.name == "Email/get"]
    assert get.arguments["maxBodyValueBytes"] == 32_768


async def test_a_text_tmail_could_not_decode_is_said_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX, encodingProblem=True)

    email = await read(client, "email-1")

    assert email["body_unreadable"] is True


async def test_an_email_outside_the_users_own_mailboxes_is_not_found(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # TMail gives the email of a mailbox shared with the user, whatever the capabilities
    boundary.tmail.mailboxes["mbx-boss"] = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 1, "unreadEmails": 1},
        shared_with={MMAUDET},
    )
    boundary.tmail.deliver("email-boss", "mbx-boss")

    shared = await client.get("/contracts/v1/mail/emails/email-boss", headers=AS_MMAUDET)
    unknown = await client.get("/contracts/v1/mail/emails/email-404", headers=AS_MMAUDET)

    assert shared.status_code == 404
    assert shared.json() == {
        "type": "urn:twake:problem:email_not_found",
        "title": "Email not found",
        "status": 404,
        "detail": "The user has no email email-boss in their own mailboxes.",
        "code": "email_not_found",
    }
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "The user has no email email-404 in their own mailboxes."


async def test_an_id_that_is_not_one_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await client.get("/contracts/v1/mail/emails/email.1", headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tmail.calls == []
