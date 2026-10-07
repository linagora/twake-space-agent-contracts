"""tasks.task.create.v1, tasks.task.update.v1, tasks.task.complete.v1, tasks.task.delete.v1 and
tasks.task.assign.v1: the user creates, changes, completes, deletes and assigns tasks in Twake
Tasks, as themselves, on the boards they can edit."""

from dataclasses import dataclass
from datetime import date, time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.json_schema import SkipJsonSchema

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    Previewing,
    day,
    digest_of,
    one_line,
    person,
    quoted,
    shown_size,
    time_of_day,
)
from twake_space_agent_contracts.problems import Problem, invalid_request
from twake_space_agent_contracts.tasks import (
    DATA_NOT_INSTRUCTIONS,
    TASKS_ID,
    BoardContent,
    BoardTask,
    Section,
    Tasks,
    TaskSummary,
    TaskText,
    board_archived,
    board_not_found,
    forbidden_role,
    owner_not_member,
    task_not_found,
)
from twake_space_agent_contracts.zones import ZONE, known_zone

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
MOST_ASSIGNEES = 50
"""The most people Tasks assigns a task to."""
SHOWN_SUBTASKS = 10
"""How many of the subtasks that go with a deleted task its preview names, at most."""
MOST_PEOPLE = 10
"""How many members a preview names in a list, at most."""
PEOPLE_SIZE = BUDGET // 6
"""What the members a preview names in a list take of its summary at most, as the harness counts
it: so that members of a board, however many and however long their emails, leave room for the
rest."""


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
    AfterValidator(known_zone),
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
Email = Annotated[str, Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")]


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


class Assignment(BaseModel):
    """Whom a task is assigned to: all of them."""

    model_config = ConfigDict(extra="forbid")

    assignees: Annotated[
        list[Email],
        Field(
            max_length=MOST_ASSIGNEES,
            description="The emails the members to assign it to joined with, 50 at most: the "
            "whole list, those to keep with the new ones, [] for nobody.",
        ),
    ]


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


class DeletedTask(WrittenTask):
    """The task as Tasks showed it before it went to the trash."""

    deleted_subtasks: int = Field(
        description="How many of its subtasks, at any depth, went to the trash with it."
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


def _members(board: BoardContent, emails: list[str]) -> list[str]:
    """The user ids of the members who joined with these emails, whatever their case, in their
    order and each once: a task is assigned to members of its board alone."""
    chosen: list[str] = []
    unknown: list[str] = []
    for email in dict.fromkeys(email.lower() for email in emails):
        member = board.member_named(email)
        if member is None:
            unknown.append(email)
        elif member not in chosen:
            chosen.append(member)
    if unknown:
        # Tasks does not say who a member is but by the email they joined with: one alone tells
        members = sorted({email for _, email in board.members if board.member_named(email)})
        raise Problem(
            status=409,
            code="assignee_not_member",
            title="Assignee not a member",
            detail=f"No member of board {board.board_id}, or more than one, joined with"
            f" {', '.join(unknown)}: a task is assigned to members of its board alone, which"
            " members lists by the emails assign_task takes.",
            extensions={"members": members},
        )
    return chosen


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


@dataclass(frozen=True)
class _Words:
    """What a preview of a write in Tasks tells the owner, in one language."""

    create: str
    in_section: str
    create_subtask: str
    change: str
    complete: str
    to_section: str
    for_due_date: str
    completed: str
    subtask: str
    subtasks: str
    told: str
    title: str
    priority: str
    due: str
    deadline: str
    field: str
    instead: str
    instead_of_none: str
    none: str
    at: str
    recurrence_cleared: str
    delete: str
    deleted_subtasks: tuple[str, str]
    """For one subtask, and for several."""
    other_subtasks: tuple[str, str]
    """How many of the subtasks the summary does not name: for one, and for several."""
    assign: str
    assigned: str
    unassigned: str
    still_assigned: str
    assigned_already: str
    told_assigned: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        create="Créer la tâche {title} dans le tableau {board}",
        in_section=", section {section}",
        create_subtask="Créer la sous-tâche {title} de la tâche {key} {parent}, dans le tableau"
        " {board}",
        change="Modifier la tâche {key} {title} du tableau {board} :",
        complete="Terminer la tâche {key} {title} du tableau {board}",
        to_section=" : elle passe dans la section {section}",
        for_due_date=" pour son échéance du {due} : elle se répète, et reste ouverte pour la"
        " suivante",
        completed="La tâche {key} {title} du tableau {board} est déjà terminée : rien ne change.",
        subtask="Sa sous-tâche ouverte est terminée avec elle.",
        subtasks="Ses {count} sous-tâches ouvertes sont terminées avec elle.",
        told="Tasks prévient ceux qui suivent la tâche, dans Tasks et par mail.",
        title="Titre",
        priority="Priorité",
        due="Échéance",
        deadline="Date limite",
        field="{label} : {value}",
        instead="{label} : {value}, au lieu de {before}",
        instead_of_none="{label} : {value}, au lieu d'aucune",
        none="aucune",
        at="{day} à {time}",
        recurrence_cleared="Sa récurrence est effacée aussi.",
        delete="Supprimer la tâche {key} {title} du tableau {board} : elle passe dans la corbeille"
        " du tableau, d'où un éditeur ou un administrateur du tableau peut la restaurer pendant 30"
        " jours, avant que Tasks la supprime définitivement",
        deleted_subtasks=(
            "Sa sous-tâche part avec elle :",
            "Ses {count} sous-tâches partent avec elle :",
        ),
        other_subtasks=("- et 1 autre", "- et {count} autres"),
        assign="Assigner la tâche {key} {title} du tableau {board} :",
        assigned="Désormais assignée à {people}",
        unassigned="Plus assignée à {people}",
        still_assigned="Toujours assignée à {people}",
        assigned_already="La tâche {key} {title} du tableau {board} est déjà assignée ainsi :"
        " rien ne change.",
        told_assigned="Tasks prévient chaque nouveau responsable et ceux qui suivent la tâche,"
        " dans Tasks et par mail.",
    ),
    "en": _Words(
        create="Create the task {title} on the board {board}",
        in_section=", in the section {section}",
        create_subtask="Create the subtask {title} of the task {key} {parent}, on the board"
        " {board}",
        change="Change the task {key} {title} on the board {board}:",
        complete="Complete the task {key} {title} on the board {board}",
        to_section=": it moves to the section {section}",
        for_due_date=" for its due date, {due}: it repeats, so it stays open for the next one",
        completed="The task {key} {title} on the board {board} is completed already: nothing"
        " changes.",
        subtask="Its open subtask is completed with it.",
        subtasks="Its {count} open subtasks are completed with it.",
        told="Tasks tells the people who follow the task, in Tasks and by email.",
        title="Title",
        priority="Priority",
        due="Due",
        deadline="Deadline",
        field="{label}: {value}",
        instead="{label}: {value}, instead of {before}",
        instead_of_none="{label}: {value}, instead of none",
        none="none",
        at="{day} at {time}",
        recurrence_cleared="Its recurrence is cleared too.",
        delete="Delete the task {key} {title} from the board {board}: it goes to the board's"
        " trash, where an editor or an admin of the board can restore it for 30 days, before Tasks"
        " deletes it for good",
        deleted_subtasks=("Its subtask goes with it:", "Its {count} subtasks go with it:"),
        other_subtasks=("- and 1 other", "- and {count} others"),
        assign="Assign the task {key} {title} on the board {board}:",
        assigned="Assigned now to {people}",
        unassigned="No longer assigned to {people}",
        still_assigned="Still assigned to {people}",
        assigned_already="The task {key} {title} on the board {board} is assigned so already:"
        " nothing changes.",
        told_assigned="Tasks tells each new assignee and the people who follow the task, in"
        " Tasks and by email.",
    ),
}


