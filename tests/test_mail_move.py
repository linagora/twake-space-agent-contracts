from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import (
    INBOX,
    MMAUDET,
    SPAM,
    TRASH,
    FakeBoundary,
    MethodCall,
    StoredMailbox,
    email_of,
)
from tests.test_openapi import refusal

EMAILS = "/contracts/v1/mail/emails"
ARCHIVE = "mbx-archive"
PROJECTS = "mbx-projects"
BOSS = "mbx-boss"


def own_mailbox(name: str, role: str | None = None, parent_id: str | None = None) -> StoredMailbox:
    return StoredMailbox(
        MMAUDET,
        {"name": name, "parentId": parent_id, "role": role, "totalEmails": 0, "unreadEmails": 0},
    )


@pytest.fixture(autouse=True)
def mailboxes(boundary: FakeBoundary) -> None:
    """Besides those TMail creates, mmaudet's archive and a mailbox in their inbox, and the boss's
    mailbox, shared with them."""
    boundary.tmail.mailboxes[ARCHIVE] = own_mailbox("Archive", "archive")
    boundary.tmail.mailboxes[PROJECTS] = own_mailbox("Projects", parent_id=INBOX)
    boundary.tmail.mailboxes[BOSS] = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 1, "unreadEmails": 1},
        shared_with={MMAUDET},
    )


async def post(
    client: AsyncClient,
    email_id: str,
    operation: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    return await client.post(
        f"{EMAILS}/{email_id}/{operation}", json=body, headers=AS_MMAUDET | (headers or {})
    )


async def ask(
    client: AsyncClient,
    email_id: str,
    operation: str,
    body: dict[str, Any] | None = None,
    language: str = "fr",
) -> Response:
    """The harness asks what a move would do, before it asks the owner."""
    return await post(client, email_id, operation, body, asking_preview(language))


def mailboxes_of(boundary: FakeBoundary, email_id: str) -> set[str]:
    return set(boundary.tmail.emails[email_id]["mailboxIds"])


def writes(boundary: FakeBoundary) -> list[MethodCall]:
    return [call for call in boundary.tmail.calls if call.name == "Email/set"]


def problem(code: str, title: str, status: int, detail: str) -> dict[str, Any]:
    return {
        "type": f"urn:twake:problem:{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
    }


async def test_the_user_moves_an_email_to_one_of_their_mailboxes(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "move", {"mailbox_id": PROJECTS})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "email_id": "email-1",
        "mailbox_id": PROJECTS,
        "mailbox_name": "Projects",
    }
    assert mailboxes_of(boundary, "email-1") == {PROJECTS}
    # The method calls the contract needs, and no other
    calls = [call.name for call in boundary.tmail.calls]
    assert calls == ["Mailbox/get", "Email/get", "Email/set"]


async def test_a_mailbox_is_found_by_its_name_whatever_its_case(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "move", {"mailbox_name": "projects"})

    assert response.status_code == 200, response.text
    assert mailboxes_of(boundary, "email-1") == {PROJECTS}


async def test_a_name_several_mailboxes_have_is_refused_rather_than_guessed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes["mbx-old-projects"] = own_mailbox("Projects", parent_id=ARCHIVE)
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "move", {"mailbox_name": "Projects"})

    assert response.status_code == 409
    assert response.json() == problem(
        "mailbox_ambiguous",
        "Ambiguous mailbox",
        409,
        "Several of the user's mailboxes are named Projects: mbx-projects, mbx-old-projects. Ask"
        " the user which one they mean, then move the email there with move_email and its"
        " mailbox_id.",
    )
    assert writes(boundary) == []


@pytest.mark.parametrize(
    ("body", "detail"),
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
async def test_a_mailbox_that_is_not_the_users_own_is_not_found(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, str], detail: str
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "move", body)

    assert response.status_code == 404
    assert response.json() == problem("mailbox_not_found", "Mailbox not found", 404, detail)
    assert writes(boundary) == []


@pytest.mark.parametrize("email_id", ["email-boss", "email-unknown"], ids=["shared", "unknown"])
@pytest.mark.parametrize(
    ("operation", "body"),
    [
        pytest.param("move", {"mailbox_id": PROJECTS}, id="move"),
        pytest.param("archive", None, id="archive"),
        pytest.param("trash", None, id="trash"),
    ],
)
async def test_an_email_outside_the_users_own_mailboxes_is_not_found(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, str] | None,
    email_id: str,
) -> None:
    # TMail gives the email of a mailbox shared with the user, whatever the capabilities
    boundary.tmail.deliver("email-boss", BOSS)

    response = await post(client, email_id, operation, body)

    assert response.status_code == 404
    assert response.json() == problem(
        "email_not_found",
        "Email not found",
        404,
        f"The user has no email {email_id} in their own mailboxes.",
    )
    assert writes(boundary) == []


