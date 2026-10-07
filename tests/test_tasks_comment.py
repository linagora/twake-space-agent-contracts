from typing import Any

import pytest
from httpx import AsyncClient, Response

from tests.conftest import AS_MMAUDET, allowed_after, asking_preview, preview_of
from tests.fakes import FakeBoundary, TasksBoard, TasksMember, TasksTask, tasks_id, tasks_member

MMAUDET = tasks_member("mmaudet")
ALICE = tasks_member("alice", "admin")


def website(boundary: FakeBoundary, *members: TasksMember, **more: Any) -> TasksBoard:
    return boundary.tasks.board("Website", "WEB", *(members or (MMAUDET, ALICE)), **more)


async def comment(
    client: AsyncClient, task: TasksTask, headers: dict[str, str] | None = None, **body: Any
) -> Response:
    return await client.post(
        f"/contracts/v1/tasks/boards/{task.board}/tasks/{task.id}/comments",
        json=body,
        headers=AS_MMAUDET | (headers or {}),
    )


async def test_a_comment_is_added_to_the_task(client: AsyncClient, boundary: FakeBoundary) -> None:
    board = website(boundary)
    task = boundary.tasks.task(board, "Fix the login page")

    response = await comment(client, task, body="  Can you add the screenshots?\n")

    assert response.status_code == 201, response.text
    answer = response.json()
    assert answer == {
        "board_id": board.id,
        "task_id": task.id,
        "comment_id": task.comments[0]["id"],
        "created_at": "2026-10-07T09:30:00Z",
        "mentioned": [],
    }
    assert boundary.tasks.writes == [
        (
            "POST",
            f"/api/boards/{board.id}/tasks/{task.id}/comments",
            {"body": "Can you add the screenshots?"},
        )
    ]
    assert [(each["author"]["email"], each["body"]) for each in task.comments] == [
        ("mmaudet@twake.test", "Can you add the screenshots?")
    ]


async def test_a_viewer_comments_too(client: AsyncClient, boundary: FakeBoundary) -> None:
    # As in Tasks, where a viewer reads the board and comments on its tasks
    task = boundary.tasks.task(
        website(boundary, tasks_member("mmaudet", "viewer"), ALICE), "Fix the login page"
    )

    response = await comment(client, task, body="Can you add the screenshots?")

    assert response.status_code == 201, response.text
    assert len(task.comments) == 1


async def test_a_task_off_the_board_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # Tasks takes a comment on an archived task, or one in the trash, which the board hides
    board = website(boundary)
    archived = boundary.tasks.task(board, "Former task", hidden=True)
    unknown = TasksTask(tasks_id("unknown"), board.id, 2, "Unknown")

    for task in (archived, unknown):
        response = await comment(client, task, body="Still needed?")

        assert response.status_code == 404
        assert response.json()["code"] == "task_not_found"
    assert boundary.tasks.writes == []


async def test_an_archived_board_is_refused(client: AsyncClient, boundary: FakeBoundary) -> None:
    # Tasks keeps an archived board read only, though it takes a comment there
    task = boundary.tasks.task(website(boundary, archived=True), "Fix the login page")

    response = await comment(client, task, body="Still needed?")

    assert response.status_code == 409
    assert response.json()["code"] == "board_archived"
    assert boundary.tasks.writes == []


