"""tasks.comment.create.v1: the user comments on a task in Twake Tasks, as themselves, which tells
the people who follow it."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, get_args

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    Previewing,
    digest_of,
    excerpt,
    longest_shown_whole,
    shown_size,
    shown_whole,
)
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.task_writes import (
    EXAMPLE_IDS,
    BoardId,
    TaskId,
    shown_emails,
    task_names,
)
from twake_space_agent_contracts.tasks import (
    BoardContent,
    BoardTask,
    Tasks,
    board_archived,
    board_not_found,
    task_not_found,
)

LONGEST_COMMENT = BUDGET // 2
"""More characters than the preview of a comment ever shows whole, each taking two bytes at least
of what the harness counts: Tasks itself takes 10,000."""
# A mention, as Tasks finds them in a comment: @ and an email, at the start or after a blank
MENTION = re.compile(r"(?:^|\s)@([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]*[A-Za-z0-9])")


class NewComment(BaseModel):
    """A comment to add to a task."""

    model_config = ConfigDict(extra="forbid")

    body: Annotated[
        str,
        Field(
            min_length=1,
            max_length=LONGEST_COMMENT,
            description="The comment, in Markdown, no longer than its preview shows whole: some "
            "5,900 characters of plain text, fewer of accented letters or emoji.",
        ),
    ]


class AddedComment(BaseModel):
    """The comment, as Tasks took it."""

    board_id: str
    task_id: str
    comment_id: str
    created_at: datetime
    mentioned: list[str] = Field(
        description="The emails of the members the comment mentions, whom Tasks tells besides "
        "the people who follow the task."
    )


def _mentioned(board: BoardContent, body: str, email: str) -> list[str]:
    """The emails of the members of the board the comment mentions, whom Tasks tells, but for the
    user's own, whom it never tells of what they do: sorted, so that the same members, mentioned in
    another order, make the same digest."""
    found = {mention.lower() for mention in MENTION.findall(body)}
    return sorted({joined for _, joined in board.members if joined in found and joined != email})


@dataclass(frozen=True)
class _Words:
    """What a preview of a comment tells the owner, in one language."""

    comment: str
    mentions: tuple[str, str]
    """For one member mentioned, and for several."""
    told: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        comment="Commenter la tâche {key} {title} du tableau {board} :",
        mentions=(
            "Il mentionne {people}, que Tasks prévient aussi, et qui suit alors la tâche.",
            "Il mentionne {people}, que Tasks prévient aussi, et qui suivent alors la tâche.",
        ),
        told="Tasks prévient ceux qui suivent la tâche, dans Tasks et par mail : par défaut son"
        " créateur, ses responsables et ceux qui l'ont commentée.",
    ),
    "en": _Words(
        comment="Comment on the task {key} {title} on the board {board}:",
        mentions=(
            "It mentions {people}, whom Tasks tells too, and who then follows the task.",
            "It mentions {people}, whom Tasks tells too, and who then follow the task.",
        ),
        told="Tasks tells the people who follow the task, in Tasks and by email: by default its"
        " creator, its assignees and those who commented on it.",
    ),
}


def _around(
    board: BoardContent, task: BoardTask, mentioned: list[str], language: Language
) -> tuple[str, str]:
    """What the preview of a comment tells before it, and after it."""
    words = _WORDS[language]
    head = words.comment.format(**task_names(board, task, language)) + "\n"
    tail = words.told
    if mentioned:
        one, several = words.mentions
        mentions = one if len(mentioned) == 1 else several
        tail = mentions.format(people=shown_emails(mentioned, language)) + "\n" + tail
    return head, tail


def _room(board: BoardContent, task: BoardTask, mentioned: list[str]) -> int:
    """What the preview of a comment leaves of its summary for the comment, in whichever of its
    languages leaves less."""
    rooms = (
        BUDGET - shown_size(head) - shown_size("\n" + tail)
        for head, tail in (
            _around(board, task, mentioned, language) for language in get_args(Language)
        )
    )
    return min(rooms)


def _commenting(
    board: BoardContent, task: BoardTask, body: str, mentioned: list[str], language: Language
) -> str:
    """What commenting does, as the owner reads it: the comment, whole, the members it mentions,
    and whom Tasks tells."""
    head, tail = _around(board, task, mentioned, language)
    return head + excerpt(body, _room(board, task, mentioned), language) + "\n" + tail


def router(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.comment.create.v1"])

    @routes.post(
        "/boards/{board_id}/tasks/{task_id}/comments",
        operation_id="comment_on_task",
        status_code=201,
        summary="Comment on a task in Twake Tasks as the user",
        description=(
            "Adds a comment, as the user you act for, to a task of a board they are a member of, "
            "a viewer too, as in Tasks: body is Markdown, no longer than its preview shows "
            "whole, which the owner reads before they confirm it, some 5,900 characters of plain "
            "text, fewer of accented letters or emoji. @ and the "
            "email of a member of the board, such as @alice@example.com, mentions them, as "
            "mentioned gives back. Tasks tells the people who follow the task, in Tasks and by "
            "email: by default its creator, its assignees and those who commented on it, and the "
            "members mentioned, who follow it from then on, as the user does. A comment stays: "
            "Tasks offers no way to change or delete it. Call it only once the user asked for "
            "this very comment; they confirm each call. An archived board is refused, and an "
            "archived task, or one in the trash, is not found. Example, to ask a member for "
            f"screenshots: {EXAMPLE_IDS}, "
            'body={"body": "@alice@example.com, can you add the screenshots?"}.'
        ),
        response_model=AddedComment,
        # A comment stays, and Tasks tells the people who follow the task of it, by email too:
        # the owner confirms each one, shown what it says
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def comment_on_task(
        board_id: BoardId,
        task_id: TaskId,
        new: NewComment,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> AddedComment | JSONResponse:
        body = new.body.strip()
        if not body:
            raise invalid_request("body: A comment cannot be blank.")
        board = await tasks.board(user, board_id)
        if board is None:
            raise board_not_found(board_id)
        # Read only in Tasks, which takes a comment there all the same
        if board.archived:
            raise board_archived(board_id)
        # Tasks takes a comment on an archived task too, or one in the trash: not the contract
        task = board.task(task_id)
        if task is None:
            raise task_not_found(board_id, task_id)
        mentioned = _mentioned(board, body, user.email)
        # The owner confirms a comment once they read it whole, whatever their language
        room = _room(board, task, mentioned)
        if not shown_whole(body, room):
            longest = longest_shown_whole(body, room)
            raise invalid_request(
                "body: The comment is longer than its preview shows whole, which its owner reads"
                f" before they confirm it: keep it to {longest:,} characters like these."
            )
        # What the owner allows: a comment on this task, which tells these members
        summary = task.summary
        digest = digest_of(board_id, task_id, summary.key, summary.untrusted.title, mentioned)
        if preview.asked:
            told = _commenting(board, task, body, mentioned, preview.language)
            return preview.answer(told, digest)
        preview.check(digest)
        comment_id, created_at = await tasks.comment_on_task(user, board_id, task_id, body)
        return AddedComment(
            board_id=board_id,
            task_id=task_id,
            comment_id=comment_id,
            created_at=created_at,
            mentioned=mentioned,
        )

    return routes
