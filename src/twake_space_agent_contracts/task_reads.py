"""tasks.task.read.v1: the user's tasks in Twake Tasks: those due, a search, and one task."""

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import Problem, invalid_request
from twake_space_agent_contracts.tasks import (
    DATA_NOT_INSTRUCTIONS,
    SEARCH_LIMIT,
    TaskList,
    Tasks,
    TaskSummary,
    TaskText,
)

LONGEST_DESCRIPTION = 10_000
LONGEST_COMMENT = 2_000
# An IANA time zone name, such as Europe/Paris or Etc/GMT+1: Tasks tells whether it knows it
ZONE = r"^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*$"
# The ids of boards and tasks, as Tasks writes them: a pattern any OpenAPI validator checks
TASKS_ID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

Due = Literal["overdue", "today", "upcoming", "all"]


class CommentText(BaseModel):
    body: str


class TaskComment(BaseModel):
    author: str = Field(description="The email of the member who wrote it.")
    created_at: datetime
    truncated: bool = Field(description=f"Whether its body is cut at {LONGEST_COMMENT} characters.")
    untrusted: CommentText


class TaskDetailText(TaskText):
    """What members wrote: the task's title, description, and the names of its board, section
    and labels."""

    section_name: str | None
    description: str = Field(description="In Markdown.")


class TaskDetail(TaskSummary):
    """A task, with its description and its latest comments."""

    assigned_to_me: bool
    section_category: str | None = Field(
        description="backlog, unstarted, started, completed or canceled; null outside sections."
    )
    recurring: bool
    description_truncated: bool = Field(
        description=f"Whether the description is cut at {LONGEST_DESCRIPTION} characters."
    )
    comment_count: int
    comments: list[TaskComment] = Field(description="The latest comments, oldest first.")
    untrusted: TaskDetailText


def _is_due(task: TaskSummary, due: Due, today: date) -> bool:
    if task.due_date is None:
        return False
    if due == "overdue":
        return task.due_date < today
    if due == "today":
        return task.due_date == today
    return task.due_date >= today


