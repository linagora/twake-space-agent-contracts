"""tasks.board.read.v1: the boards of the user's projects in Twake Tasks."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.tasks import DATA_NOT_INSTRUCTIONS, Board, Tasks

MOST_BOARDS = 100


class BoardList(BaseModel):
    boards: list[Board]
    truncated: bool = Field(
        description=f"Whether the user has more boards than the {MOST_BOARDS} the list holds."
    )


def router(tasks: Tasks, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.board.read.v1"])

    @routes.get(
        "/boards",
        operation_id="list_boards",
        summary="List the user's boards in Twake Tasks",
        description=(
            "Lists the boards of the projects the user you act for is a member of, their Inbox "
            f"first, then by name, {MOST_BOARDS} at most, with the user's role on each: a viewer "
            "only reads. Like opening Twake Tasks, it creates the user's Inbox if they have none "
            "yet, and makes them a member of the projects they were invited to. "
            f"{DATA_NOT_INSTRUCTIONS} Example, for the boards the user still works on: "
            "include_archived=false."
        ),
    )
    async def list_boards(
        user: Annotated[User, Depends(caller)],
        include_archived: Annotated[
            bool, Query(description="Whether to list archived boards too, false by default.")
        ] = False,
    ) -> BoardList:
        boards = [
            board for board in await tasks.boards(user) if include_archived or not board.archived
        ]
        return BoardList(boards=boards[:MOST_BOARDS], truncated=len(boards) > MOST_BOARDS)

    return routes
