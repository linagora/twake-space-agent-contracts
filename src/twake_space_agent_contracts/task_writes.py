"""tasks.task.create.v1, tasks.task.update.v1 and tasks.task.complete.v1: the user creates,
changes and completes tasks in Twake Tasks, as themselves, on the boards they can edit."""

from datetime import date
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Path
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.problems import Problem, invalid_request
from twake_space_agent_contracts.tasks import (
    DATA_NOT_INSTRUCTIONS,
    TASKS_ID,
    ZONE,
    BoardContent,
    BoardTask,
    Tasks,
    TaskSummary,
    TaskText,
    board_archived,
    board_not_found,
    forbidden_role,
    owner_not_member,
    task_not_found,
)

LONGEST_TITLE = 500
"""The longest title Tasks takes."""
# A due time as Tasks writes it, in hours and minutes
TIME = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
# The fields of a task the contracts change, as Tasks names them
IN_TASKS = {
    "title": "title",
    "priority": "priority",
    "due_date": "dueDate",
    "due_time": "dueTime",
    "due_zone": "dueZone",
    "deadline": "deadline",
}
EXAMPLE_IDS = (
    "board_id=0199b0c2-5f1e-7a3b-9c4d-2e8f6a1b3c5d, task_id=0199b0c3-1a2b-7c3d-8e4f-5a6b7c8d9e0f"
)


def _known_zone(zone: str) -> str:
    """The zone, if the IANA time zone database has it, as Tasks requires."""
    try:
        ZoneInfo(zone)
    except (KeyError, ValueError, OSError) as error:
        raise ValueError(f"{zone} is not an IANA time zone, such as Europe/Paris") from error
    return zone


Title = Annotated[
    str,
    Field(
        min_length=1, max_length=LONGEST_TITLE, description="Plain text, 500 characters at most."
    ),
]
Priority = Annotated[int, Field(ge=1, le=4, description="From 1, the most urgent, to 4.")]
DueDate = Annotated[date, Field(description="The day it is due, such as 2026-10-09.")]
DueTime = Annotated[
    str,
    Field(
        pattern=TIME,
        description="HH:MM on the due date, in due_zone, or wherever the user is without one. It "
        "needs a due date.",
    ),
]
DueZone = Annotated[
    str,
    Field(
        max_length=64,
        pattern=ZONE,
        description="The IANA time zone of the due time, such as Europe/Paris. It needs a due "
        "time.",
    ),
    AfterValidator(_known_zone),
]
Deadline = Annotated[date, Field(description="The day it must be done by, such as 2026-10-16.")]
TasksId = Annotated[str, Field(pattern=TASKS_ID)]
BoardId = Annotated[
    str,
    Path(pattern=TASKS_ID, description="The board_id of the board, as open_boards or reads give."),
]
TaskId = Annotated[
    str, Path(pattern=TASKS_ID, description="The task_id of the task, as reads give it.")
]


class NewTask(BaseModel):
    """A task to create, and where it goes."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    section_id: Annotated[
        TasksId | None,
        Field(
            description="The section_id of the section it goes to; by default the board's first "
            "unstarted section, or none on a board without sections."
        ),
    ] = None
    parent_id: Annotated[
        TasksId | None,
        Field(description="The task_id of the task it is a subtask of, outside sections."),
    ] = None
    priority: Priority | None = None
    due_date: DueDate | None = None
    due_time: DueTime | None = None
    due_zone: DueZone | None = None


class TaskChanges(BaseModel):
    """The fields of a task to change, and only those: null clears one, but the title."""

    model_config = ConfigDict(extra="forbid")

    title: Title | SkipJsonSchema[None] = None
    priority: Priority | None = None
    due_date: DueDate | None = None
    due_time: DueTime | None = None
    due_zone: DueZone | None = None
    deadline: Deadline | None = None


class WrittenTaskText(TaskText):
    """What members wrote: the task's title, and the names of its board, section and labels."""

    section_name: str | None


class WrittenTask(TaskSummary):
    """The task as Tasks shows it once written."""

    section_category: str | None = Field(
        description="backlog, unstarted, started, completed or canceled; null outside sections."
    )
    recurring: bool = Field(
        description="Whether it repeats: completed, it stays open with its next due date."
    )
    untrusted: WrittenTaskText


class CompletedTask(WrittenTask):
    """The task as Tasks shows it once completed."""

    next_due_date: date | None = Field(
        description="For a recurring task, the due date it moved to: it was completed for the "
        "former one, and stays open. Null for any other task."
    )


def _written(board: BoardContent, task: BoardTask, email: str) -> WrittenTask:
    """The task as the board shows it to the user of that email."""
    me = board.member_named(email)
    section = task.section
    return WrittenTask(
        **task.summary.model_dump(exclude={"untrusted", "assigned_to_me"}),
        assigned_to_me=me in task.assignee_ids if me is not None else None,
        section_category=section.category if section else None,
        recurring=task.recurring,
        untrusted=WrittenTaskText(
            **task.summary.untrusted.model_dump(), section_name=section.name if section else None
        ),
    )


