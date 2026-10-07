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


def _whole(schema: Any, components: dict[str, Any]) -> Any:
    """The schema with the components it refers to written in place."""
    if isinstance(schema, list):
        return [_whole(item, components) for item in schema]
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        return _whole(components[schema["$ref"].removeprefix("#/components/schemas/")], components)
    return {key: _whole(value, components) for key, value in schema.items()}


class Contracts(FastAPI):
    """The service, whose OpenAPI document also names, at its root, each application it publishes
    in the words the harness asks their owners with (x-twake-domains)."""

    def __init__(self, domains: dict[str, applications.Description], **options: Any) -> None:
        super().__init__(**options)
        self.domains = domains

    def openapi(self) -> dict[str, Any]:
        document = super().openapi()
        # The harness gives the model the schema of a body as it is: whole, without a reference
        components = document.get("components", {}).get("schemas", {})
        for item in document["paths"].values():
            for operation in item.values():
                for content in operation.get("requestBody", {}).get("content", {}).values():
                    content["schema"] = _whole(content["schema"], components)
        return document | {"x-twake-domains": self.domains}


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
