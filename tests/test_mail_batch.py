from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET
from tests.fakes import INBOX, SPAM, TRASH, FakeBoundary
from tests.test_mail_move import (
    ARCHIVE,
    BOSS,
    EMAILS,
    NO_SINGLE_MAILBOX,
    PROJECTS,
    WRITTEN,
    mailboxes_of,
    own_mailbox,
    problem,
    writes,
)

# The mailboxes the moves of one email are tested with, arranged for each test here too
from tests.test_mail_move import mailboxes as mailboxes
from tests.test_openapi import refusal

BATCHES = [
    pytest.param("move", {"mailbox_id": PROJECTS}, PROJECTS, "Projects", id="move"),
    pytest.param("archive", {}, ARCHIVE, "Archive", id="archive"),
    pytest.param("trash", {}, TRASH, "Trash", id="trash"),
]
"""Each batched move: its operation, what its body names besides the emails, and the mailbox the
emails go to, with its name."""
OUT_OF_SPAM = BATCHES[:2]
"""The batched moves that take no email out of spam."""

IN_SPAM = (
    "The email is in spam: TMail would report it as not spam to the spam filter that all users"
    " share, which takes report_not_spam, a high-risk contract not offered yet, rather than a"
    " move. trash_emails can still put it in the trash, which reports nothing."
)


async def post(
    client: AsyncClient, operation: str, body: Any, headers: dict[str, str] | None = None
) -> Response:
    return await client.post(
        f"{EMAILS}/{operation}", json=body, headers=AS_MMAUDET | (headers or {})
    )


def outcome(
    email_id: str, what: str, code: str | None = None, detail: str | None = None
) -> dict[str, Any]:
    """What became of one of the emails, as the answer gives it."""
    return {"email_id": email_id, "outcome": what, "code": code, "detail": detail}


def counted(
    moved: int = 0, already_there: int = 0, not_found: int = 0, refused: int = 0
) -> dict[str, int]:
    return {
        "moved": moved,
        "already_there": already_there,
        "not_found": not_found,
        "refused": refused,
    }


