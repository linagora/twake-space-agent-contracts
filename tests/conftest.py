from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

import httpx
import psycopg
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from psycopg.types.json import Jsonb
from testcontainers.community.postgres import PostgresContainer

from tests.fakes import SETTINGS, FakeBoundary, FakeClock, as_user, email_of
from twake_space_agent_contracts.app import create_app
from twake_space_agent_contracts.settings import Settings

SCHEMA = Path(__file__).parent.parent / "sql" / "workplace_events.sql"

INVITED = "com.twake.calendar.event.invited.v1"

AS_MMAUDET = as_user(email_of("mmaudet"))


class Store(Protocol):
    async def __call__(self, event: dict[str, Any]) -> None: ...


def invitation(
    event_id: str, *, targets: list[str], time: str, uid: str | None = None
) -> dict[str, Any]:
    """A stored invitation, as the calendar producer and the normalizer write it; with the UID of
    the calendar event when a test reaches Calendar."""
    event: dict[str, Any] = {
        "id": event_id,
        "type": INVITED,
        "org": "linagora",
        "actor": "e2e.organizer",
        "targets": targets,
        "subject": f"calendars/e2e.organizer/{event_id}.ics",
        "time": time,
        "data": {
            "object": {"title": "Point Twake Space E2E", "start": "2026-10-13T17:00:00+02:00"},
            "targets": [
                {"uid": target, "native_id": email_of(target), "role": "invitee"}
                for target in targets
            ],
        },
    }
    if uid is not None:
        event["data"]["object"]["uid"] = uid
    return event


async def pages_of(
    client: AsyncClient,
    path: str,
    params: dict[str, str],
    *,
    items: str,
    key: str,
    cursor: str = "cursor",
) -> list[list[str]]:
    """The key of each item of a list as the user, page by page, passing the next of each answer
    as the cursor: ten pages at most, so that a cursor that never ends fails."""
    pages: list[list[str]] = []
    params = dict(params)
    for _ in range(10):
        response = await client.get(path, params=params, headers=AS_MMAUDET)
        assert response.status_code == 200, response.text
        answer = response.json()
        pages.append([item[key] for item in answer[items]])
        if answer["next"] is None:
            return pages
        params[cursor] = answer["next"]
    raise AssertionError(f"More than ten pages: {pages}")


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
def boundary() -> FakeBoundary:
    return FakeBoundary()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@asynccontextmanager
async def serving(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A client of the service's HTTP API, the service started as uvicorn starts it."""
    async with (
        LifespanManager(app) as manager,
        AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://contracts"
        ) as client,
    ):
        yield client


Serve = Callable[[Settings], AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def serve(database_url: str, boundary: FakeBoundary, clock: FakeClock) -> Serve:
    """Starts the service with the given settings, what it reaches over HTTP faked."""

    def start(settings: Settings) -> AbstractAsyncContextManager[AsyncClient]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(boundary.handle))
        return serving(create_app(database_url, settings, http=http, clock=clock))

    return start


@pytest.fixture
async def client(serve: Serve) -> AsyncIterator[AsyncClient]:
    async with serve(SETTINGS) as client:
        yield client


def operations_of(document: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """Each operation of an OpenAPI document: its path, its method as OpenAPI writes it, and
    itself."""
    return [
        (path, method, operation)
        for path, item in document["paths"].items()
        for method, operation in item.items()
    ]
