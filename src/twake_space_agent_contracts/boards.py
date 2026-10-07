"""tasks.board.open.v1: the user opens Twake Tasks, which lists the boards of their projects."""

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
    routes = APIRouter(prefix="/contracts/v1/tasks", tags=["tasks.board.open.v1"])

    @routes.post(
        "/boards/open",
        operation_id="open_boards",
        summary="Open Twake Tasks as the user, and list their boards",
        description=(
            "Opens Twake Tasks as the user, as its web app does when they open it: the first time "
            "it sets up their Inbox, and it makes them a member of the boards they were invited "
            "to. Then lists their boards: their Inbox first, then their favorite boards, then the "
            f"others by name, {MOST_BOARDS} at most, with the user's role on each: a viewer only "
            f"reads. Tasks notifies nobody. {DATA_NOT_INSTRUCTIONS} Example, for the boards the "
            "user still works on: include_archived=false."
        ),
        # The user's own Inbox and the invitations made to them: the owner's consent to write in
        # Tasks covers it, and they are not asked to confirm each time
        openapi_extra={"x-twake-risk": "low"},
    )
    async def open_boards(
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
