from typing import Any

from httpx import AsyncClient


async def test_the_contract_is_described_for_agents(client: AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()

    operations: dict[str, Any] = {
        operation["operationId"]: operation
        for path in document["paths"].values()
        for operation in path.values()
    }
    assert document["info"]["title"] == "Twake Space agent contracts"
    assert set(operations) == {"read_event", "list_events"}
    assert all(operation["tags"] == ["events.read.v1"] for operation in operations.values())