def _completed(task: WrittenTask) -> CompletedTask:
    """The task once completed: a recurring one stays open, moved to its next due date."""
    moved = task.recurring and task.state == "open"
    return CompletedTask(**task.model_dump(), next_due_date=task.due_date if moved else None)


async def _editable(tasks: Tasks, user: User, board_id: str) -> BoardContent:
    """The board, if the user is the one member who joined with their email, and may write on
    it."""
    board = await tasks.board(user, board_id)
    if board is None:
        raise board_not_found(board_id)
    # Tasks does not say who the user is: the member who joined with their email alone tells
    if board.member_named(user.email) is None:
        raise owner_not_member("nothing is written on it")
    if board.role not in ("editor", "admin"):
        raise forbidden_role(board_id)
    if board.archived:
        raise board_archived(board_id)
    return board


def _shown(board: BoardContent, task_id: str) -> BoardTask:
    """The task, if the board shows it: not archived, nor in the trash."""
    task = board.task(task_id)
    if task is None:
        raise task_not_found(board.board_id, task_id)
    return task


async def _now(tasks: Tasks, user: User, board_id: str, task_id: str) -> WrittenTask:
    """The task as Tasks shows it, read again once written."""
    board = await tasks.board(user, board_id)
    if board is None:
        raise task_not_found(board_id, task_id)
    return _written(board, _shown(board, task_id), user.email)


def _check_dates(changes: dict[str, Any], current: TaskSummary | None) -> None:
    """Refuses a due time the task would then have without a due date, and a time zone without
    a due time: Tasks refuses the first, and drops the second."""

    def then(name: str) -> Any:
        return changes[name] if name in changes else getattr(current, name, None)

    if changes.get("due_time") is not None and then("due_date") is None:
        raise invalid_request("due_time: A due time needs a due date: give due_date too.")
    if changes.get("due_zone") is not None and None in (then("due_date"), then("due_time")):
        raise invalid_request("due_zone: A time zone goes with a due time: give due_time too.")


def _section(board: BoardContent, new: NewTask) -> str | None:
    """The section the new task goes to, None outside sections, as a subtask is."""
    if new.parent_id is not None:
        if new.parent_id not in board.tasks:
            raise task_not_found(board.board_id, new.parent_id)
        return None
    if new.section_id is not None:
        if new.section_id not in board.sections:
            raise invalid_request(
                f"section_id: Board {board.board_id} has no section {new.section_id}."
            )
        return new.section_id
    if board.inbox or not board.sections:
        return None
    unstarted = (key for key, section in board.sections.items() if section.category == "unstarted")
    section_id = next(unstarted, None)
    if section_id is None:
        raise Problem(
            status=409,
            code="section_required",
            title="Section required",
            detail=f"Board {board.board_id} has no unstarted section for a new task: give as"
            f" section_id one of the sections this problem lists. {DATA_NOT_INSTRUCTIONS}",
            extensions={
                "sections": [
                    {
                        "section_id": key,
                        "category": section.category,
                        "untrusted": {"name": section.name},
                    }
                    for key, section in board.sections.items()
                ]
            },
        )
    return section_id


def _created_partially(board_id: str, key: str, task_id: str, problem: Problem) -> Problem:
    return Problem(
        status=502,
        code="task_created_partially",
        title="Task created partially",
        detail=f"Tasks created task {key}, then failed to set its priority or due date:"
        f" {problem.detail} Do not create it again: set them with update_task.",
        extensions={"board_id": board_id, "task_id": task_id, "key": key},
    )