WRITTEN = "it holds what the user writes and sends"


@pytest.mark.parametrize(
    ("mailbox_id", "why"),
    [
        pytest.param("mbx-drafts", WRITTEN, id="drafts"),
        pytest.param("mbx-sent", WRITTEN, id="sent"),
        pytest.param("mbx-outbox", WRITTEN, id="outbox"),
        pytest.param("mbx-templates", WRITTEN, id="templates"),
        pytest.param(TRASH, "trash_email puts emails there", id="trash"),
        # TMail would report it as spam to the filter all users share: report_spam's, not a move's
        pytest.param(SPAM, "which takes report_spam, a high-risk contract", id="spam"),
    ],
)
async def test_an_email_is_not_moved_to_a_special_mailbox(
    client: AsyncClient, boundary: FakeBoundary, mailbox_id: str, why: str
) -> None:
    for role in ("drafts", "sent", "outbox", "templates"):
        boundary.tmail.mailboxes[f"mbx-{role}"] = own_mailbox(role.title(), role)
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "move", {"mailbox_id": mailbox_id})

    assert response.status_code == 409
    assert response.json()["code"] == "mailbox_forbidden"
    assert why in response.json()["detail"]
    assert writes(boundary) == []


@pytest.mark.parametrize(
    ("operation", "body"),
    [
        pytest.param("move", {"mailbox_id": INBOX}, id="move"),
        pytest.param("archive", None, id="archive"),
    ],
)
async def test_an_email_in_spam_is_not_taken_out_of_it(
    client: AsyncClient, boundary: FakeBoundary, operation: str, body: dict[str, str] | None
) -> None:
    # TMail would report it as ham to the filter all users share: report_not_spam's, not a move's
    boundary.tmail.deliver("email-1", SPAM)

    response = await post(client, "email-1", operation, body)

    assert response.status_code == 409
    assert response.json() == problem(
        "email_in_spam",
        "Email in spam",
        409,
        "The email is in spam: TMail would report it as not spam to the spam filter that all"
        " users share, which takes report_not_spam, a high-risk contract not offered yet, rather"
        " than a move. trash_email can still put it in the trash, which reports nothing.",
    )
    assert writes(boundary) == []


async def test_a_mailbox_shared_with_the_user_keeps_the_email(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # The email is in the boss's mailbox too: only the user's own mailboxes are theirs to change
    boundary.tmail.deliver("email-1", INBOX, mailboxIds={INBOX: True, BOSS: True})

    response = await post(client, "email-1", "move", {"mailbox_id": PROJECTS})

    assert response.status_code == 200, response.text
    assert mailboxes_of(boundary, "email-1") == {PROJECTS, BOSS}


async def test_the_user_archives_an_email(client: AsyncClient, boundary: FakeBoundary) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "archive")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "email_id": "email-1",
        "mailbox_id": ARCHIVE,
        "mailbox_name": "Archive",
    }
    assert mailboxes_of(boundary, "email-1") == {ARCHIVE}


ROLES = [
    pytest.param("archive", "archive", ARCHIVE, id="archive"),
    pytest.param("trash", "trash", TRASH, id="trash"),
]
"""Each operation that moves an email to the mailbox of a role: its role, and that mailbox."""


@pytest.mark.parametrize(("operation", "role", "mailbox_id"), ROLES)
async def test_without_the_mailbox_of_its_role_nothing_is_moved(
    client: AsyncClient, boundary: FakeBoundary, operation: str, role: str, mailbox_id: str
) -> None:
    del boundary.tmail.mailboxes[mailbox_id]
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", operation)

    assert response.status_code == 404
    assert response.json() == problem(
        "mailbox_not_found", "Mailbox not found", 404, f"The user has no {role} mailbox."
    )
    assert writes(boundary) == []


async def test_two_archives_are_refused_rather_than_guessed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes["mbx-archive-2"] = own_mailbox("Archive 2", "archive")
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "archive")

    assert response.status_code == 409
    assert response.json() == problem(
        "mailbox_ambiguous",
        "Ambiguous mailbox",
        409,
        "Several of the user's mailboxes have the role archive: mbx-archive, mbx-archive-2. Ask"
        " the user which one they mean, then move the email there with move_email and its"
        " mailbox_id.",
    )
    assert writes(boundary) == []


