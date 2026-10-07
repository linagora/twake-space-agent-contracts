"""The key of the contracts' consumer, which the gateway's outbound route to Synapse admits, so
that only this service uses the token of the contracts' application service."""

import logging
from dataclasses import replace

import httpx
import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, Serve
from tests.fakes import (
    CHAT_GATEWAY_KEY,
    MMAUDET_INSTANCE,
    SETTINGS,
    FakeBoundary,
    FakeRoom,
    as_drive_owner,
    matrix_id,
    said,
)

ROOMS = "/contracts/v1/chat/rooms"
PROJECT = "!project:chat.twake.test"
MMAUDET = matrix_id("mmaudet")


def to_chat(request: httpx.Request) -> bool:
    """Whether the request goes to the gateway's outbound route to Synapse."""
    return request.url.host == "gateway.test" and request.url.path.startswith("/synapse/")


def carries_the_key(request: httpx.Request) -> bool:
    """Whether the key shows anywhere in the request: its address, a header or its body."""
    return (
        CHAT_GATEWAY_KEY in str(request.url)
        or any(CHAT_GATEWAY_KEY in value for value in request.headers.values())
        or CHAT_GATEWAY_KEY.encode() in request.content
    )


async def test_every_call_to_chat_presents_the_key_in_its_header(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.synapse.rooms[PROJECT] = FakeRoom(
        members={MMAUDET: "Michel-Marie"},
        timeline=[said("$hello", MMAUDET, "2026-10-06T09:00:00Z", "Bonjour")],
    )

    for path in ("", f"/{PROJECT}", f"/{PROJECT}/members", f"/{PROJECT}/messages"):
        response = await client.get(ROOMS + path, headers=AS_MMAUDET)
        assert response.status_code == 200, response.text

    assert boundary.synapse.requests
    for request in boundary.synapse.requests:
        assert request.headers["apikey"] == CHAT_GATEWAY_KEY
        # Never in the address, which logs show
        assert CHAT_GATEWAY_KEY not in str(request.url)


async def test_a_key_the_gateway_does_not_admit_is_refused(serve: Serve) -> None:
    async with serve(replace(SETTINGS, chat_gateway_key="key-of-another-consumer")) as client:
        response = await client.get(ROOMS, headers=AS_MMAUDET)

    # The gateway answers 401, as APISIX does, and the problem names no key
    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:twake:problem:chat_refused",
        "title": "Chat refused the contracts",
        "status": 502,
        "detail": "Chat answered 401 to GET /account/3pid.",
        "code": "chat_refused",
    }


async def test_the_key_goes_to_chat_alone(client: AsyncClient, boundary: FakeBoundary) -> None:
    period = {"start": "2026-10-13T17:00:00+02:00", "end": "2026-10-13T18:00:00+02:00"}
    # One contract of each application the service reaches over HTTP
    for path, params, headers in [
        (ROOMS, {}, AS_MMAUDET),
        ("/contracts/v1/calendar/freebusy", period, AS_MMAUDET),
        ("/contracts/v1/mail/mailboxes", {}, AS_MMAUDET),
        ("/contracts/v1/drive/folders/root/items", {}, as_drive_owner()),
        ("/contracts/v1/tasks/mine", {"zone": "Europe/Paris"}, AS_MMAUDET),
    ]:
        response = await client.get(path, params=params, headers=headers)
        assert response.status_code == 200, response.text

    assert {request.url.host for request in boundary.requests} == {
        "sign-up.test",
        "gateway.test",
        "calendar.test",
        "tmail.test",
        MMAUDET_INSTANCE,
        "tasks.test",
    }
    for request in boundary.requests:
        assert carries_the_key(request) == to_chat(request), request.url


async def test_the_key_shows_in_no_log_nor_representation(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    response = await client.get(ROOMS, headers=AS_MMAUDET)

    assert response.status_code == 200, response.text
    # httpx logs the address of each call
    assert "gateway.test/synapse" in caplog.text
    assert CHAT_GATEWAY_KEY not in caplog.text
    assert CHAT_GATEWAY_KEY not in repr(SETTINGS)
