import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from psycopg_pool import AsyncConnectionPool

from twake_space_agent_contracts import applications, problems
from twake_space_agent_contracts.caller import TokenVerifier, caller_dependency
from twake_space_agent_contracts.settings import Settings


class Contracts(FastAPI):
    """The service, whose OpenAPI document also names, at its root, each application it publishes
    in the words the harness asks their owners with (x-twake-domains)."""

    def __init__(self, domains: dict[str, applications.Description], **options: Any) -> None:
        super().__init__(**options)
        self.domains = domains

    def openapi(self) -> dict[str, Any]:
        return super().openapi() | {"x-twake-domains": self.domains}


def create_app(
    database_url: str,
    settings: Settings,
    http: httpx.AsyncClient | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    published = applications.published(settings.published_apps)
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
    app = Contracts(
        {application.domain: application.described() for application in published},
        title="Twake Space agent contracts",
        lifespan=lifespan,
    )
    problems.install(app)
    context = applications.Context(settings, pool, http, caller, clock)
    for application in published:
        for router in application.routers(context):
            app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn --factory, configured by the environment."""
    return create_app(os.environ["DATABASE_URL"], Settings.from_env())