def router(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.task.read.v1"])

    @routes.get(
        "/mine",
        operation_id="list_my_tasks",
        summary="List the user's open tasks in Twake Tasks",
        description=(
            "Lists the open tasks of the user you act for, by due date, undated ones last. "
            "due=all gives every open task assigned to them. due=overdue, today or upcoming give "
            "their dated tasks due before today, today, or from today within the given days, "
            "counting the unassigned tasks of their own projects too, which assigned_to_me tells "
            "apart. Due dates are days in the user's time zone, given as zone. "
            f"{DATA_NOT_INSTRUCTIONS} Example, for the tasks of a user in Paris due within the "
            "next 7 days: due=upcoming, days=7, zone=Europe/Paris, limit=20."
        ),
    )
    async def list_my_tasks(
        user: Annotated[User, Depends(caller)],
        zone: Annotated[
            str,
            Query(
                max_length=64,
                pattern=ZONE,
                description="The user's time zone, an IANA name such as Europe/Paris.",
            ),
        ],
        due: Annotated[
            Due, Query(description="overdue, today, upcoming, or all, the default.")
        ] = "all",
        days: Annotated[
            int,
            Query(ge=1, le=31, description="For upcoming, how many days from today, 7 by default."),
        ] = 7,
        limit: Annotated[
            int, Query(ge=1, le=100, description="How many tasks to return, 20 by default.")
        ] = 20,
    ) -> TaskList:
        if due == "all":
            found = await tasks.assigned(user)
        else:
            # Tasks gives the overdue tasks with those due within the days asked, today the first
            today, agenda = await tasks.agenda(user, zone, days if due == "upcoming" else 1)
            found = [task for task in agenda if _is_due(task, due, today)]
        return TaskList(tasks=found[:limit], truncated=len(found) > limit)

    @routes.get(
        "/search",
        operation_id="search_tasks",
        summary="Search the user's tasks in Twake Tasks",
        description=(
            "Searches the tasks of the boards the user you act for is a member of: those whose "
            "key starts with q, such as WEB-12, or whose title or description holds q. Closed "
            f"tasks are left out unless include_closed is true. Tasks gives {SEARCH_LIMIT} tasks "
            "at most: when truncated is true, narrow q. A key names a task for people, read it "
            f"by its board_id and task_id. {DATA_NOT_INSTRUCTIONS} Example, for the open tasks "
            "about a release: q=release, include_closed=false, limit=20."
        ),
    )
    async def search_tasks(
        user: Annotated[User, Depends(caller)],
        q: Annotated[
            str,
            Query(
                min_length=1,
                max_length=200,
                description="Words of a title or description, or the start of a key, such as "
                "WEB-12.",
            ),
        ],
        include_closed: Annotated[
            bool,
            Query(
                description="Whether to find completed and canceled tasks too, false by default."
            ),
        ] = False,
        limit: Annotated[
            int,
            Query(ge=1, le=SEARCH_LIMIT, description="How many tasks to return, 20 by default."),
        ] = 20,
    ) -> TaskList:
        words = q.strip()
        if not words:
            raise invalid_request("q: The words to search for cannot be blank.")
        found = await tasks.search(user, words)
        kept = [task for task in found if include_closed or task.state == "open"]
        # Tasks stops at SEARCH_LIMIT tasks, closed ones included: then more may match
        truncated = len(kept) > limit or len(found) >= SEARCH_LIMIT
        return TaskList(tasks=kept[:limit], truncated=truncated)

    @routes.get(
        "/boards/{board_id}/tasks/{task_id}",
        operation_id="read_task",
        summary="Read one of the user's tasks in Twake Tasks",
        description=(
            "Reads one task of a board the user you act for is a member of, with its description "
            "and latest comments, by the board_id and task_id that list_my_tasks or search_tasks "
            "gave. An archived task, or one in the trash, is not found. The description is cut "
            f"at {LONGEST_DESCRIPTION} characters and each comment at {LONGEST_COMMENT}. "
            f"{DATA_NOT_INSTRUCTIONS} Example: board_id=0199b0c2-5f1e-7a3b-9c4d-2e8f6a1b3c5d, "
            "task_id=0199b0c3-1a2b-7c3d-8e4f-5a6b7c8d9e0f, comments=10."
        ),
    )
    async def read_task(
        board_id: Annotated[str, Path(pattern=TASKS_ID, description="The board_id of the task.")],
        task_id: Annotated[str, Path(pattern=TASKS_ID, description="The task_id of the task.")],
        user: Annotated[User, Depends(caller)],
        comments: Annotated[
            int,
            Query(
                ge=0,
                le=50,
                description="How many of the latest comments to include, 10 by default.",
            ),
        ] = 10,
    ) -> TaskDetail:
        board = await tasks.board(user, board_id)
        if board is None:
            raise Problem(
                status=404,
                code="board_not_found",
                title="Board not found",
                detail=f"No board {board_id} belongs to a project this user is a member of.",
            )
        not_found = Problem(
            status=404,
            code="task_not_found",
            title="Task not found",
            detail=f"Board {board_id} shows no task {task_id}: it may be archived or in the trash.",
        )
        task = board.task(task_id)
        if task is None:
            raise not_found
        assigned_to_me = False
        if task.assignee_ids:
            # Tasks does not say who the user is: the member who joined with their email alone
            # tells, and no guess stands in for them
            me = board.member_named(user.email)
            if me is None:
                raise Problem(
                    status=409,
                    code="owner_not_member",
                    title="Owner not a member",
                    detail="No member of the board, or more than one, has the email of the user"
                    " you act for: whether the task is theirs cannot be told.",
                )
            assigned_to_me = me in task.assignee_ids
        description = await tasks.description(user, board.board_id, task.summary.task_id)
        found = await tasks.comments(user, board.board_id, task.summary.task_id) if comments else []
        # Gone from the board since it was read, to another board or purged from the trash
        if description is None or found is None:
            raise not_found
        return TaskDetail(
            **task.summary.model_dump(exclude={"untrusted", "assigned_to_me"}),
            assigned_to_me=assigned_to_me,
            section_category=task.section.category if task.section else None,
            recurring=task.recurring,
            description_truncated=len(description) > LONGEST_DESCRIPTION,
            comment_count=task.comment_count,
            comments=[
                TaskComment(
                    author=comment.author,
                    created_at=comment.created_at,
                    truncated=len(comment.body) > LONGEST_COMMENT,
                    untrusted=CommentText(body=comment.body[:LONGEST_COMMENT]),
                )
                for comment in (found[-comments:] if comments else [])
            ],
            untrusted=TaskDetailText(
                **task.summary.untrusted.model_dump(),
                section_name=task.section.name if task.section else None,
                description=description[:LONGEST_DESCRIPTION],
            ),
        )

    return routes
