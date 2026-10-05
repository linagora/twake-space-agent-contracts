import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, Store, invitation


async def test_a_target_reads_a_stored_event(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-1", targets=["mmaudet"], time="2026-10-05T09:14:22Z"))

    response = await client.get("/contracts/v1/events/evt-1", headers=AS_MMAUDET)

    assert response.status_code == 200
    assert response.json() == {
        "id": "evt-1",
        "type": "com.twake.calendar.event.invited.v1",
        "time": "2026-10-05T09:14:22Z",
        "org": "linagora",
        "actor": "e2e.organizer",
        "targets": ["mmaudet"],
        "subject": "calendars/e2e.organizer/evt-1.ics",
        "data": {
            "object": {"title": "Point Twake Space E2E", "start": "2026-10-13T17:00:00+02:00"}
        },
    }


async def test_an_event_of_another_user_is_not_found(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-1", targets=["alice"], time="2026-10-05T09:14:22Z"))

    response = await client.get("/contracts/v1/events/evt-1", headers=AS_MMAUDET)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:twake:problem:event_not_found",
        "title": "Event not found",
        "status": 404,
        "detail": "No event evt-1 concerns this user.",
        "code": "event_not_found",
    }


async def test_an_unknown_event_is_not_found(client: AsyncClient, store: Store) -> None:
    response = await client.get("/contracts/v1/events/evt-404", headers=AS_MMAUDET)

    assert response.status_code == 404
    assert response.json()["code"] == "event_not_found"


@pytest.mark.parametrize("headers", [{}, {"X-Twake-User": ""}], ids=["missing", "empty"])
async def test_a_request_without_a_user_is_refused(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/contracts/v1/events/evt-1", headers=headers)

    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:twake:problem:missing_user",
        "title": "Missing user",
        "status": 401,
        "detail": "The X-Twake-User header must name the user the agent acts for.",
        "code": "missing_user",
    }
