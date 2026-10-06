import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from twake_space_agent_contracts import events, freebusy, problems
from twake_space_agent_contracts.caller import TokenVerifier, caller_dependency
from twake_space_agent_contracts.settings import Settings


def create_app(
    database_url: str,
    settings: Settings,
    http: httpx.AsyncClient | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    pool = AsyncConnectionPool(database_url, open=False)
    http = http or httpx.AsyncClient(timeout=10)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        try:
            yield
        finally:
            await pool.close()
            await http.aclose()

    caller = caller_dependency(TokenVerifier(settings, http, clock))
    app = FastAPI(title="Twake Space agent contracts", lifespan=lifespan)
    problems.install(app)
    app.include_router(events.router(pool, caller))
    app.include_router(freebusy.router(freebusy.Calendar(settings.calendar_url, http), caller))
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn --factory, configured by the environment."""
    return create_app(os.environ["DATABASE_URL"], Settings.from_env())
