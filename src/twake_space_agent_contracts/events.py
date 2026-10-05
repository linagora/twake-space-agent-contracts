from datetime import datetime
from typing import Any

from fastapi import APIRouter
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from twake_space_agent_contracts.caller import Caller
from twake_space_agent_contracts.problems import Problem


class Event(BaseModel):
    id: str
    type: str
    time: datetime
    org: str | None
    actor: str | None
    targets: list[str]
    subject: str | None
    data: dict[str, Any]


def router(pool: AsyncConnectionPool) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/events")

    @routes.get("/{event_id}")
    async def read_event(event_id: str, caller: Caller) -> Event:
        async with pool.connection() as connection:
            cursor = connection.cursor(row_factory=class_row(Event))
            await cursor.execute(
                "SELECT id, type, time, org, actor, targets, subject, data FROM workplace_events"
                " WHERE id = %s AND targets @> ARRAY[%s::text]",
                (event_id, caller),
            )
            event = await cursor.fetchone()
        if event is None:
            raise Problem(
                status=404,
                code="event_not_found",
                title="Event not found",
                detail=f"No event {event_id} concerns this user.",
            )
        return event

    return routes
