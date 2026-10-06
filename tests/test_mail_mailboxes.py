from httpx import AsyncClient

from tests.conftest import AS_MMAUDET
from tests.fakes import INBOX, MMAUDET, SPAM, TRASH, FakeBoundary, StoredMailbox, email_of

MAILBOXES = "/contracts/v1/mail/mailboxes"


async def test_the_user_lists_their_own_mailboxes(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes[INBOX].jmap |= {"totalEmails": 12, "unreadEmails": 3}
    boundary.tmail.mailboxes["mbx-projects"] = StoredMailbox(
        MMAUDET,
        {"name": "Projects", "parentId": INBOX, "role": None, "totalEmails": 4, "unreadEmails": 0},
    )

    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "mailboxes": [
            {
                "id": INBOX,
                "name": "INBOX",
                "role": "inbox",
                "parent_id": None,
                "total_emails": 12,
                "unread_emails": 3,
            },
            {
                "id": TRASH,
                "name": "Trash",
                "role": "trash",
                "parent_id": None,
                "total_emails": 0,
                "unread_emails": 0,
            },
            {
                "id": SPAM,
                "name": "Spam",
                "role": "spam",
                "parent_id": None,
                "total_emails": 0,
                "unread_emails": 0,
            },
            {
                "id": "mbx-projects",
                "name": "Projects",
                "role": None,
                "parent_id": INBOX,
                "total_emails": 4,
                "unread_emails": 0,
            },
        ]
    }


async def test_a_mailbox_shared_with_the_user_is_not_listed(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.tmail.mailboxes["mbx-boss"] = StoredMailbox(
        email_of("boss"),
        {"name": "Boss", "parentId": None, "role": None, "totalEmails": 9, "unreadEmails": 9},
        shared_with={MMAUDET},
    )

    response = await client.get(MAILBOXES, headers=AS_MMAUDET)

    assert [mailbox["id"] for mailbox in response.json()["mailboxes"]] == [INBOX, TRASH, SPAM]