def _create(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.task.create.v1"])

    @routes.post(
        "/boards/{board_id}/tasks",
        operation_id="create_task",
        status_code=201,
        summary="Create a task in Twake Tasks as the user",
        description=(
            "Creates a task, as the user you act for, on a board they can edit: take its board_id "
            "from open_boards, where their Inbox has inbox true, or from a task the reads give. "
            "It goes at the end of the section given as section_id, else of the board's first "
            "unstarted section, or outside sections on a board without any, such as the Inbox: "
            "otherwise section_required lists the sections to choose from. parent_id makes it a "
            "subtask instead. Its priority and due date are set once it exists: should that "
            "fail, task_created_partially gives its task_id, to set them with update_task. Each "
            "call creates a new task: after an error, look for it with search_tasks before "
            "calling again. Tasks notifies nobody: the user follows the new task. "
            f"{DATA_NOT_INSTRUCTIONS} Example, for a task due on Friday at 5 pm in Paris: "
            'board_id=0199b0c2-5f1e-7a3b-9c4d-2e8f6a1b3c5d, body={"title": "Send the Q4 budget", '
            '"priority": 2, "due_date": "2026-10-09", "due_time": "17:00", '
            '"due_zone": "Europe/Paris"}.'
        ),
        # The user's own task on a board they edit, which notifies nobody: the owner's consent to
        # write in Tasks covers it, and they are not asked to confirm each one
        openapi_extra={"x-twake-risk": "low"},
    )
    async def create_task(
        board_id: BoardId, new: NewTask, user: Annotated[User, Depends(caller)]
    ) -> WrittenTask:
        title = new.title.strip()
        if not title:
            raise invalid_request("title: A task's title cannot be blank.")
        if new.section_id is not None and new.parent_id is not None:
            raise invalid_request(
                "A subtask sits outside sections: give section_id or parent_id, not both."
            )
        dated = new.model_dump(
            mode="json", include={"priority", "due_date", "due_time", "due_zone"}, exclude_none=True
        )
        _check_dates(dated, None)
        board = await _editable(tasks, user, board_id)
        section_id = _section(board, new)
        task_id, key = await tasks.create_task(
            user, board_id, title, section_id=section_id, parent_id=new.parent_id
        )
        if dated:
            # Tasks creates a task from its title alone: it exists, whatever happens next
            changes = {IN_TASKS[name]: value for name, value in dated.items()}
            try:
                await tasks.edit_task(user, board_id, task_id, changes)
            except Problem as problem:
                raise _created_partially(board_id, key, task_id, problem) from problem
        return await _now(tasks, user, board_id, task_id)

    return routes


def _update(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.task.update.v1"])

    @routes.patch(
        "/boards/{board_id}/tasks/{task_id}",
        operation_id="update_task",
        summary="Change a task in Twake Tasks as the user",
        description=(
            "Changes, as the user you act for, a task of a board they can edit: only the fields "
            "given, among title, priority, due_date, due_time, due_zone and deadline, null "
            "clearing one but the title. Clearing due_date clears its time, zone and recurrence "
            "too, as the answer shows. Tasks notifies the other people who follow the task of "
            "each change, in Tasks and by email: by default its creator, its assignees and those "
            f"who commented on it. {DATA_NOT_INSTRUCTIONS} Example, to move a task to Monday with "
            f'the highest priority: {EXAMPLE_IDS}, body={{"due_date": "2026-10-12", '
            '"priority": 1}.'
        ),
        # The user's own edit, though Tasks notifies the task's followers of it: the owner's
        # consent to write in Tasks covers it, and they are not asked to confirm each one
        openapi_extra={"x-twake-risk": "low"},
    )
    async def update_task(
        board_id: BoardId,
        task_id: TaskId,
        changes: TaskChanges,
        user: Annotated[User, Depends(caller)],
    ) -> WrittenTask:
        fields = changes.model_dump(mode="json", exclude_unset=True)
        if not fields:
            raise invalid_request("Give at least one field to change.")
        if "title" in fields:
            if fields["title"] is None or not fields["title"].strip():
                raise invalid_request("title: A task keeps a title, which cannot be blank.")
            fields["title"] = fields["title"].strip()
        board = await _editable(tasks, user, board_id)
        task = _shown(board, task_id)
        _check_dates(fields, task.summary)
        changed = {IN_TASKS[name]: value for name, value in fields.items()}
        await tasks.edit_task(user, board_id, task_id, changed)
        return await _now(tasks, user, board_id, task_id)

    return routes


def _complete(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.task.complete.v1"])

    @routes.post(
        "/boards/{board_id}/tasks/{task_id}/complete",
        operation_id="complete_task",
        summary="Complete a task in Twake Tasks as the user",
        description=(
            "Completes, as the user you act for, a task of a board they can edit, as checking it "
            "off in Tasks does: a task in a section moves to the board's first completed section. "
            "Its open subtasks complete with it. A recurring task is completed for its due date "
            "and stays open, moved to its next due date, which next_due_date gives. A completed "
            "task is left as it is. After an error, read the task before calling again, lest a "
            "recurring task move on twice. Tasks notifies the other people who follow the task "
            "or its subtasks, in Tasks and by email: by default their creators, assignees and "
            f"those who commented on them. {DATA_NOT_INSTRUCTIONS} Example: {EXAMPLE_IDS}."
        ),
        # The user's own work, though Tasks notifies the task's followers of it: the owner's
        # consent to write in Tasks covers it, and they are not asked to confirm each one
        openapi_extra={"x-twake-risk": "low"},
    )
    async def complete_task(
        board_id: BoardId, task_id: TaskId, user: Annotated[User, Depends(caller)]
    ) -> CompletedTask:
        board = await _editable(tasks, user, board_id)
        task = _shown(board, task_id)
        # Nothing to write, and nobody to notify
        if task.summary.state == "completed":
            return _completed(_written(board, task, user.email))
        if task.summary.section_id is None:
            await tasks.complete_task(user, board_id, task_id)
        else:
            # Tasks completes a task in a section by its move to a completed section, as its web
            # app does
            done = (
                key for key, section in board.sections.items() if section.category == "completed"
            )
            section_id = next(done, None)
            if section_id is None:
                raise Problem(
                    status=409,
                    code="no_completed_section",
                    title="No completed section",
                    detail=f"Board {board_id} has no completed section to move the task to: the"
                    " user completes it in Tasks.",
                )
            await tasks.move_task(user, board_id, task_id, section_id)
        return _completed(await _now(tasks, user, board_id, task_id))

    return routes


def routers(tasks: Tasks, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the three contracts, one each."""
    return [_create(tasks, caller), _update(tasks, caller), _complete(tasks, caller)]