async def test_two_trash_mailboxes_are_left_to_the_user(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # move_email moves no email to a trash: no contract can put it in the one the user means
    boundary.tmail.mailboxes["mbx-trash-2"] = own_mailbox("Trash 2", "trash")
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "trash")

    assert response.status_code == 409
    assert response.json() == problem(
        "trash_ambiguous",
        "Several trash mailboxes",
        409,
        "Several of the user's mailboxes have the role trash: mbx-trash, mbx-trash-2. No"
        " contract chooses among them: ask the user to keep a single trash folder in Twake Mail,"
        " then put the email in the trash again.",
    )
    assert writes(boundary) == []


@pytest.mark.parametrize("mailbox_id", [INBOX, SPAM], ids=["from the inbox", "from spam"])
async def test_the_user_puts_an_email_in_the_trash(
    client: AsyncClient, boundary: FakeBoundary, mailbox_id: str
) -> None:
    boundary.tmail.deliver("email-1", mailbox_id)

    response = await post(client, "email-1", "trash")

    assert response.status_code == 200, response.text
    assert response.json() == {"email_id": "email-1", "mailbox_id": TRASH, "mailbox_name": "Trash"}
    assert mailboxes_of(boundary, "email-1") == {TRASH}
    # Moved, never destroyed: the user can move it back
    [write] = writes(boundary)
    assert set(write.arguments) == {"accountId", "update"}


async def test_a_move_tmail_refuses_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.refused_update = "forbidden"
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "archive")

    assert response.status_code == 502
    assert response.json()["code"] == "mail_unavailable"
    assert response.json()["detail"] == "Mail answered forbidden to Email/set."


NO_SINGLE_MAILBOX = [
    pytest.param({}, id="no mailbox"),
    pytest.param({"mailbox_id": PROJECTS, "mailbox_name": "Projects"}, id="two mailboxes"),
    pytest.param({"mailbox_id": None}, id="a null id"),
    pytest.param({"mailbox_name": "Projects", "parent": "INBOX"}, id="a field it does not take"),
    pytest.param({"mailbox_id": "mbx/projects"}, id="a mailbox id that is not one"),
    pytest.param({"mailbox_name": ""}, id="an empty name"),
    pytest.param({"mailbox_name": "p" * 201}, id="a name of 201 characters"),
]
"""Bodies of move_email that do not name exactly one mailbox, by a valid id or name."""


