import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from twake_space_agent_contracts import events, problems


def create_app(database_url: str) -> FastAPI:
    pool = AsyncConnectionPool(database_url, open=False)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        yield
        await pool.close()

    app = FastAPI(title="Twake Space agent contracts", lifespan=lifespan)
    problems.install(app)
    app.include_router(events.router(pool))
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn --factory, configured by the environment."""
    return create_app(os.environ["DATABASE_URL"])
