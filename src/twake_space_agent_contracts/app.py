import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from twake_space_agent_contracts import applications, documents, problems
from twake_space_agent_contracts.caller import TokenVerifier, caller_dependency
from twake_space_agent_contracts.settings import Settings

COMPONENTS = "#/components/schemas/"


def _whole(schema: Any, components: dict[str, Any], within: frozenset[str] = frozenset()) -> Any:
    """The schema with the components it refers to written in place, what it says beside a
    reference kept over what the component says. A component found within itself, which no schema
    holds whole, keeps its reference there, for the test of the document to find."""
    if isinstance(schema, list):
        return [_whole(item, components, within) for item in schema]
    if not isinstance(schema, dict):
        return schema
    beside = {
        key: _whole(value, components, within) for key, value in schema.items() if key != "$ref"
    }
    if "$ref" not in schema:
        return beside
    name = schema["$ref"].removeprefix(COMPONENTS)
    if name in within:
        return {"$ref": schema["$ref"]} | beside
    return _whole(components[name], components, within | {name}) | beside


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
    settings: Settings,
    http: httpx.AsyncClient | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    published = applications.published(settings.published_apps)
    http = http or httpx.AsyncClient(timeout=10)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await http.aclose()

    caller = caller_dependency(TokenVerifier(settings, http, clock))
    app = Contracts(
        {application.domain: application.described() for application in published},
        title="Twake Space agent contracts",
        lifespan=lifespan,
    )
    problems.install(app)
    context = applications.Context(settings, http, caller, clock)
    for application in published:
        for router in application.routers(context):
            app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for uvicorn --factory, configured by the environment."""
    # Its settings and the tokens it handles stay the service's own: the processes it starts to
    # read documents may not inspect it
    documents.forbid_inspection()
    return create_app(Settings.from_env())