async def move_schema(client: AsyncClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """The schema of move_email's body, and the document it is in."""
    document: dict[str, Any] = (await client.get("/openapi.json")).json()
    operation = document["paths"][f"{EMAILS}/{{email_id}}/move"]["post"]
    return operation["requestBody"]["content"]["application/json"]["schema"], document


@pytest.mark.parametrize("body", NO_SINGLE_MAILBOX)
async def test_a_move_that_names_no_single_mailbox_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    response = await post(client, "email-1", "move", body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tmail.calls == []


@pytest.mark.parametrize("body", NO_SINGLE_MAILBOX)
async def test_the_gateway_refuses_a_move_that_names_no_single_mailbox(
    client: AsyncClient, body: dict[str, Any]
) -> None:
    # The gateway checks each body against the document, before the service sees it
    schema, document = await move_schema(client)

    assert refusal(body, schema, document) is not None


async def test_the_gateway_takes_a_move_to_one_mailbox(client: AsyncClient) -> None:
    schema, document = await move_schema(client)

    assert refusal({"mailbox_id": PROJECTS}, schema, document) is None
    assert refusal({"mailbox_name": "Projects"}, schema, document) is None


async def test_a_move_without_a_body_names_the_body(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await post(client, "email-1", "move")

    assert response.json() == problem(
        "invalid_request", "Invalid request", 400, "body: Field required"
    )
    assert boundary.tmail.calls == []


@pytest.mark.parametrize(
    ("operation", "body"),
    [
        pytest.param("move", {"mailbox_id": PROJECTS}, id="move"),
        pytest.param("archive", None, id="archive"),
        pytest.param("trash", None, id="trash"),
    ],
)
async def test_an_email_id_that_is_not_one_is_an_invalid_request(
    client: AsyncClient, boundary: FakeBoundary, operation: str, body: dict[str, str] | None
) -> None:
    response = await post(client, "email.1", operation, body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tmail.calls == []


@pytest.mark.parametrize(
    ("operation", "body", "language", "summary"),
    [
        pytest.param(
            "move",
            {"mailbox_name": "projects"},
            "fr",
            "Déplacer le mail « Budget Q4 » de « Paul Martin » <paul.martin@twake.test> : il va"
            " dans le dossier « Projects »",
            id="move",
        ),
        pytest.param(
            "move",
            {"mailbox_id": PROJECTS},
            "en",
            "Move the email “Budget Q4” from “Paul Martin” <paul.martin@twake.test>: it goes to the"
            " folder “Projects”",
            id="move, in English",
        ),
        pytest.param(
            "archive",
            None,
            "fr",
            "Archiver le mail « Budget Q4 » de « Paul Martin » <paul.martin@twake.test> : il va"
            " dans le dossier « Archive »",
            id="archive",
        ),
        pytest.param(
            "trash",
            None,
            "fr",
            "Mettre à la corbeille le mail « Budget Q4 » de « Paul Martin »"
            " <paul.martin@twake.test> : il va dans le dossier « Trash », d'où il peut être"
            " ressorti",
            id="trash",
        ),
        pytest.param(
            "trash",
            None,
            "en",
            "Trash the email “Budget Q4” from “Paul Martin” <paul.martin@twake.test>: it goes to"
            " the folder “Trash”, from which it can be moved back",
            id="trash, in English",
        ),
    ],
)
async def test_a_preview_tells_the_owner_which_email_goes_where_and_moves_nothing(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, Any] | None,
    language: str,
    summary: str,
) -> None:
    boundary.tmail.deliver("email-1", INBOX)

    told, _ = preview_of(await ask(client, "email-1", operation, body, language))

    assert told == summary
    assert writes(boundary) == []
    assert mailboxes_of(boundary, "email-1") == {INBOX}


async def test_what_the_sender_wrote_cannot_close_the_quotes_it_comes_in(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    sender = {"name": "Paul» de « Boss", "email": "paul@x.test> <boss@corp.test"}
    boundary.tmail.deliver("email-1", INBOX, subject="Budget » vers « Trash", **{"from": [sender]})

    told, _ = preview_of(await ask(client, "email-1", "archive"))

    assert told == (
        "Archiver le mail « Budget ' vers ' Trash » de « Paul' de ' Boss »"
        " <paul@x.test boss@corp.test> : il va dans le dossier « Archive »"
    )


async def test_a_preview_names_an_email_without_subject_nor_sender(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX, subject="", **{"from": None})

    told, _ = preview_of(await ask(client, "email-1", "archive"))

    assert told == "Archiver le mail sans objet : il va dans le dossier « Archive »"


async def test_a_preview_refuses_what_the_move_would_refuse(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", SPAM)

    response = await ask(client, "email-1", "archive")

    assert response.status_code == 409
    assert response.json()["code"] == "email_in_spam"
    assert writes(boundary) == []


async def test_the_owner_who_allowed_what_they_were_shown_moves_the_email(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.deliver("email-1", INBOX)
    _, digest = preview_of(await ask(client, "email-1", "move", {"mailbox_id": PROJECTS}))

    response = await post(
        client, "email-1", "move", {"mailbox_id": PROJECTS}, allowed_after(digest)
    )

    assert response.status_code == 200, response.text
    assert mailboxes_of(boundary, "email-1") == {PROJECTS}


@pytest.mark.parametrize(
    ("operation", "body"),
    [("move", {"mailbox_id": PROJECTS}), ("archive", None), ("trash", None)],
    ids=["move", "archive", "trash"],
)
async def test_an_email_moved_since_the_preview_stays_where_it_is(
    client: AsyncClient,
    boundary: FakeBoundary,
    operation: str,
    body: dict[str, Any] | None,
) -> None:
    boundary.tmail.deliver("email-1", INBOX)
    _, digest = preview_of(await ask(client, "email-1", operation, body))
    # The user files it themselves before they say yes
    boundary.tmail.mailboxes["mbx-later"] = own_mailbox("Later")
    boundary.tmail.emails["email-1"]["mailboxIds"] = {"mbx-later": True}

    response = await post(client, "email-1", operation, body, allowed_after(digest))

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert writes(boundary) == []
    assert mailboxes_of(boundary, "email-1") == {"mbx-later"}


async def test_a_preview_of_an_email_from_many_fits_what_the_harness_shows(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    senders = [{"name": "🦊" * 300, "email": f"fox{n}@crafted.test"} for n in range(1, 51)]
    boundary.tmail.deliver("email-1", INBOX, **{"from": senders})

    told, _ = preview_of(await ask(client, "email-1", "archive", language="en"))

    assert told == (
        f"Archive the email “Budget Q4” from “{'🦊' * 200}” <fox1@crafted.test> and 49 others:"
        " it goes to the folder “Archive”"
    )


async def test_a_preview_counts_a_sender_too_long_to_name(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    sender = {"name": "🦊" * 200, "email": "🦊" * 300 + "@crafted.test"}
    boundary.tmail.deliver("email-1", INBOX, **{"from": [sender]})

    told, _ = preview_of(await ask(client, "email-1", "archive", language="en"))

    assert told == "Archive the email “Budget Q4” from 1 person: it goes to the folder “Archive”"