def _named(text: str | None, language: Language, longest: int = 200) -> str:
    """A name or a title members wrote, as a preview shows it."""
    return quoted(one_line(text, longest), language)


def _due(on: date | str | None, at: str | None, zone: str | None, language: Language) -> str | None:
    """A due date as the owner reads it, with its time and zone when it has them."""
    if on is None:
        return None
    shown = day(date.fromisoformat(on) if isinstance(on, str) else on, language)
    if at:
        try:
            clock = time_of_day(time.fromisoformat(at), language)
        except ValueError:
            # A time Tasks wrote otherwise reads as it is
            clock = one_line(at)
        shown = _WORDS[language].at.format(day=shown, time=clock)
    return f"{shown} ({one_line(zone)})" if zone else shown


def members_named(emails: list[str], language: Language) -> str:
    """Members by the email they joined with, as a preview names them: the first ones, ten at most
    and as many as fit in what a list takes of the summary, then how many others."""
    named: list[str] = []
    for email in emails[:MOST_PEOPLE]:
        found = person(None, email, language)
        if found is None:
            continue
        if shown_size(", ".join([*named, found])) > PEOPLE_SIZE:
            break
        named.append(found)
    others = len(emails) - len(named)
    if not others:
        return ", ".join(named)
    if not named:
        if language == "fr":
            return "1 membre" if others == 1 else f"{others} membres"
        return "1 member" if others == 1 else f"{others} members"
    if language == "fr":
        rest = "1 autre" if others == 1 else f"{others} autres"
        return f"{', '.join(named)} et {rest}"
    rest = "1 other" if others == 1 else f"{others} others"
    return f"{', '.join(named)} and {rest}"


