from httpx import AsyncClient

from tests.conftest import Store, invitation


async def test_a_target_reads_a_stored_event(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-1", targets=["mmaudet"], time="2026-10-05T09:14:22Z"))

    response = await client.get("/contracts/v1/events/evt-1", headers={"X-Twake-User": "mmaudet"})

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
