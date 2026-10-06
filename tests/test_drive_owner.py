from dataclasses import replace

import pytest
from httpx import AsyncClient

from tests.conftest import Serve
from tests.fakes import (
    MMAUDET_DRIVE_TOKEN,
    MMAUDET_INSTANCE,
    ROOT_ID,
    SETTINGS,
    FakeBoundary,
    as_drive_owner,
    text_file,
)

ROOT_ITEMS = "/contracts/v1/drive/folders/root/items"


async def test_the_drive_token_goes_to_the_users_instance_over_https(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    response = await client.get(ROOT_ITEMS, headers=as_drive_owner())

    assert response.status_code == 200, response.text
    assert boundary.drive.requests
    for request in boundary.drive.requests:
        assert (request.url.scheme, request.url.host, request.url.port) == (
            "https",
            MMAUDET_INSTANCE,
            None,
        )
        assert request.headers["authorization"] == f"Bearer {MMAUDET_DRIVE_TOKEN}"


async def test_a_setting_gives_the_scheme_and_port_of_the_instances(
    serve: Serve, boundary: FakeBoundary
) -> None:
    boundary.drive.add(text_file("notes", "notes.txt"))
    local_stack = replace(SETTINGS, drive_scheme="http", drive_port=8080)

    async with serve(local_stack) as client:
        response = await client.get("/contracts/v1/drive/files/notes", headers=as_drive_owner())

    assert response.status_code == 200, response.text
    assert {(request.url.scheme, request.url.port) for request in boundary.drive.requests} == {
        ("http", 8080)
    }
    assert response.json()["web_url"] == (
        f"http://mmaudet-drive.twake.test:8080/#/folder/{ROOT_ID}/file/notes"
    )


async def test_a_drive_contract_needs_the_users_token(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    headers = as_drive_owner()
    del headers["Authorization"]

    response = await client.get(ROOT_ITEMS, headers=headers)

    assert response.status_code == 401
    assert response.json()["code"] == "missing_token"
    assert boundary.drive.requests == []


@pytest.mark.parametrize(
    "instance",
    [None, "", f"{MMAUDET_INSTANCE}:8443", f"https://{MMAUDET_INSTANCE}", "mmaudet"],
    ids=["missing", "empty", "with a port", "a URL", "not a domain name"],
)
async def test_a_user_whose_drive_instance_is_unknown_is_told_so(
    client: AsyncClient, boundary: FakeBoundary, instance: str | None
) -> None:
    # LemonLDAP-NG gives no workplaceFqdn for the user, or one the service cannot reach
    headers = as_drive_owner()
    if instance is None:
        del headers["X-Twake-Drive-Instance"]
    else:
        headers["X-Twake-Drive-Instance"] = instance

    response = await client.get(ROOT_ITEMS, headers=headers)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "drive_instance_unknown"
    assert boundary.drive.requests == []


@pytest.mark.parametrize("token", [None, " "], ids=["missing", "empty"])
async def test_a_request_without_the_drive_token_is_refused(
    client: AsyncClient, boundary: FakeBoundary, token: str | None
) -> None:
    headers = as_drive_owner()
    if token is None:
        del headers["X-Twake-Drive-Token"]
    else:
        headers["X-Twake-Drive-Token"] = token

    response = await client.get(ROOT_ITEMS, headers=headers)

    assert response.status_code == 401
    assert response.json() == {
        "type": "urn:twake:problem:missing_drive_token",
        "title": "Missing Drive token",
        "status": 401,
        "detail": "The request must carry the user's Drive token in X-Twake-Drive-Token.",
        "code": "missing_drive_token",
    }
    assert boundary.drive.requests == []


async def test_a_drive_token_the_instance_refuses_is_named_so(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.refused_tokens = True

    response = await client.get(ROOT_ITEMS, headers=as_drive_owner())

    assert response.status_code == 502
    assert response.json()["code"] == "drive_refused"


async def test_an_unavailable_drive_is_a_bad_gateway(
    client: AsyncClient, boundary: FakeBoundary
) -> None:
    boundary.drive.down = True

    response = await client.get(ROOT_ITEMS, headers=as_drive_owner())

    assert response.status_code == 502
    assert response.json()["code"] == "drive_unavailable"
