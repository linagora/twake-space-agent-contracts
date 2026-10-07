"""The applications the service publishes, as the operator sets them in PUBLISHED_APPS."""

import re
from dataclasses import replace
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, Serve, operations_of, serving
from tests.fakes import CHAT_GATEWAY_KEY, ISSUER, SETTINGS, FakeBoundary, as_drive_owner, email_of
from twake_space_agent_contracts.app import create_app_from_env
from twake_space_agent_contracts.settings import Settings

PERIOD = {"start": "2026-10-13T17:00:00+02:00", "end": "2026-10-13T18:00:00+02:00"}
# A parameter of a path, as the document writes it, such as {task_id}
PATH_PARAMETER = re.compile(r"\{[^}]+\}")
CHAT_SETTINGS = {
    "CHAT_URL": "https://gateway.test/synapse/",
    "CHAT_GATEWAY_KEY": CHAT_GATEWAY_KEY,
    "MATRIX_SERVER_NAME": "twake.test",
}
DRIVE_SETTINGS = ("DRIVE_INSTANCE_DOMAIN", "DRIVE_SCHEME", "DRIVE_PORT")
TASKS_OPERATIONS = {
    "open_boards",
    "list_my_tasks",
    "search_tasks",
    "read_task",
    "create_task",
    "update_task",
    "complete_task",
}


async def document_of(client: AsyncClient) -> dict[str, Any]:
    document: dict[str, Any] = (await client.get("/openapi.json")).json()
    return document


def operation_ids(document: dict[str, Any]) -> set[str]:
    return {operation["operationId"] for _, _, operation in operations_of(document)}


def operations_in(document: dict[str, Any], domain: str) -> set[tuple[str, str]]:
    """The path and method of each operation of the application of that domain, the first segment
    of their contract id."""
    return {
        (path, method)
        for path, method, operation in operations_of(document)
        if operation["tags"][0].split(".")[0] == domain
    }