async def test_a_board_the_user_is_not_a_member_of_answers_like_an_unknown_one(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    others = boundary.tasks.task(website(boundary, ALICE), "Fix the login page")
    unknown = TasksTask(tasks_id("unknown"), tasks_id("no board"), 1, "Unknown")

    for task in (others, unknown):
        response = await comment(client, task, body="Still needed?")

        assert response.status_code == 404
        assert response.json()["code"] == "board_not_found"
    assert boundary.tasks.writes == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no body"),
        pytest.param({"body": " \n "}, id="a blank body"),
        pytest.param({"body": "c" * 10_001}, id="a body longer than Tasks takes"),
        pytest.param({"body": "Soon?", "notify": False}, id="a field it does not take"),
    ],
)
async def test_an_invalid_comment_is_refused_before_anything_is_written(
    client: AsyncClient, boundary: FakeBoundary, body: dict[str, Any]
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")

    response = await comment(client, task, **body)

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert boundary.tasks.writes == []


async def test_the_members_a_comment_mentions_are_named(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    # As Tasks finds them: @ and the email of a member of the board, after a blank, whatever its
    # case; the user, whom Tasks does not tell of their own comment, and anyone else are not
    bob = tasks_member("bob")
    task = boundary.tasks.task(website(boundary, MMAUDET, ALICE, bob), "Fix the login page")

    response = await comment(
        client,
        task,
        body="@ALICE@twake.test and @bob@twake.test, can you check? cc @mmaudet@twake.test"
        " @carol@twake.test mail@bob@twake.test",
    )

    assert response.status_code == 201, response.text
    assert response.json()["mentioned"] == ["alice@twake.test", "bob@twake.test"]


@pytest.mark.parametrize(
    ("language", "summary"),
    [
        (
            "fr",
            "Commenter la tâche WEB-1 « Fix the login page » du tableau « Website » :\n"
            "\tCan you add the screenshots, @alice@twake.test?\n"
            "\tThanks!\n"
            "Il mentionne <alice@twake.test>, que Tasks prévient aussi, et qui suit alors la"
            " tâche.\n"
            "Tasks prévient ceux qui suivent la tâche, dans Tasks et par mail : par défaut son"
            " créateur, ses responsables et ceux qui l'ont commentée.",
        ),
        (
            "en",
            "Comment on the task WEB-1 “Fix the login page” on the board “Website”:\n"
            "\tCan you add the screenshots, @alice@twake.test?\n"
            "\tThanks!\n"
            "It mentions <alice@twake.test>, whom Tasks tells too, and who then follows the"
            " task.\n"
            "Tasks tells the people who follow the task, in Tasks and by email: by default its"
            " creator, its assignees and those who commented on it.",
        ),
    ],
)
async def test_a_preview_tells_the_owner_what_the_comment_says_and_writes_nothing(
    client: AsyncClient, boundary: FakeBoundary, language: str, summary: str
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")

    response = await comment(
        client,
        task,
        asking_preview(language),
        body="Can you add the screenshots, @alice@twake.test?\nThanks!",
    )

    told, _ = preview_of(response)
    assert told == summary
    assert boundary.tasks.writes == []
    assert task.comments == []


async def test_a_preview_names_ten_members_mentioned_at_most(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    members = [tasks_member(f"member{number:02}") for number in range(12)]
    task = boundary.tasks.task(website(boundary, MMAUDET, *members), "Fix the login page")
    body = " ".join(f"@{member.email}" for member in members) + " can you check?"

    told, _ = preview_of(await comment(client, task, asking_preview("en"), body=body))

    assert told.splitlines()[-2] == (
        "It mentions "
        + ", ".join(f"<{member.email}>" for member in members[:10])
        + " and 2 others, whom Tasks tells too, and who then follow the task."
    )


@pytest.mark.parametrize(
    ("character", "most"),
    [
        pytest.param("c", range(5_900, 6_000), id="plain text"),
        pytest.param("é", range(2_900, 3_000), id="accented letters"),
        pytest.param("😀", range(1_450, 1_500), id="emoji"),
    ],
)
async def test_a_comment_is_no_longer_than_its_preview_shows_whole(
    client: AsyncClient, boundary: FakeBoundary, character: str, most: range
) -> None:
    # The owner confirms a comment they read whole: Tasks takes 10,000 characters, of which the
    # preview shows fewer, the fewer the more bytes each takes
    task = boundary.tasks.task(website(boundary), "Fix the login page")

    async def shown_whole(count: int) -> bool:
        """Whether the preview shows a comment of that many characters whole, or refuses it."""
        body = character * count
        response = await comment(client, task, asking_preview("fr"), body=body)
        if response.status_code == 400:
            assert response.json()["code"] == "invalid_request"
            return False
        summary, _ = preview_of(response)
        assert f"\t{body}\n" in summary
        return True

    # The longest comment taken, between one character and the 10,000 Tasks takes
    taken, refused = 1, 10_000
    while refused - taken > 1:
        middle = (taken + refused) // 2
        if await shown_whole(middle):
            taken = middle
        else:
            refused = middle
    response = await comment(client, task, body=character * refused)

    assert taken in most
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert f"keep it to {taken:,} characters" in response.json()["detail"]
    assert boundary.tasks.writes == []


async def test_the_owner_who_allowed_what_they_were_shown_comments(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")
    _, digest = preview_of(await comment(client, task, asking_preview("fr"), body="Soon?"))

    response = await comment(client, task, allowed_after(digest), body="Soon?")

    assert response.status_code == 201, response.text
    assert [each["body"] for each in task.comments] == ["Soon?"]


async def test_a_task_renamed_since_the_preview_gets_no_comment(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    task = boundary.tasks.task(website(boundary), "Fix the login page")
    _, digest = preview_of(await comment(client, task, asking_preview("fr"), body="Soon?"))
    # A member renames the task before the owner says yes
    task.title = "Rewrite the login page"

    response = await comment(client, task, allowed_after(digest), body="Soon?")

    assert response.status_code == 409
    assert response.json()["code"] == "changed_since_preview"
    assert boundary.tasks.writes == []