def _change(label: str, value: str | None, before: str | None, words: _Words) -> str:
    """A field as a change leaves it, and as it was."""
    shown = words.none if value is None else value
    if before is None:
        return words.instead_of_none.format(label=label, value=shown)
    return words.instead.format(label=label, value=shown, before=before)


def task_names(board: BoardContent, task: BoardTask, language: Language) -> dict[str, str]:
    """How a preview names a task: by its key and title, on its board."""
    return {
        "key": one_line(task.summary.key),
        "title": _named(task.summary.untrusted.title, language),
        "board": _named(board.name, language),
    }


def _acted_on(task: BoardTask) -> dict[str, Any]:
    """What a change or a completion acts on, as the board shows the task: its title, its state
    and place, and the fields the contracts change."""
    summary = task.summary
    return summary.model_dump(
        mode="json",
        include={
            "task_id",
            "state",
            "section_id",
            "parent_id",
            "priority",
            "due_date",
            "due_time",
            "due_zone",
            "deadline",
        },
    ) | {"title": summary.untrusted.title, "recurring": task.recurring}


def _created(
    board: BoardContent,
    title: str,
    new: NewTask,
    section: Section | None,
    parent: BoardTask | None,
    language: Language,
) -> str:
    """What creating the task does, as the owner reads it: where it goes, and when it is due."""
    words = _WORDS[language]
    named = {"title": _named(title, language, LONGEST_TITLE), "board": _named(board.name, language)}
    if parent is not None:
        summary = parent.summary
        line = words.create_subtask.format(
            **named,
            key=one_line(summary.key),
            parent=_named(summary.untrusted.title, language),
        )
    else:
        line = words.create.format(**named)
        if section is not None:
            line += words.in_section.format(section=_named(section.name, language))
    lines = [line]
    if new.priority is not None:
        lines.append(words.field.format(label=words.priority, value=new.priority))
    due = _due(new.due_date, new.due_time, new.due_zone, language)
    if due is not None:
        lines.append(words.field.format(label=words.due, value=due))
    return "\n".join(lines)


