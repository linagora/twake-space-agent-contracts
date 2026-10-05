from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header
from psycopg.rows import class_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel


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
    async def read_event(event_id: str, x_twake_user: Annotated[str, Header()]) -> Event:
        async with pool.connection() as connection:
            cursor = connection.cursor(row_factory=class_row(Event))
            await cursor.execute(
                "SELECT id, type, time, org, actor, targets, subject, data FROM workplace_events"
                " WHERE id = %s AND targets @> ARRAY[%s::text]",
                (event_id, x_twake_user),
            )
            event = await cursor.fetchone()
        assert event is not None
        return event

    return routes
