from httpx import AsyncClient

from tests.conftest import Store, invitation

MMAUDET = {"X-Twake-User": "mmaudet"}


async def test_a_target_lists_its_events_newest_first(client: AsyncClient, store: Store) -> None:
    await store(invitation("evt-old", targets=["mmaudet"], time="2026-10-01T08:00:00Z"))
    await store(invitation("evt-new", targets=["mmaudet"], time="2026-10-05T08:00:00Z"))
    await store(invitation("evt-mid", targets=["mmaudet"], time="2026-10-03T08:00:00Z"))

    response = await client.get("/contracts/v1/events", params={"limit": 2}, headers=MMAUDET)

    assert response.status_code == 200
    assert [event["id"] for event in response.json()["events"]] == ["evt-new", "evt-mid"]


async def test_the_list_holds_twenty_events_by_default(client: AsyncClient, store: Store) -> None:
    for day in range(1, 26):
        await store(
            invitation(f"evt-{day}", targets=["mmaudet"], time=f"2026-09-{day:02}T08:00:00Z")
        )

    response = await client.get("/contracts/v1/events", headers=MMAUDET)

    assert len(response.json()["events"]) == 20