@pytest.fixture
def environment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The environment the image reads, without a database, which nothing it serves reads, nor
    PUBLISHED_APPS, nor any setting of Chat, Mail, Drive or Tasks."""
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CALENDAR_URL", SETTINGS.calendar_url)
    for name in (
        "DATABASE_URL",
        "PUBLISHED_APPS",
        *CHAT_SETTINGS,
        "MATRIX_MAIL_DOMAIN",
        "MAIL_URL",
        *DRIVE_SETTINGS,
        "TASKS_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize(
    ("domain", "served", "params"),
    [pytest.param("calendar", "/contracts/v1/calendar/freebusy", PERIOD, id="calendar")],
)
async def test_an_application_taken_out_is_gone_until_it_is_put_back(
    client: AsyncClient, serve: Serve, domain: str, served: str, params: dict[str, str]
) -> None:
    # client serves every application, as the service does once this one is put back
    published = await document_of(client)
    operations = operations_in(published, domain)
    without = replace(SETTINGS, published_apps=SETTINGS.published_apps - {domain})
    async with serve(without) as taken_out:
        document = await document_of(taken_out)
        unknown = await taken_out.get("/contracts/v1/nothing", headers=AS_MMAUDET)
        gone = [
            await taken_out.request(method, PATH_PARAMETER.sub("id-1", path), headers=AS_MMAUDET)
            for path, method in sorted(operations)
        ]
    answer = await client.get(served, params=params, headers=AS_MMAUDET)

    assert (served, "get") in operations
    assert not {path for path, _ in operations} & set(document["paths"])
    assert set(document["x-twake-domains"]) == SETTINGS.published_apps - {domain}
    # Its paths answer exactly like paths the service never had
    for response in gone:
        assert response.status_code == 404
        assert response.json() == unknown.json()
    assert domain in published["x-twake-domains"]
    assert answer.status_code == 200, answer.text


async def test_the_events_once_stored_are_no_longer_published(serve: Serve) -> None:
    # Nothing stores them since the Kafka bus was removed: their paths answer like paths the
    # service never had, and the setting alone says what is published
    calendar_only = replace(SETTINGS, published_apps=frozenset({"calendar"}))
    async with serve(calendar_only) as client:
        document = await document_of(client)
        unknown = await client.get("/contracts/v1/nothing", headers=AS_MMAUDET)
        events = await client.get("/contracts/v1/events", headers=AS_MMAUDET)

    assert not {"read_event", "list_events"} & operation_ids(document)
    assert set(document["x-twake-domains"]) == {"calendar"}
    assert events.status_code == 404
    assert events.json() == unknown.json()


@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
async def test_without_applications_set_calendar_is_published(
    environment: pytest.MonkeyPatch, serve: Serve, value: str | None
) -> None:
    # What the service published before the setting existed, but for the events it no longer
    # has; a chart may render it empty
    if value is not None:
        environment.setenv("PUBLISHED_APPS", value)

    async with serve(Settings.from_env()) as client:
        document = await document_of(client)
        freebusy = await client.get(
            "/contracts/v1/calendar/freebusy", params=PERIOD, headers=AS_MMAUDET
        )

    assert operation_ids(document) == {"read_freebusy", "accept_invitation", "create_event"}
    assert set(document["x-twake-domains"]) == {"calendar"}
    assert freebusy.status_code == 200, freebusy.text


async def test_the_setting_lists_the_applications_by_their_domain(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PUBLISHED_APPS", " Contacts ")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert operation_ids(document) == {
        "list_address_books",
        "search_contacts",
        "read_contact",
        "create_contact",
        "update_contact",
        "delete_contact",
    }


@pytest.mark.parametrize(
    ("names", "unknown"),
    [("calendar,calender", "calender"), ("calendar,events", "events")],
    ids=["misspelt", "events, which the service no longer has"],
)
def test_an_application_the_service_does_not_have_stops_it_from_starting(
    environment: pytest.MonkeyPatch, names: str, unknown: str
) -> None:
    # A misspelt application would otherwise vanish from the agents' tools without a word, and a
    # deployment that still names events would take it for published
    environment.setenv("PUBLISHED_APPS", names)

    with pytest.raises(ValueError, match=f"does not have: {unknown}$"):
        create_app_from_env()


async def test_chat_unpublished_needs_none_of_its_settings(
    environment: pytest.MonkeyPatch,
) -> None:
    # The image runs on dev before Chat goes live there
    environment.setenv("PUBLISHED_APPS", "calendar")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert set(document["x-twake-domains"]) == {"calendar"}


@pytest.mark.parametrize("missing", list(CHAT_SETTINGS))
def test_chat_published_without_its_settings_stops_the_service_from_starting(
    environment: pytest.MonkeyPatch, missing: str
) -> None:
    environment.setenv("PUBLISHED_APPS", "calendar,chat")
    for name, value in CHAT_SETTINGS.items():
        if name != missing:
            environment.setenv(name, value)

    with pytest.raises(ValueError, match=f"chat, which needs {missing}$"):
        create_app_from_env()


@pytest.mark.parametrize("key", ["", " \n"], ids=["empty", "blank"])
def test_chat_published_with_an_empty_gateway_key_stops_the_service_from_starting(
    environment: pytest.MonkeyPatch, key: str
) -> None:
    # A chart renders a secret it lacks as an empty value
    environment.setenv("PUBLISHED_APPS", "calendar,chat")
    for name, value in CHAT_SETTINGS.items():
        environment.setenv(name, value)
    environment.setenv("CHAT_GATEWAY_KEY", key)

    with pytest.raises(ValueError, match=r"chat, which needs CHAT_GATEWAY_KEY$"):
        create_app_from_env()


@pytest.mark.parametrize(
    "key",
    [CHAT_GATEWAY_KEY, f"{CHAT_GATEWAY_KEY}\n"],
    ids=["key", "key ending with a line break, as a file may give it"],
)
async def test_chat_published_with_its_settings_is_served(
    environment: pytest.MonkeyPatch, serve: Serve, boundary: FakeBoundary, key: str
) -> None:
    environment.setenv("PUBLISHED_APPS", "chat")
    for name, value in CHAT_SETTINGS.items():
        environment.setenv(name, value)
    environment.setenv("CHAT_GATEWAY_KEY", key)
    # Without MATRIX_MAIL_DOMAIN, the users' mail domain is the server name
    boundary.synapse.accounts["@mmaudet:twake.test"] = [email_of("mmaudet")]

    async with serve(Settings.from_env()) as client:
        document = await document_of(client)
        rooms = await client.get("/contracts/v1/chat/rooms", headers=AS_MMAUDET)

    assert set(document["x-twake-domains"]) == {"chat"}
    assert rooms.status_code == 200, rooms.text


async def test_mail_is_published_once_the_setting_names_it(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PUBLISHED_APPS", "mail")
    environment.setenv("MAIL_URL", "https://tmail.test/")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    # The domain of an operation is the first segment of its contract id
    domains = {operation["tags"][0].split(".")[0] for _, _, operation in operations_of(document)}
    assert domains == {"mail"}
    assert set(document["x-twake-domains"]) == {"mail"}


async def test_tmail_is_needed_once_mail_is_published_only(
    environment: pytest.MonkeyPatch,
) -> None:
    # Unpublished, Mail needs no address: the service starts without MAIL_URL
    async with serving(create_app_from_env()) as client:
        document = await document_of(client)
    assert "mail" not in document["x-twake-domains"]

    environment.setenv("PUBLISHED_APPS", "calendar,mail")

    with pytest.raises(ValueError, match="MAIL_URL"):
        create_app_from_env()


async def test_drive_unpublished_needs_none_of_its_settings(
    environment: pytest.MonkeyPatch,
) -> None:
    # The image runs on dev before Drive goes live there
    environment.setenv("PUBLISHED_APPS", "calendar")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert set(document["x-twake-domains"]) == {"calendar"}


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
    environment.setenv("PUBLISHED_APPS", "calendar,drive")
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

    assert set(document["x-twake-domains"]) == {"drive"}
    assert items.status_code == 200, items.text


async def test_contacts_is_published_once_the_setting_names_it_with_no_setting_of_its_own(
    environment: pytest.MonkeyPatch,
) -> None:
    # Contacts goes through the Calendar side service, at CALENDAR_URL
    environment.setenv("PUBLISHED_APPS", "contacts")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    domains = {operation["tags"][0].split(".")[0] for _, _, operation in operations_of(document)}
    assert domains == {"contacts"}
    assert set(document["x-twake-domains"]) == {"contacts"}


async def test_tasks_left_unpublished_needs_no_url(environment: pytest.MonkeyPatch) -> None:
    # A deployment that does not publish Tasks starts as before, knowing nothing of it
    environment.setenv("PUBLISHED_APPS", "calendar")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert not operation_ids(document) & TASKS_OPERATIONS
    assert "tasks" not in document["x-twake-domains"]


async def test_tasks_published_with_its_url_is_served(environment: pytest.MonkeyPatch) -> None:
    environment.setenv("PUBLISHED_APPS", "calendar,tasks")
    environment.setenv("TASKS_URL", "https://tasks.test/")

    async with serving(create_app_from_env()) as client:
        document = await document_of(client)

    assert operation_ids(document) >= TASKS_OPERATIONS
    assert "tasks" in document["x-twake-domains"]


def test_tasks_published_without_its_url_stops_the_service_from_starting(
    environment: pytest.MonkeyPatch,
) -> None:
    # Its tools would otherwise reach the agents, every call of them failing
    environment.setenv("PUBLISHED_APPS", "calendar,tasks")

    with pytest.raises(ValueError, match="TASKS_URL"):
        create_app_from_env()
