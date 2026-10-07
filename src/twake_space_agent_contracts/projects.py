"""tasks.project.read.v1 and tasks.project.create.v1: the projects of the user in Twake Tasks,
which hold their boards, and the projects they create."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import Language, Previewing, digest_of, one_line, quoted
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.tasks import (
    DATA_NOT_INSTRUCTIONS,
    Board,
    Project,
    Tasks,
    key_prefix_taken,
)

MOST_PROJECTS = 100
LONGEST_NAME = 100
"""The longest name Tasks takes for a board, and so for the project it starts."""
# The start of the keys of a board's tasks, as Tasks takes it, such as WEB for WEB-12
KEY_PREFIX = r"^[A-Z][A-Z0-9]{0,9}$"
INBOX = "INBOX"
"""The key prefix of every Inbox, which Tasks gives no other board."""


class ProjectList(BaseModel):
    projects: list[Project]
    truncated: bool = Field(
        description=f"Whether the user has more projects than the {MOST_PROJECTS} the list holds."
    )


class NewProject(BaseModel):
    """A project to create, with its first board, of the same name."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=LONGEST_NAME,
            description="Plain text, 100 characters at most: the name of the project, and of its "
            "first board.",
        ),
    ]
    key_prefix: Annotated[
        str,
        Field(
            pattern=KEY_PREFIX,
            description="1 to 10 capital letters or digits, starting with a letter, such as WEB: "
            "the keys of the board's tasks start with it, as WEB-1. Tasks keeps INBOX for every "
            "Inbox.",
        ),
    ]


class CreatedProject(Project):
    """A new project, and its first board."""

    board: Board = Field(description="Its first board, of the same name, as open_boards gives it.")


@dataclass(frozen=True)
class _Words:
    """What a preview of a new project tells the owner, in one language."""

    create: str
    namesakes_already: tuple[str, str]
    """That the user has one project of that name already, or several."""
    member: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        create="Créer le projet {name} dans Twake Tasks, avec un premier tableau du même nom, dont"
        " les clés des tâches commencent par {prefix}",
        namesakes_already=(
            "Tu es déjà membre d'un projet de ce nom : celui-ci en sera un autre.",
            "Tu es déjà membre de {count} projets de ce nom : celui-ci en sera un autre.",
        ),
        member="Tu en es le seul membre, avec le rôle administrateur. Tasks ne prévient personne.",
    ),
    "en": _Words(
        create="Create the project {name} in Twake Tasks, with a first board of the same name,"
        " whose task keys start with {prefix}",
        namesakes_already=(
            "You are a member of a project of that name already: this one is another.",
            "You are a member of {count} projects of that name already: this one is another.",
        ),
        member="You are its only member, as its admin. Tasks tells nobody.",
    ),
}


def _compared(name: str) -> str:
    """A project's name as the contract compares it with another's: whatever its case and its
    blanks."""
    return " ".join(name.split()).casefold()


def _creating(name: str, prefix: str, namesake_count: int, language: Language) -> str:
    """What creating the project does, as the owner reads it: its name, its board's, the keys of
    its tasks, and that the user has projects of that name already, if they do."""
    words = _WORDS[language]
    lines = [
        words.create.format(name=quoted(one_line(name, LONGEST_NAME), language), prefix=prefix)
    ]
    if namesake_count:
        one, several = words.namesakes_already
        lines.append(one if namesake_count == 1 else several.format(count=namesake_count))
    lines.append(words.member)
    return "\n".join(lines)


def _read(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.project.read.v1"])

    @routes.get(
        "/projects",
        operation_id="list_projects",
        summary="List the user's projects in Twake Tasks",
        description=(
            "Lists the projects of Twake Tasks that the user you act for is a member of, by name, "
            f"{MOST_PROJECTS} at most, with their role in each: a viewer only reads and comments. "
            "Their personal project, personal true, holds their Inbox and is never shared. space "
            "marks the project of a Twake Space they are a member of, whose members are the "
            "space's. It only reads: a project the user was invited to comes once open_boards "
            "made them a member. The boards of a project are those open_boards gives with its "
            f"project_id. {DATA_NOT_INSTRUCTIONS} Example: (no parameters)."
        ),
    )
    async def list_projects(user: Annotated[User, Depends(caller)]) -> ProjectList:
        projects = await tasks.projects(user)
        return ProjectList(
            projects=projects[:MOST_PROJECTS], truncated=len(projects) > MOST_PROJECTS
        )

    return routes


def _create(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.project.create.v1"])

    @routes.post(
        "/projects",
        operation_id="create_project",
        status_code=201,
        summary="Create a project in Twake Tasks as the user",
        description=(
            "Creates a project in Twake Tasks, as the user you act for, with its first board, of "
            "the same name, whose task keys start with key_prefix, as LAUNCH-1: Tasks creates a "
            "project only with a board, which create_task takes tasks on by its board_id. The "
            "user is the project's only member, as its admin, and Tasks notifies nobody. Each "
            "call creates a new project, even of a name the user has already: after an error, "
            "look for it with list_projects before calling again. A key prefix Tasks keeps for "
            "another board, such as INBOX, answers key_prefix_taken. "
            f"{DATA_NOT_INSTRUCTIONS} Example, for the launch of a product: "
            'body={"name": "Q4 launch", "key_prefix": "LAUNCH"}.'
        ),
        response_model=CreatedProject,
        # A new project the user alone is a member of, which notifies nobody: the owner's consent
        # to write in Tasks covers it, and they are not asked to confirm each one. It tells what
        # it would do, for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def create_project(
        new: NewProject, user: Annotated[User, Depends(caller)], preview: Previewing
    ) -> CreatedProject | JSONResponse:
        name = new.name.strip()
        if not name:
            raise invalid_request("name: A project's name cannot be blank.")
        # Refused before the owner is asked: Tasks keeps it for every Inbox
        if new.key_prefix == INBOX:
            raise key_prefix_taken(INBOX)
        # Tasks takes a project of a name the user has already as another one
        compared = _compared(name)
        namesake_ids = sorted(
            found.project_id
            for found in await tasks.projects(user)
            if _compared(found.untrusted.name) == compared
        )
        # What the owner allows: a project of that name, beside those they have of it already
        digest = digest_of(compared, new.key_prefix, namesake_ids)
        if preview.asked:
            summary = _creating(name, new.key_prefix, len(namesake_ids), preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        project, board = await tasks.create_project(user, name, new.key_prefix)
        return CreatedProject(**project.model_dump(), board=board)

    return routes


def routers(tasks: Tasks, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the projects' contracts, one each."""
    return [_read(tasks, caller), _create(tasks, caller)]
