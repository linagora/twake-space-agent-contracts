import re
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tests.fakes import SETTINGS, FakeBoundary, FakeClock, as_user, email_of
from twake_space_agent_contracts.app import create_app
from twake_space_agent_contracts.settings import Settings

AS_MMAUDET = as_user(email_of("mmaudet"))

# Tuesday 17:00 to 18:00 in Paris, a period free/busy reads
SLOT: dict[str, str | list[str]] = {
    "start": "2026-10-06T17:00:00+02:00",
    "end": "2026-10-06T18:00:00+02:00",
}

# The digest of a preview, as the harness keeps it and sends it back (src/contracts/preview.ts)
DIGEST = re.compile(r"[A-Za-z0-9+/=._:-]{1,256}")


# The most a summary may take for the harness to show it (CALL_BYTES in
# src/consents/request.ts)
HARNESS_LIMIT = 16_384


def harness_size(text: str) -> int:
    """What a summary takes as the harness counts it: its bytes in UTF-8, and those of its HTML,
    which escapes &, < and >."""
    html = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return len(text.encode()) + len(html.encode())


def asking_preview(language: str) -> dict[str, str]:
    """What the harness adds to a call to ask what it would do, in the owner's language."""
    return {"x-twake-preview": "true", "accept-language": language}


def allowed_after(digest: str) -> dict[str, str]:
    """What the harness adds to the call its owner allowed once shown its preview."""
    return {"x-twake-preview-digest": digest}


def preview_of(response: Response) -> tuple[str, str]:
    """The summary and the digest of a preview, once found what the harness takes for one: a 200
    that carries x-twake-preview: true back, with a summary it can show, which holds no control
    or format character but line feeds and tabs, and a digest it can send back."""
    assert response.status_code == 200, response.text
    assert response.headers.get("x-twake-preview") == "true"
    answer = response.json()
    assert set(answer) == {"summary", "digest"}, answer
    summary, digest = answer["summary"], answer["digest"]
    assert isinstance(summary, str)
    assert summary.strip()
    unshown = [
        character
        for character in summary
        if character not in "\n\t" and unicodedata.category(character) in ("Cc", "Cf")
    ]
    assert unshown == []
    assert DIGEST.fullmatch(digest), digest
    assert harness_size(summary.strip()) <= HARNESS_LIMIT
    return summary, digest


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
def serve(boundary: FakeBoundary, clock: FakeClock) -> Serve:
    """Starts the service with the given settings, what it reaches over HTTP faked."""

    def start(settings: Settings) -> AbstractAsyncContextManager[AsyncClient]:
        http = httpx.AsyncClient(transport=httpx.MockTransport(boundary.handle))
        return serving(create_app(settings, http=http, clock=clock))

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
