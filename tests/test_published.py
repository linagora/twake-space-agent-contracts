"""The applications the service publishes, as the operator sets them in PUBLISHED_APPS."""

from dataclasses import replace
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, Serve, operations_of, serving
from tests.fakes import ISSUER, SETTINGS
from twake_space_agent_contracts.app import create_app_from_env

PERIOD = {"start": "2026-10-13T17:00:00+02:00", "end": "2026-10-13T18:00:00+02:00"}


async def document_of(client: AsyncClient) -> dict[str, Any]:
    document: dict[str, Any] = (await client.get("/openapi.json")).json()
    return document


def operation_ids(document: dict[str, Any]) -> set[str]:
    return {operation["operationId"] for _, _, operation in operations_of(document)}


@pytest.fixture
def environment(database_url: str, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The environment the image reads, without PUBLISHED_APPS."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CALENDAR_URL", SETTINGS.calendar_url)
    monkeypatch.delenv("PUBLISHED_APPS", raising=False)
    return monkeypatch


async def test_calendar_taken_out_is_gone_until_it_is_put_back(serve: Serve) -> None:
    without_calendar = replace(SETTINGS, published_apps=SETTINGS.published_apps - {"calendar"})
    async with serve(without_calendar) as client:
        document = await document_of(client)
        unknown = await client.get("/contracts/v1/nothing", headers=AS_MMAUDET)
        freebusy = await client.get(
            "/contracts/v1/calendar/freebusy", params=PERIOD, headers=AS_MMAUDET
        )
        accept = await client.post(
            "/contracts/v1/calendar/invitations/invitation-a/accept", headers=AS_MMAUDET
        )

    assert operation_ids(document) == {"read_event", "list_events"}
    assert set(document["x-twake-domains"]) == {"events"}
    # Its paths answer exactly like paths the service never had
    for response in (freebusy, accept):
        assert response.status_code == 404
        assert response.json() == unknown.json()

    async with serve(SETTINGS) as client:
        document = await document_of(client)
        freebusy = await client.get(
            "/contracts/v1/calendar/freebusy", params=PERIOD, headers=AS_MMAUDET
        )

    assert {"read_freebusy", "accept_invitation"} <= operation_ids(document)
    assert "calendar" in document["x-twake-domains"]
    assert freebusy.status_code == 200, freebusy.text


async def test_without_the_setting_events_and_calendar_are_published(
    environment: pytest.MonkeyPatch,
) -> None:
    # What the service published before the setting existed
    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert operation_ids(document) == {
        "read_event",
        "list_events",
        "read_freebusy",
        "accept_invitation",
    }
    assert set(document["x-twake-domains"]) == {"events", "calendar"}


async def test_the_setting_lists_the_applications_by_their_domain(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PUBLISHED_APPS", " Events ")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert operation_ids(document) == {"read_event", "list_events"}


def test_an_application_the_service_does_not_have_stops_it_from_starting(
    environment: pytest.MonkeyPatch,
) -> None:
    # A misspelt application would otherwise vanish from the agents' tools without a word
    environment.setenv("PUBLISHED_APPS", "events,calender")

    with pytest.raises(ValueError, match="calender"):
        create_app_from_env()
