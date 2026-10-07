"""tasks.project.read.v1: the projects of the user in Twake Tasks, which hold their boards."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.tasks import DATA_NOT_INSTRUCTIONS, Project, Tasks

MOST_PROJECTS = 100


class ProjectList(BaseModel):
    projects: list[Project]
    truncated: bool = Field(
        description=f"Whether the user has more projects than the {MOST_PROJECTS} the list holds."
    )


def _read(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.project.read.v1"])

    @routes.get(
        "/projects",
        operation_id="list_projects",
        summary="List the user's projects in Twake Tasks",
        description=(
            "Lists the projects of Twake Tasks that the user you act for is a member of, by name, "
            f"{MOST_PROJECTS} at most, with their role in each: a viewer only reads. Their "
            "personal project, personal true, holds their Inbox and is never shared. space "
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


def routers(tasks: Tasks, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the projects' contracts, one each."""
    return [_read(tasks, caller)]
