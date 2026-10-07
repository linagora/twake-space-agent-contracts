"""The applications the service publishes, as the operator sets them in PUBLISHED_APPS."""

from dataclasses import replace
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, Serve, operations_of, serving
from tests.fakes import ISSUER, SETTINGS, FakeBoundary, as_drive_owner, email_of
from twake_space_agent_contracts.app import create_app_from_env
from twake_space_agent_contracts.settings import Settings

PERIOD = {"start": "2026-10-13T17:00:00+02:00", "end": "2026-10-13T18:00:00+02:00"}
CHAT_SETTINGS = {"CHAT_URL": "https://gateway.test/synapse/", "MATRIX_SERVER_NAME": "twake.test"}
DRIVE_SETTINGS = ("DRIVE_INSTANCE_DOMAIN", "DRIVE_SCHEME", "DRIVE_PORT")


async def document_of(client: AsyncClient) -> dict[str, Any]:
    document: dict[str, Any] = (await client.get("/openapi.json")).json()
    return document


def operation_ids(document: dict[str, Any]) -> set[str]:
    return {operation["operationId"] for _, _, operation in operations_of(document)}


@pytest.fixture
def environment(database_url: str, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The environment the image reads, without PUBLISHED_APPS nor any setting of Chat, Mail or
    Drive."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CALENDAR_URL", SETTINGS.calendar_url)
    for name in (
        "PUBLISHED_APPS",
        *CHAT_SETTINGS,
        "MATRIX_MAIL_DOMAIN",
        "MAIL_URL",
        *DRIVE_SETTINGS,
    ):
        monkeypatch.delenv(name, raising=False)
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

    assert not {"read_freebusy", "accept_invitation"} & operation_ids(document)
    assert set(document["x-twake-domains"]) == SETTINGS.published_apps - {"calendar"}
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


async def test_events_stay_published_whatever_the_setting_says(serve: Serve) -> None:
    # The harness reads the assistant's own feed without asking, and checks invitations with it
    calendar_only = replace(SETTINGS, published_apps=frozenset({"calendar"}))
    async with serve(calendar_only) as client:
        document = await document_of(client)
        events = await client.get("/contracts/v1/events", headers=AS_MMAUDET)

    assert {"read_event", "list_events", "read_freebusy"} <= operation_ids(document)
    assert set(document["x-twake-domains"]) == {"events", "calendar"}
    assert events.status_code == 200, events.text


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
async def test_without_applications_set_events_and_calendar_are_published(
    environment: pytest.MonkeyPatch, value: str | None
) -> None:
    # What the service published before the setting existed; a chart may render it empty
    if value is not None:
        environment.setenv("PUBLISHED_APPS", value)

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


async def test_chat_unpublished_needs_none_of_its_settings(
    environment: pytest.MonkeyPatch,
) -> None:
    # The image runs on dev before Chat goes live there
    environment.setenv("PUBLISHED_APPS", "events,calendar")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert set(document["x-twake-domains"]) == {"events", "calendar"}


@pytest.mark.parametrize("missing", list(CHAT_SETTINGS))
def test_chat_published_without_its_settings_stops_the_service_from_starting(
    environment: pytest.MonkeyPatch, missing: str
) -> None:
    environment.setenv("PUBLISHED_APPS", "events,calendar,chat")
    for name, value in CHAT_SETTINGS.items():
        if name != missing:
            environment.setenv(name, value)

    with pytest.raises(ValueError, match=f"chat, which needs {missing}$"):
        create_app_from_env()


async def test_chat_published_with_its_settings_is_served(
    environment: pytest.MonkeyPatch, serve: Serve, boundary: FakeBoundary
) -> None:
    environment.setenv("PUBLISHED_APPS", "chat")
    for name, value in CHAT_SETTINGS.items():
        environment.setenv(name, value)
    # Without MATRIX_MAIL_DOMAIN, the users' mail domain is the server name
    boundary.synapse.accounts["@mmaudet:twake.test"] = [email_of("mmaudet")]

    async with serve(Settings.from_env()) as client:
        document = await document_of(client)
        rooms = await client.get("/contracts/v1/chat/rooms", headers=AS_MMAUDET)

    assert set(document["x-twake-domains"]) == {"events", "chat"}
    assert rooms.status_code == 200, rooms.text


async def test_mail_is_published_once_the_setting_names_it(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PUBLISHED_APPS", "events,mail")
    environment.setenv("MAIL_URL", "https://tmail.test/")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    # The domain of an operation is the first segment of its contract id
    domains = {operation["tags"][0].split(".")[0] for _, _, operation in operations_of(document)}
    assert domains == {"events", "mail"}
    assert set(document["x-twake-domains"]) == {"events", "mail"}


async def test_tmail_is_needed_once_mail_is_published_only(
    environment: pytest.MonkeyPatch,
) -> None:
    # Unpublished, Mail needs no address: the service starts without MAIL_URL
    async with serving(create_app_from_env()) as client:
        document = await document_of(client)
    assert "mail" not in document["x-twake-domains"]

    environment.setenv("PUBLISHED_APPS", "events,calendar,mail")

    with pytest.raises(ValueError, match="MAIL_URL"):
        create_app_from_env()


async def test_drive_unpublished_needs_none_of_its_settings(
    environment: pytest.MonkeyPatch,
) -> None:
    # The image runs on dev before Drive goes live there
    environment.setenv("PUBLISHED_APPS", "events,calendar")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert set(document["x-twake-domains"]) == {"events", "calendar"}


@pytest.mark.parametrize(
    ("domain", "refusal"),
    [
        (None, "drive, which needs DRIVE_INSTANCE_DOMAIN$"),
        ("169.254.169.254", "DRIVE_INSTANCE_DOMAIN is not a domain name"),
    ],
    ids=["missing", "an address"],
)
def test_drive_published_without_the_domain_of_its_instances_stops_the_service_from_starting(
    environment: pytest.MonkeyPatch, domain: str | None, refusal: str
) -> None:
    environment.setenv("PUBLISHED_APPS", "events,calendar,drive")
    if domain is not None:
        environment.setenv("DRIVE_INSTANCE_DOMAIN", domain)

    with pytest.raises(ValueError, match=refusal):
        create_app_from_env()


async def test_drive_published_with_the_domain_of_its_instances_is_served(
    environment: pytest.MonkeyPatch, serve: Serve, boundary: FakeBoundary
) -> None:
    # Its instances are reached over HTTPS unless told otherwise
    environment.setenv("PUBLISHED_APPS", "drive")
    environment.setenv("DRIVE_INSTANCE_DOMAIN", "twake.test")

    async with serve(Settings.from_env()) as client:
        document = await document_of(client)
        items = await client.get("/contracts/v1/drive/folders/root/items", headers=as_drive_owner())

    assert set(document["x-twake-domains"]) == {"events", "drive"}
    assert items.status_code == 200, items.text