def _changed(
    board: BoardContent, task: BoardTask, fields: dict[str, Any], language: Language
) -> str:
    """What changing the task does, as the owner reads it: each field it changes, as it was."""
    words = _WORDS[language]
    now = task.summary
    lines = [words.change.format(**task_names(board, task, language))]
    if "title" in fields:
        title = _named(fields["title"], language, LONGEST_TITLE)
        lines.append(_change(words.title, title, _named(now.untrusted.title, language), words))
    if "priority" in fields:
        before = None if now.priority is None else str(now.priority)
        after = None if fields["priority"] is None else str(fields["priority"])
        lines.append(_change(words.priority, after, before, words))
    if fields.keys() & {"due_date", "due_time", "due_zone"}:
        # As Tasks keeps them: no time without a date, nor a zone without a time
        on = fields.get("due_date", now.due_date)
        at = None if on is None else fields.get("due_time", now.due_time)
        zone = None if at is None else fields.get("due_zone", now.due_zone)
        before = _due(now.due_date, now.due_time, now.due_zone, language)
        lines.append(_change(words.due, _due(on, at, zone, language), before, words))
        if on is None and task.recurring:
            lines.append(words.recurrence_cleared)
    if "deadline" in fields:
        before = _due(now.deadline, None, None, language)
        after = _due(fields["deadline"], None, None, language)
        lines.append(_change(words.deadline, after, before, words))
    lines.append(words.told)
    return "\n".join(lines)


def _subtasks(board: BoardContent, task_id: str) -> list[str]:
    """The ids of the tasks under that one, at any depth, as the board shows them: each followed by
    its own, in the board's order."""
    children: dict[str, list[str]] = {}
    for key, item in board.tasks.items():
        parent = item.get("parentId") if isinstance(item, dict) else None
        if isinstance(parent, str):
            children.setdefault(parent, []).append(key)
    under: list[str] = []
    next_ones = list(reversed(children.get(task_id, [])))
    while next_ones:
        key = next_ones.pop()
        if key not in under and key != task_id:
            under.append(key)
            next_ones.extend(reversed(children.get(key, [])))
    return under


def _open_subtasks(board: BoardContent, task_id: str) -> list[str]:
    """The ids of the open tasks under that one, at any depth, as the board shows them: sorted, so
    that the same subtasks, listed in another order, make the same digest."""
    return sorted(
        key
        for key in _subtasks(board, task_id)
        if not board.tasks[key].get("completedAt") and not board.tasks[key].get("canceledAt")
    )


def _completing(
    board: BoardContent, task: BoardTask, done: str | None, subtasks: int, language: Language
) -> str:
    """What completing the task does, as the owner reads it: where it goes, or the due date it
    moves on from, and the subtasks it completes with it."""
    words = _WORDS[language]
    names = task_names(board, task, language)
    summary = task.summary
    if summary.state == "completed":
        return words.completed.format(**names)
    line = words.complete.format(**names)
    lines = [line]
    if task.recurring and summary.due_date is not None:
        due = _due(summary.due_date, summary.due_time, summary.due_zone, language)
        lines[0] += words.for_due_date.format(due=due)
    else:
        if done is not None:
            lines[0] += words.to_section.format(section=_named(board.sections[done].name, language))
        if subtasks:
            lines.append(words.subtask if subtasks == 1 else words.subtasks.format(count=subtasks))
    lines.append(words.told)
    return "\n".join(lines)


def _deleting(
    board: BoardContent, task: BoardTask, subtasks: list[BoardTask], language: Language
) -> str:
    """What deleting the task does, as the owner reads it: where it goes, for how long, and the
    subtasks that go with it, each on a line of its own by its key and title, which members wrote,
    ten at most and as many as fit in the summary, then how many others."""
    words = _WORDS[language]
    lines = [words.delete.format(**task_names(board, task, language))]
    if not subtasks:
        return lines[0]
    one, several = words.deleted_subtasks
    lines.append(one if len(subtasks) == 1 else several.format(count=len(subtasks)))

    def others(named: int) -> list[str]:
        """The line that counts the subtasks left unnamed."""
        unnamed = len(subtasks) - named
        one, several = words.other_subtasks
        return [one if unnamed == 1 else several.format(count=unnamed)] if unnamed else []

    named: list[str] = []
    for subtask in subtasks[:SHOWN_SUBTASKS]:
        summary = subtask.summary
        line = f"- {one_line(summary.key)} {_named(summary.untrusted.title, language)}"
        if shown_size("\n".join([*lines, *named, line, *others(len(named) + 1)])) > BUDGET:
            break
        named.append(line)
    return "\n".join([*lines, *named, *others(len(named))])


