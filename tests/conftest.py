from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Protocol

import psycopg
import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from psycopg.types.json import Jsonb
from testcontainers.community.postgres import PostgresContainer

from twake_space_agent_contracts.app import create_app

SCHEMA = Path(__file__).parent.parent / "sql" / "workplace_events.sql"

INVITED = "com.twake.calendar.event.invited.v1"


class Store(Protocol):
    async def __call__(self, event: dict[str, Any]) -> None: ...


def invitation(event_id: str, *, targets: list[str], time: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": INVITED,
        "org": "linagora",
        "actor": "e2e.organizer",
        "targets": targets,
        "subject": f"calendars/e2e.organizer/{event_id}.ics",
        "time": time,
        "data": {
            "object": {"title": "Point Twake Space E2E", "start": "2026-10-13T17:00:00+02:00"}
        },
    }


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:18-alpine", driver=None) as postgres:
        url = postgres.get_connection_url()
        with psycopg.connect(url) as connection:
            connection.execute(SCHEMA.read_text())
        yield url


@pytest.fixture
async def store(database_url: str) -> AsyncIterator[Store]:
    """Arranges stored events the way storage would have written them."""
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as connection:
        await connection.execute("TRUNCATE workplace_events")

        async def insert(event: dict[str, Any]) -> None:
            await connection.execute(
                "INSERT INTO workplace_events (id, type, org, actor, targets, subject, time, data)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    event["id"],
                    event["type"],
                    event["org"],
                    event["actor"],
                    event["targets"],
                    event["subject"],
                    event["time"],
                    Jsonb(event["data"]),
                ),
            )

        yield insert


@pytest.fixture
async def client(database_url: str) -> AsyncIterator[AsyncClient]:
    app = create_app(database_url)
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://contracts"
        ) as client,
    ):
        yield client