async def body_schema(client: AsyncClient, operation: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The schema of a batched move's body, and the document it is in."""
    document: dict[str, Any] = (await client.get("/openapi.json")).json()
    found = document["paths"][f"{EMAILS}/{operation}"]["post"]
    return found["requestBody"]["content"]["application/json"]["schema"], document


@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_the_user_moves_several_emails_in_one_call(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    emails = ["email-1", "email-2", "email-3"]
    for email_id in emails:
        boundary.tmail.deliver(email_id, INBOX)

    response = await post(client, operation, {"email_ids": emails} | body)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "mailbox_id": mailbox_id,
        "mailbox_name": name,
        "counts": counted(moved=3),
        "emails": [outcome(email_id, "moved") for email_id in emails],
    }
    assert [mailboxes_of(boundary, email_id) for email_id in emails] == [{mailbox_id}] * 3
    # One request reads them all, and one Email/set moves them all
    assert [call.name for call in boundary.tmail.calls] == ["Mailbox/get", "Email/get", "Email/set"]
    [write] = writes(boundary)
    assert sorted(write.arguments["update"]) == emails
    # Moved, never destroyed: the user can move them back
    assert set(write.arguments) == {"accountId", "update"}


async def test_fifty_emails_are_read_and_moved_in_one_request_each(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    emails = [f"email-{number}" for number in range(1, 51)]
    for email_id in emails:
        boundary.tmail.deliver(email_id, INBOX)

    response = await post(client, "trash", {"email_ids": emails})

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(moved=50)
    assert [call.name for call in boundary.tmail.calls] == ["Mailbox/get", "Email/get", "Email/set"]


@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_each_email_is_answered_with_what_became_of_it(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    boundary.tmail.deliver("email-1", INBOX)
    boundary.tmail.deliver("email-there", mailbox_id)
    # TMail gives the email of a mailbox shared with the user, whatever the capabilities
    boundary.tmail.deliver("email-boss", BOSS)
    emails = ["email-1", "email-there", "email-boss", "email-unknown"]

    response = await post(client, operation, {"email_ids": emails} | body)

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(moved=1, already_there=1, not_found=2)
    # In the order the call gave them
    assert response.json()["emails"] == [
        outcome("email-1", "moved"),
        outcome("email-there", "already_there"),
        outcome("email-boss", "not_found"),
        outcome("email-unknown", "not_found"),
    ]
    # Only the email that had to move is written
    [write] = writes(boundary)
    assert list(write.arguments["update"]) == ["email-1"]
    assert mailboxes_of(boundary, "email-boss") == {BOSS}


@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_nothing_is_written_when_no_email_has_to_move(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    boundary.tmail.deliver("email-there", mailbox_id)

    response = await post(client, operation, {"email_ids": ["email-there", "email-gone"]} | body)

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(already_there=1, not_found=1)
    assert writes(boundary) == []


async def test_an_email_there_and_in_another_mailbox_leaves_the_other(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX, mailboxIds={INBOX: True, ARCHIVE: True})

    response = await post(client, "archive", {"email_ids": ["email-1"]})

    assert response.json()["emails"] == [outcome("email-1", "moved")]
    assert mailboxes_of(boundary, "email-1") == {ARCHIVE}


@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_a_mailbox_shared_with_the_user_keeps_its_emails(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    # The email is in the boss's mailbox too: only the user's own mailboxes are theirs to change
    boundary.tmail.deliver("email-1", INBOX, mailboxIds={INBOX: True, BOSS: True})

    response = await post(client, operation, {"email_ids": ["email-1"]} | body)

    assert response.status_code == 200, response.text
    assert mailboxes_of(boundary, "email-1") == {mailbox_id, BOSS}


async def test_an_email_given_twice_is_moved_once(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "trash", {"email_ids": ["email-1", "email-1"]})

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(moved=1)
    assert response.json()["emails"] == [outcome("email-1", "moved")]


async def test_an_email_tmail_does_not_move_is_answered_with_why(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    for email_id in ("email-1", "email-2", "email-3"):
        boundary.tmail.deliver(email_id, INBOX)
    # TMail refuses one, and finds another gone since the contract read it
    boundary.tmail.refused_updates = {"email-2": "forbidden", "email-3": "notFound"}

    response = await post(client, "trash", {"email_ids": ["email-1", "email-2", "email-3"]})

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(moved=1, not_found=1, refused=1)
    assert response.json()["emails"] == [
        outcome("email-1", "moved"),
        outcome("email-2", "refused", "mail_unavailable", "Mail answered forbidden to Email/set."),
        outcome("email-3", "not_found"),
    ]
    assert mailboxes_of(boundary, "email-2") == {INBOX}


async def test_a_batch_tmail_fails_to_write_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.failing_method = "Email/set"
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "archive", {"email_ids": ["email-1"]})

    assert response.status_code == 502
    assert response.json()["code"] == "mail_unavailable"
    assert response.json()["detail"] == "Mail answered serverFail to Email/set."


async def test_the_trash_takes_emails_out_of_spam_too(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", SPAM)
    boundary.tmail.deliver("email-2", INBOX)

    response = await post(client, "trash", {"email_ids": ["email-1", "email-2"]})

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(moved=2)
    assert mailboxes_of(boundary, "email-1") == {TRASH}


@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), OUT_OF_SPAM)
async def test_an_email_in_spam_is_refused_and_stays_there(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    # TMail would report it as ham to the filter all users share: report_not_spam's, not a move's
    boundary.tmail.deliver("email-1", INBOX)
    boundary.tmail.deliver("email-spam", SPAM)

    response = await post(client, operation, {"email_ids": ["email-1", "email-spam"]} | body)

    assert response.status_code == 200, response.text
    assert response.json()["counts"] == counted(moved=1, refused=1)
    assert response.json()["emails"] == [
        outcome("email-1", "moved"),
        outcome("email-spam", "refused", "email_in_spam", IN_SPAM),
    ]
    assert mailboxes_of(boundary, "email-spam") == {SPAM}
    [write] = writes(boundary)
    assert list(write.arguments["update"]) == ["email-1"]


async def test_a_batched_move_finds_the_mailbox_by_its_name_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "move", {"email_ids": ["email-1"], "mailbox_name": "projects"})

    assert response.status_code == 200, response.text
    assert response.json()["mailbox_id"] == PROJECTS
    assert mailboxes_of(boundary, "email-1") == {PROJECTS}


@pytest.mark.parametrize(
    ("destination", "detail"),
    [
        pytest.param(
            {"mailbox_id": "mbx-unknown"},
            "The user has no mailbox mbx-unknown of their own.",
            id="unknown",
        ),
        pytest.param(
            {"mailbox_id": BOSS}, "The user has no mailbox mbx-boss of their own.", id="shared"
        ),
        pytest.param(
            {"mailbox_name": "Boss"},
            "The user has no mailbox of their own named Boss.",
            id="shared, by its name",
        ),
    ],
)
async def test_no_email_moves_to_a_mailbox_that_is_not_the_users_own(
    client: AsyncClient, boundary: FakeBoundary, destination: dict[str, str], detail: str
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "move", {"email_ids": ["email-1"]} | destination)

    assert response.status_code == 404
    assert response.json() == problem("mailbox_not_found", "Mailbox not found", 404, detail)
    assert writes(boundary) == []


async def test_a_name_several_mailboxes_have_moves_none_of_the_emails(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes["mbx-old-projects"] = own_mailbox("Projects", parent_id=ARCHIVE)
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "move", {"email_ids": ["email-1"], "mailbox_name": "Projects"})

    assert response.status_code == 409
    # The agent goes on with the batched move, not with one move per email
    assert response.json() == problem(
        "mailbox_ambiguous",
        "Ambiguous mailbox",
        409,
        "Several of the user's mailboxes are named Projects: mbx-projects, mbx-old-projects. Ask"
        " the user which one they mean, then move the emails there with move_emails and its"
        " mailbox_id.",
    )
    assert writes(boundary) == []


@pytest.mark.parametrize(
    ("mailbox_id", "why"),
    [
        pytest.param("mbx-drafts", WRITTEN, id="drafts"),
        pytest.param("mbx-sent", WRITTEN, id="sent"),
        pytest.param("mbx-outbox", WRITTEN, id="outbox"),
        pytest.param("mbx-templates", WRITTEN, id="templates"),
        pytest.param(TRASH, "trash_emails puts emails there", id="trash"),
        # TMail would report them as spam to the filter all users share: report_spam's
        pytest.param(SPAM, "which takes report_spam, a high-risk contract", id="spam"),
    ],
)
async def test_no_email_moves_to_a_special_mailbox(
    client: AsyncClient, boundary: FakeBoundary, mailbox_id: str, why: str
) -> None:
    for role in ("drafts", "sent", "outbox", "templates"):
        boundary.tmail.mailboxes[f"mbx-{role}"] = own_mailbox(role.title(), role)
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "move", {"email_ids": ["email-1"], "mailbox_id": mailbox_id})

    assert response.status_code == 409
    assert response.json()["code"] == "mailbox_forbidden"
    assert why in response.json()["detail"]
    assert writes(boundary) == []


@pytest.mark.parametrize(
    ("operation", "mailbox_id", "role"),
    [
        pytest.param("archive", ARCHIVE, "archive", id="archive"),
        pytest.param("trash", TRASH, "trash", id="trash"),
    ],
)
async def test_without_the_mailbox_of_its_role_no_email_moves(
    client: AsyncClient, boundary: FakeBoundary, operation: str, mailbox_id: str, role: str
) -> None:
    del boundary.tmail.mailboxes[mailbox_id]
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, operation, {"email_ids": ["email-1"]})

    assert response.status_code == 404
    assert response.json() == problem(
        "mailbox_not_found", "Mailbox not found", 404, f"The user has no {role} mailbox."
    )
    assert writes(boundary) == []


async def test_two_archives_move_none_of_the_emails(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes["mbx-archive-2"] = own_mailbox("Archive 2", "archive")
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "archive", {"email_ids": ["email-1"]})

    assert response.status_code == 409
    assert response.json() == problem(
        "mailbox_ambiguous",
        "Ambiguous mailbox",
        409,
        "Several of the user's mailboxes have the role archive: mbx-archive, mbx-archive-2. Ask"
        " the user which one they mean, then move the emails there with move_emails and its"
        " mailbox_id.",
    )
    assert writes(boundary) == []


async def test_two_trash_mailboxes_are_left_to_the_user(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes["mbx-trash-2"] = own_mailbox("Trash 2", "trash")
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "trash", {"email_ids": ["email-1"]})

    assert response.status_code == 409
    assert response.json() == problem(
        "trash_ambiguous",
        "Several trash mailboxes",
        409,
        "Several of the user's mailboxes have the role trash: mbx-trash, mbx-trash-2. No"
        " contract chooses among them: ask the user to keep a single trash folder in Twake Mail,"
        " then put the emails in the trash again.",
    )
    assert writes(boundary) == []


NOT_ONE_TO_FIFTY_IDS = [
    pytest.param({}, id="no emails"),
    pytest.param({"email_ids": []}, id="an empty list"),
    pytest.param({"email_ids": [f"email-{number}" for number in range(51)]}, id="51 emails"),
    pytest.param({"email_ids": ["email.1"]}, id="an email id that is not one"),
    pytest.param({"email_ids": "email-1"}, id="an email id, not a list"),
    pytest.param({"email_ids": ["email-1"], "mailbox": "INBOX"}, id="a field it does not take"),
]
"""Bodies that do not name 1 to 50 emails by valid ids, and only them."""


@pytest.mark.parametrize("emails", NOT_ONE_TO_FIFTY_IDS)
@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_a_batch_without_1_to_50_valid_ids_is_an_invalid_request(
    client: AsyncClient,
    boundary: FakeBoundary,
    emails: dict[str, Any],
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    response = await post(client, operation, emails | body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tmail.calls == []


@pytest.mark.parametrize("emails", NOT_ONE_TO_FIFTY_IDS)
@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_the_gateway_refuses_a_batch_without_1_to_50_valid_ids(
    client: AsyncClient,
    emails: dict[str, Any],
    operation: str,
    body: dict[str, str],
    mailbox_id: str,
    name: str,
) -> None:
    # The gateway checks each body against the document, before the service sees it
    schema, document = await body_schema(client, operation)

    assert refusal(emails | body, schema, document) is not None


@pytest.mark.parametrize(("operation", "body", "mailbox_id", "name"), BATCHES)
async def test_the_gateway_takes_up_to_50_emails(
    client: AsyncClient, operation: str, body: dict[str, str], mailbox_id: str, name: str
) -> None:
    schema, document = await body_schema(client, operation)
    emails = [f"email-{number}" for number in range(50)]

    assert refusal({"email_ids": emails} | body, schema, document) is None
    assert refusal({"email_ids": emails[:1]} | body, schema, document) is None


@pytest.mark.parametrize("destination", NO_SINGLE_MAILBOX)
async def test_a_batched_move_that_names_no_single_mailbox_is_refused(
    client: AsyncClient, boundary: FakeBoundary, destination: dict[str, Any]
) -> None:
    boundary.tmail.deliver("email-1", INBOX)
    body = {"email_ids": ["email-1"]} | destination

    response = await post(client, "move", body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tmail.calls == []
    schema, document = await body_schema(client, "move")
    assert refusal(body, schema, document) is not None


@pytest.mark.parametrize("operation", ["move", "archive", "trash"])
async def test_a_batch_without_a_body_names_the_body(
    client: AsyncClient, boundary: FakeBoundary, operation: str
) -> None:
    response = await post(client, operation, None)

    assert response.json() == problem(
        "invalid_request", "Invalid request", 400, "body: Field required"
    )
    assert boundary.tmail.calls == []