def _assigning(board: BoardContent, task: BoardTask, chosen: list[str], language: Language) -> str:
    """What assigning the task does, as the owner reads it: whom it is assigned to now, no longer
    and still, by the emails they joined with, and whom Tasks tells."""
    words = _WORDS[language]
    names = task_names(board, task, language)
    now = task.assignee_ids
    if set(chosen) == now:
        return words.assigned_already.format(**names)
    joined = dict(board.members)
    lines = [words.assign.format(**names)]
    for line, user_ids in (
        (words.assigned, [user_id for user_id in chosen if user_id not in now]),
        (words.unassigned, [user_id for user_id in now if user_id not in chosen]),
        (words.still_assigned, [user_id for user_id in chosen if user_id in now]),
    ):
        # By email, whatever order the call gave them in
        emails = sorted(joined[user_id] for user_id in user_ids if user_id in joined)
        if emails:
            lines.append(line.format(people=members_named(emails, language)))
    added = any(user_id not in now for user_id in chosen)
    lines.append(words.told_assigned if added else words.told)
    return "\n".join(lines)


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
        response_model=WrittenTask,
        # The user's own task on a board they edit, which notifies nobody: the owner's consent to
        # write in Tasks covers it, and they are not asked to confirm each one. It tells what it
        # would do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def create_task(
        board_id: BoardId,
        new: NewTask,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> WrittenTask | JSONResponse:
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
        section = board.sections.get(section_id) if section_id is not None else None
        parent = board.task(new.parent_id) if new.parent_id is not None else None
        # What the owner allows: where the new task goes
        digest = digest_of(
            board.board_id,
            board.name,
            section_id,
            section.name if section else None,
            new.parent_id,
            parent.summary.untrusted.title if parent else None,
        )
        if preview.asked:
            summary = _created(board, title, new, section, parent, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
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
        response_model=WrittenTask,
        # The user's own edit, though Tasks notifies the task's followers of it: the owner's
        # consent to write in Tasks covers it, and they are not asked to confirm each one. It
        # tells what it would do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def update_task(
        board_id: BoardId,
        task_id: TaskId,
        changes: TaskChanges,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> WrittenTask | JSONResponse:
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
        # What the owner allows: the task as it is
        digest = digest_of(board_id, _acted_on(task))
        if preview.asked:
            return preview.answer(_changed(board, task, fields, preview.language), digest)
        preview.check(digest)
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
        response_model=CompletedTask,
        # The user's own work, though Tasks notifies the task's followers of it: the owner's
        # consent to write in Tasks covers it, and they are not asked to confirm each one. It
        # tells what it would do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def complete_task(
        board_id: BoardId,
        task_id: TaskId,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> CompletedTask | JSONResponse:
        board = await _editable(tasks, user, board_id)
        task = _shown(board, task_id)
        completed = task.summary.state == "completed"
        done = None
        if not completed and task.summary.section_id is not None:
            # Tasks completes a task in a section by its move to a completed section, as its web
            # app does
            done = next(
                (key for key, section in board.sections.items() if section.category == "completed"),
                None,
            )
            if done is None:
                raise Problem(
                    status=409,
                    code="no_completed_section",
                    title="No completed section",
                    detail=f"Board {board_id} has no completed section to move the task to: the"
                    " user completes it in Tasks.",
                )
        subtasks = _open_subtasks(board, task_id)
        # What the owner allows: the task as it is, where it goes, and the subtasks it takes along
        digest = digest_of(board_id, _acted_on(task), done, subtasks)
        if preview.asked:
            summary = _completing(board, task, done, len(subtasks), preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        # Nothing to write, and nobody to notify
        if completed:
            return _completed(_written(board, task, user.email))
        if done is None:
            await tasks.complete_task(user, board_id, task_id)
        else:
            await tasks.move_task(user, board_id, task_id, done)
        return _completed(await _now(tasks, user, board_id, task_id))

    return routes


def _delete(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.task.delete.v1"])

    @routes.delete(
        "/boards/{board_id}/tasks/{task_id}",
        operation_id="delete_task",
        summary="Delete a task in Twake Tasks as the user",
        description=(
            "Deletes, as the user you act for, a task of a board they can edit, with its "
            "subtasks: they go to the board's trash, where an editor or an admin of the board "
            "can restore them in Tasks for 30 days, before Tasks deletes them for good. Call it "
            "only once the user asked "
            "to delete this very task; they confirm each call. It answers the task as it was, "
            "and how many of its subtasks went with it. An archived task, or one in the trash, "
            f"is not found. {DATA_NOT_INSTRUCTIONS} Example: {EXAMPLE_IDS}."
        ),
        response_model=DeletedTask,
        # The task leaves the board, with its subtasks, and Tasks deletes it for good once 30 days
        # have passed in the trash: the owner confirms each one, shown what it would delete
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def delete_task(
        board_id: BoardId,
        task_id: TaskId,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> DeletedTask | JSONResponse:
        board = await _editable(tasks, user, board_id)
        task = _shown(board, task_id)
        subtasks = [_shown(board, key) for key in _subtasks(board, task_id)]
        # What the owner allows: the task as it is, and the subtasks that go with it, by their
        # titles, sorted, so that the same subtasks, listed in another order, make the same digest
        titled = sorted([each.summary.task_id, each.summary.untrusted.title] for each in subtasks)
        digest = digest_of(board_id, _acted_on(task), titled)
        if preview.asked:
            return preview.answer(_deleting(board, task, subtasks, preview.language), digest)
        preview.check(digest)
        deleted = _written(board, task, user.email)
        await tasks.trash_task(user, board_id, task_id)
        return DeletedTask(**deleted.model_dump(), deleted_subtasks=len(subtasks))

    return routes


def _assign(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.task.assign.v1"])

    @routes.put(
        "/boards/{board_id}/tasks/{task_id}/assignees",
        operation_id="assign_task",
        summary="Assign a task in Twake Tasks as the user",
        description=(
            "Assigns, as the user you act for, a task of a board they can edit to members of the "
            "board, by the emails they joined with, as read_task gives its assignees: assignees "
            "replaces the whole list, so give those to keep with the new ones, and [] to assign "
            "it to nobody. Tasks tells each new assignee, in Tasks and by email, who then follows "
            "the task, and the other people who follow it. A task is assigned to members of its "
            "board alone: assignee_not_member lists those it takes. Call it only once the user "
            "asked for this very assignment; they confirm each call. An archived task, or one in "
            f"the trash, is not found. {DATA_NOT_INSTRUCTIONS} Example, to assign a task to Alice "
            f'besides Michel: {EXAMPLE_IDS}, body={{"assignees": ["alice@example.com", '
            '"michel@example.com"]}.'
        ),
        response_model=WrittenTask,
        # Tasks tells each new assignee, by email too, who then follows the task: the owner
        # confirms each assignment, shown whom it would go to
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def assign_task(
        board_id: BoardId,
        task_id: TaskId,
        assignment: Assignment,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> WrittenTask | JSONResponse:
        board = await _editable(tasks, user, board_id)
        task = _shown(board, task_id)
        chosen = _members(board, assignment.assignees)
        # What the owner allows: the task as it is, whom it is assigned to, and whom it would be
        digest = digest_of(board_id, _acted_on(task), sorted(task.assignee_ids), sorted(chosen))
        if preview.asked:
            return preview.answer(_assigning(board, task, chosen, preview.language), digest)
        preview.check(digest)
        # Nothing to write, and nobody to tell
        if set(chosen) == task.assignee_ids:
            return _written(board, task, user.email)
        await tasks.assign(user, board_id, task_id, chosen)
        return await _now(tasks, user, board_id, task_id)

    return routes


def routers(tasks: Tasks, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the contracts, one each."""
    return [
        _create(tasks, caller),
        _update(tasks, caller),
        _complete(tasks, caller),
        _delete(tasks, caller),
        _assign(tasks, caller),
    ]
