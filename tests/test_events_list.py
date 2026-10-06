import pytest
from httpx import AsyncClient

from tests.conftest import AS_MMAUDET, Store, invitation


async def test_a_target_lists_its_events_newest_first(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-old", targets=["mmaudet"], time="2026-10-01T08:00:00Z"))
    await store(invitation("evt-new", targets=["mmaudet"], time="2026-10-05T08:00:00Z"))
    await store(invitation("evt-mid", targets=["mmaudet"], time="2026-10-03T08:00:00Z"))

    response = await client.get("/contracts/v1/events", params={"limit": 2}, headers=AS_MMAUDET)

    assert response.status_code == 200
    assert [event["id"] for event in response.json()["events"]] == ["evt-new", "evt-mid"]


async def test_the_list_holds_twenty_events_by_default(client: AsyncClient, store: Store) -> None:
    for day in range(1, 26):
        await store(
            invitation(f"evt-{day}", targets=["mmaudet"], time=f"2026-09-{day:02}T08:00:00Z")
        )

    response = await client.get("/contracts/v1/events", headers=AS_MMAUDET)

    assert len(response.json()["events"]) == 20


async def test_the_list_leaves_out_other_users_events(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-mine", targets=["mmaudet", "alice"], time="2026-10-01T08:00:00Z"))
    await store(invitation("evt-alice", targets=["alice"], time="2026-10-02T08:00:00Z"))

    response = await client.get("/contracts/v1/events", headers=AS_MMAUDET)

    assert [event["id"] for event in response.json()["events"]] == ["evt-mine"]


async def test_the_list_keeps_only_the_requested_type(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-invite", targets=["mmaudet"], time="2026-10-01T08:00:00Z"))
    shared_file = invitation("evt-file", targets=["mmaudet"], time="2026-10-02T08:00:00Z")
    await store({**shared_file, "type": "com.twake.drive.file.shared.v1"})

    response = await client.get(
        "/contracts/v1/events",
        params={"type": "com.twake.calendar.event.invited.v1"},
        headers=AS_MMAUDET,
    )

    assert [event["id"] for event in response.json()["events"]] == ["evt-invite"]


async def test_what_others_wrote_in_each_event_comes_apart(
    client: AsyncClient, store: Store
) -> None:
    await store(
        invitation("evt-1", targets=["mmaudet"], time="2026-10-01T08:00:00Z", uid="event-a")
    )

    response = await client.get("/contracts/v1/events", headers=AS_MMAUDET)

    [event] = response.json()["events"]
    assert event["untrusted"] == {"title": "Point Twake Space E2E"}
    # What the harness reads of an invitation stays where it was
    assert event["data"]["object"] == {"start": "2026-10-13T17:00:00+02:00", "uid": "event-a"}


@pytest.mark.parametrize("limit", [0, 101])
async def test_a_limit_out_of_range_is_an_invalid_request(client: AsyncClient, limit: int) -> None:
    response = await client.get("/contracts/v1/events", params={"limit": limit}, headers=AS_MMAUDET)

    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"
    problem = response.json()
    assert problem["type"] == "urn:twake:problem:invalid_request"
    assert problem["code"] == "invalid_request"
    assert "limit" in problem["detail"]
