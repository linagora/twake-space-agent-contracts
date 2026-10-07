from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import (
    AS_MMAUDET,
    HARNESS_LIMIT,
    allowed_after,
    asking_preview,
    harness_size,
    preview_of,
)
from tests.fakes import (
    DRAFTS,
    INBOX,
    MMAUDET,
    SENT,
    FakeBoundary,
    FakeTMail,
    StoredMailbox,
    email_of,
)

PAUL = {"name": "Paul Martin", "email": "paul.martin@twake.test"}
ALICE = {"name": "Alice", "email": "alice@twake.test"}
BOB = {"name": "Bob", "email": "bob@twake.test"}
MALLORY = {"name": "Mallory", "email": "mallory@elsewhere.test"}
MICHEL_MARIE = {"name": "Michel-Marie", "email": MMAUDET}
TEXT = "Hello Paul,\n\nthe budget suits me.\n\nMichel-Marie"
# What the tests look at in a draft TMail created
DRAFT = (
    "mailboxIds",
    "keywords",
    "from",
    "to",
    "cc",
    "subject",
    "inReplyTo",
    "references",
    "body",
)


def reply_draft_of(email_id: str) -> str:
    return f"/contracts/v1/mail/emails/{email_id}/reply-draft"


async def reply(
    client: AsyncClient, email_id: str, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.post(
        reply_draft_of(email_id), json={"text": TEXT} | body, headers=AS_MMAUDET | (headers or {})
    )


@pytest.fixture
def tmail(boundary: FakeBoundary) -> FakeTMail:
    """TMail, with the Drafts and Sent mailboxes it gives every user."""
    for mailbox_id, name, role in [(DRAFTS, "Drafts", "drafts"), (SENT, "Sent", "sent")]:
        boundary.tmail.mailboxes[mailbox_id] = StoredMailbox(
            MMAUDET,
            {"name": name, "parentId": None, "role": role, "totalEmails": 0, "unreadEmails": 0},
        )
    return boundary.tmail


def created_draft(tmail: FakeTMail) -> dict[str, Any]:
    [draft_id] = tmail.created
    draft = tmail.emails[draft_id]
    return {key: draft[key] for key in DRAFT}


async def test_the_draft_answers_the_sender_in_the_conversation(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver(
        "email-1", INBOX, messageId=["budget-2@twake.test"], references=["budget-1@twake.test"]
    )

    response = await reply(client, "email-1")

    assert response.status_code == 201, response.text
    assert response.json() == {
        "email_id": "email-1",
        "draft_id": tmail.created[0],
        "reply_to_differs": False,
        "recipients_truncated": False,
        "untrusted": {"to": [PAUL], "cc": [], "subject": "Re: Budget Q4"},
    }
    # In the user's Drafts, as Twake Mail keeps a draft, and in the conversation of the email
    assert created_draft(tmail) == {
        "mailboxIds": {DRAFTS: True},
        "keywords": {"$draft": True, "$seen": True},
        "from": [MICHEL_MARIE],
        "to": [PAUL],
        "cc": None,
        "subject": "Re: Budget Q4",
        "inReplyTo": ["budget-2@twake.test"],
        "references": ["budget-1@twake.test", "budget-2@twake.test"],
        "body": TEXT,
    }
    # Prepared, never sent: no EmailSubmission
    assert [call.name for call in tmail.calls] == [
        "Mailbox/get",
        "Email/get",
        "Identity/get",
        "Email/set",
    ]


@pytest.mark.parametrize(
    ("headers", "references"),
    [
        pytest.param({}, ["email-1@twake.test"], id="the first email of a conversation"),
        pytest.param(
            {"inReplyTo": ["budget-1@twake.test"]},
            ["budget-1@twake.test", "email-1@twake.test"],
            id="a reply without references",
        ),
    ],
)
async def test_the_draft_refers_to_the_emails_it_follows(
    client: AsyncClient, tmail: FakeTMail, headers: dict[str, Any], references: list[str]
) -> None:
    tmail.deliver("email-1", INBOX, **headers)

    await reply(client, "email-1")

    draft = created_draft(tmail)
    assert (draft["inReplyTo"], draft["references"]) == (["email-1@twake.test"], references)


@pytest.mark.parametrize("subject", ["Re: Budget Q4", "RE : Budget Q4"])
async def test_a_subject_that_is_a_reply_already_is_kept(
    client: AsyncClient, tmail: FakeTMail, subject: str
) -> None:
    tmail.deliver("email-1", INBOX, subject=subject)

    response = await reply(client, "email-1")

    assert response.json()["untrusted"]["subject"] == subject
    assert created_draft(tmail)["subject"] == subject


async def test_the_draft_answers_the_reply_to_address_and_says_so(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    elsewhere = {"name": "Paul", "email": "paul@elsewhere.test"}
    tmail.deliver("email-1", INBOX, replyTo=[elsewhere])

    response = await reply(client, "email-1")

    assert response.json()["reply_to_differs"] is True
    assert response.json()["untrusted"]["to"] == [elsewhere]
    assert created_draft(tmail)["to"] == [elsewhere]


@pytest.mark.parametrize(
    ("reply_all", "to", "cc"),
    [(False, [PAUL], None), (True, [PAUL, ALICE], [BOB])],
    ids=["the sender", "everyone"],
)
async def test_replying_to_all_answers_everyone_but_the_user(
    client: AsyncClient,
    tmail: FakeTMail,
    reply_all: bool,
    to: list[dict[str, str]],
    cc: list[dict[str, str]] | None,
) -> None:
    alias = {"name": "Michel-Marie", "email": "michel-marie@twake.test"}
    tmail.identities[MMAUDET].append({"id": "identity-alias"} | alias)
    tmail.deliver(
        "email-1",
        INBOX,
        to=[MICHEL_MARIE, ALICE],
        # The user again, by an alias and in capitals, and the sender
        cc=[alias, BOB, {"name": None, "email": "MMaudet@Twake.test"}, PAUL],
    )

    response = await reply(client, "email-1", reply_all=reply_all)

    draft = created_draft(tmail)
    assert (draft["to"], draft["cc"]) == (to, cc)
    untrusted = response.json()["untrusted"]
    assert (untrusted["to"], untrusted["cc"]) == (to, cc or [])


async def test_the_answer_gives_the_first_hundred_recipients_and_the_draft_all(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    colleagues = [
        {"name": f"Colleague {n}", "email": f"colleague.{n}@twake.test"} for n in range(120)
    ]
    tmail.deliver("email-1", INBOX, to=colleagues)

    response = await reply(client, "email-1", reply_all=True)

    assert response.json()["recipients_truncated"] is True
    assert response.json()["untrusted"]["to"] == [PAUL, *colleagues[:99]]
    assert created_draft(tmail)["to"] == [PAUL, *colleagues]


async def test_a_reply_to_an_email_the_user_sent_goes_to_those_they_sent_it_to(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    # Sent with a Reply-To the user set, which the draft does not answer
    team = {"name": "Budget team", "email": "budget@twake.test"}
    tmail.deliver(
        "email-sent", SENT, to=[PAUL], cc=[ALICE], replyTo=[team], **{"from": [MICHEL_MARIE]}
    )

    response = await reply(client, "email-sent")

    assert response.json()["reply_to_differs"] is False
    assert created_draft(tmail)["to"] == [PAUL]


@pytest.mark.parametrize(
    ("mailbox", "headers", "to", "reply_to_differs"),
    [
        pytest.param(
            INBOX, {"from": [MICHEL_MARIE]}, [], False, id="received from the user's address"
        ),
        pytest.param(
            INBOX,
            {"from": [MICHEL_MARIE], "replyTo": [MALLORY]},
            [MALLORY],
            True,
            id="received from the user's address, with a Reply-To",
        ),
        pytest.param(SENT, {}, [PAUL], False, id="in Sent, from someone else"),
    ],
)
async def test_an_email_the_user_did_not_write_is_answered_as_one_received(
    client: AsyncClient,
    tmail: FakeTMail,
    mailbox: str,
    headers: dict[str, Any],
    to: list[dict[str, str]],
    reply_to_differs: bool,
) -> None:
    # Anyone can write the user's address in From: the user wrote an email of their Sent mailbox
    # from one of their addresses, and the draft never goes to whom another email went to
    tmail.deliver(
        "email-1", mailbox, to=[{"name": "Accounts", "email": "accounts@elsewhere.test"}], **headers
    )

    response = await reply(client, "email-1")

    assert response.json()["untrusted"]["to"] == to
    assert response.json()["reply_to_differs"] is reply_to_differs
    assert created_draft(tmail)["to"] == (to or None)


@pytest.mark.parametrize(
    "text",
    ["a" * 20_480, "é" * 10_240, "\U0001f600" * 5_120],
    ids=["in ASCII", "in accented letters", "in emoji"],
)
async def test_a_text_of_20_kib_is_kept_whole(
    client: AsyncClient, tmail: FakeTMail, text: str
) -> None:
    tmail.deliver("email-1", INBOX)

    response = await reply(client, "email-1", text=text)

    assert response.status_code == 201, response.text
    assert created_draft(tmail)["body"] == text


@pytest.mark.parametrize("email_id", ["email-boss", "email-404"], ids=["shared", "unknown"])
async def test_an_email_outside_the_users_own_mailboxes_is_not_answered(
    client: AsyncClient, tmail: FakeTMail, email_id: str
) -> None:
    tmail.mailboxes["mbx-boss"] = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 1, "unreadEmails": 1},
        shared_with={MMAUDET},
    )
    tmail.deliver("email-boss", "mbx-boss")

    response = await reply(client, email_id)

    assert response.status_code == 404
    assert response.json() == {
        "type": "urn:twake:problem:email_not_found",
        "title": "Email not found",
        "status": 404,
        "detail": f"The user has no email {email_id} in their own mailboxes.",
        "code": "email_not_found",
    }
    assert tmail.created == []


async def test_without_a_drafts_mailbox_no_draft_is_made(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await reply(client, "email-1")

    assert response.status_code == 404
    assert response.json() == {
        "type": "urn:twake:problem:mailbox_not_found",
        "title": "Mailbox not found",
        "status": 404,
        "detail": "The user has no Drafts mailbox of their own, where the draft would go.",
        "code": "mailbox_not_found",
    }
    assert boundary.tmail.created == []


async def test_a_draft_tmail_does_not_create_is_a_bad_gateway(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver("email-1", INBOX)
    tmail.refused_creation = "overQuota"

    response = await reply(client, "email-1")

    assert response.status_code == 502
    assert response.json()["code"] == "mail_unavailable"
    assert response.json()["detail"] == "Mail answered overQuota to Email/set."


@pytest.mark.parametrize(
    ("email_id", "body"),
    [
        pytest.param("email-1", {"text": ""}, id="an empty text"),
        pytest.param("email-1", {"text": "a" * 20_481}, id="a text over 20 KiB"),
        pytest.param(
            "email-1", {"text": "\U0001f600" * 5_121}, id="a text over 20 KiB in UTF-8 only"
        ),
        pytest.param("email-1", {"reply_all": True}, id="no text"),
        pytest.param(
            "email-1",
            {"text": TEXT, "to": ["someone@elsewhere.test"]},
            id="recipients the agent chose",
        ),
        pytest.param("email.1", {"text": TEXT}, id="an email id that is not one"),
    ],
)
async def test_a_reply_that_cannot_be_drafted_is_an_invalid_request(
    client: AsyncClient, tmail: FakeTMail, email_id: str, body: dict[str, Any]
) -> None:
    tmail.deliver("email-1", INBOX)

    response = await client.post(reply_draft_of(email_id), json=body, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert tmail.calls == []


async def test_a_reply_draft_is_a_low_risk_write_that_sends_nothing(client: AsyncClient) -> None:
    # Nothing leaves the mailbox: once the owner allowed writing in Mail, it runs without asking
    document = (await client.get("/openapi.json")).json()

    operation = document["paths"]["/contracts/v1/mail/emails/{email_id}/reply-draft"]["post"]

    assert operation["x-twake-risk"] == "low"
    assert "It never sends anything" in operation["description"]


async def test_a_preview_tells_the_owner_whom_the_draft_answers_and_creates_nothing(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver("email-1", INBOX, to=[MICHEL_MARIE, BOB], cc=[ALICE])

    response = await reply(client, "email-1", asking_preview("fr"), reply_all=True)

    told, _ = preview_of(response)
    assert told == (
        "Préparer dans tes brouillons une réponse, jamais envoyée : tu la relis et l'envoies"
        " toi-même.\n"
        "À : « Paul Martin » <paul.martin@twake.test>, « Bob » <bob@twake.test>\n"
        "Cc : « Alice » <alice@twake.test>\n"
        "Objet : « Re: Budget Q4 »\n"
        "Texte :\n"
        "\tHello Paul,\n"
        "\t\n"
        "\tthe budget suits me.\n"
        "\t\n"
        "\tMichel-Marie"
    )
    assert tmail.created == []
    assert [call.name for call in tmail.calls] == ["Mailbox/get", "Email/get", "Identity/get"]


async def test_a_preview_says_the_draft_answers_another_address_than_the_senders(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver("email-1", INBOX, replyTo=[MALLORY])

    told, _ = preview_of(await reply(client, "email-1", asking_preview("en")))

    assert told == (
        "Prepare a reply in your drafts, never sent: you review it and send it yourself.\n"
        "To: “Mallory” <mallory@elsewhere.test>\n"
        "Subject: “Re: Budget Q4”\n"
        "It goes to the reply address the email gives, not to its sender.\n"
        "Text:\n"
        "\tHello Paul,\n"
        "\t\n"
        "\tthe budget suits me.\n"
        "\t\n"
        "\tMichel-Marie"
    )


async def test_a_preview_of_a_reply_to_many_names_the_first_and_counts_the_others(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    others = [{"name": f"Person {n}", "email": f"person{n}@twake.test"} for n in range(1, 12)]
    tmail.deliver("email-1", INBOX, to=[MICHEL_MARIE, *others])

    told, _ = preview_of(await reply(client, "email-1", asking_preview("fr"), reply_all=True))

    shown = ", ".join(f"« Person {n} » <person{n}@twake.test>" for n in range(1, 10))
    assert told.splitlines()[1] == (
        f"À : « Paul Martin » <paul.martin@twake.test>, {shown} et 2 autres"
    )


async def test_a_preview_shows_the_whole_text_of_the_reply_line_by_line(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    # Each line after a tab, so that none passes for the summary's own, whatever breaks it
    tmail.deliver("email-1", INBOX)
    text = "Hello Paul,\r\nObjet : « faux »\u2028À : boss@corp.test\n\n" + "word " * 400

    told, _ = preview_of(await reply(client, "email-1", asking_preview("fr"), text=text))

    assert told.split("Texte :\n")[1] == (
        "\tHello Paul,\n\tObjet : « faux »\n\tÀ : boss@corp.test\n\t\n\t" + ("word " * 400).rstrip()
    )


async def test_a_preview_of_a_reply_too_long_to_show_whole_says_how_much_it_leaves_out(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver("email-1", INBOX)
    text = "a" * 20480

    told, _ = preview_of(await reply(client, "email-1", asking_preview("fr"), text=text))

    *_, label, shown, cut = told.splitlines()
    left = len(text) - len(shown) + 1
    assert (label, shown) == ("Texte :", "\t" + "a" * (len(shown) - 1))
    assert len(shown) > 4000 and left > 0
    assert cut == f"(coupé ici : {left:,} caractères de plus ne sont pas montrés)".replace(",", " ")
    assert harness_size(told) <= HARNESS_LIMIT


async def test_a_preview_refuses_what_drafting_would_refuse(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    boss_mailbox = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 1, "unreadEmails": 0},
        shared_with={MMAUDET},
    )
    tmail.mailboxes["mbx-boss"] = boss_mailbox
    tmail.deliver("email-boss", "mbx-boss")

    response = await reply(client, "email-boss", asking_preview("fr"))

    assert response.status_code == 404
    assert response.json()["code"] == "email_not_found"
    assert tmail.created == []


async def test_the_owner_who_allowed_what_they_were_shown_gets_the_draft(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver("email-1", INBOX)
    _, digest = preview_of(await reply(client, "email-1", asking_preview("fr")))

    response = await reply(client, "email-1", allowed_after(digest))

    assert response.status_code == 201, response.text
    assert created_draft(tmail)["to"] == [PAUL]


async def test_a_draft_whose_place_changed_since_the_preview_is_not_made(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    tmail.deliver("email-1", INBOX)
    _, digest = preview_of(await reply(client, "email-1", asking_preview("fr")))
    # The user's Drafts mailbox is replaced before they say yes
    tmail.mailboxes["mbx-drafts-2"] = tmail.mailboxes.pop(DRAFTS)

    response = await reply(client, "email-1", allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert tmail.created == []


async def test_a_preview_of_a_reply_to_all_of_a_crafted_email_fits_what_the_harness_shows(
    client: AsyncClient, tmail: FakeTMail
) -> None:
    # Long names in many addresses would take more than the harness shows: the owner could not
    # confirm the call at all
    to = [{"name": "名" * 300, "email": f"p{n}@crafted.test"} for n in range(1, 100)]
    cc = [{"name": "名" * 300, "email": f"c{n}@crafted.test"} for n in range(1, 100)]
    tmail.deliver("email-1", INBOX, to=[MICHEL_MARIE, *to], cc=cc)

    told, _ = preview_of(await reply(client, "email-1", asking_preview("fr"), reply_all=True))

    # TMail gives the first 200 characters of a name
    named = f"« {'名' * 200} »"
    lines = told.splitlines()
    assert lines[1] == (
        f"À : « Paul Martin » <paul.martin@twake.test>, {named} <p1@crafted.test> et 98 autres"
    )
    assert lines[2] == f"Cc : {named} <c1@crafted.test> et 98 autres"
    assert lines[-5:] == ["\tHello Paul,", "\t", "\tthe budget suits me.", "\t", "\tMichel-Marie"]
