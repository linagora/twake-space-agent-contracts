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
    assert {name: operation["tags"] for name, operation in operations.items()} == {
        "read_event": ["events.read.v1"],
        "list_events": ["events.read.v1"],
        "read_freebusy": ["calendar.freebusy.read.v1"],
        "accept_invitation": ["calendar.invitation.accept.v1"],
    }


async def test_agents_neither_hold_a_token_nor_choose_the_user(client: AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()

    parameters = {
        parameter["name"].lower()
        for path in document["paths"].values()
        for operation in path.values()
        for parameter in operation.get("parameters", [])
    }
    assert not parameters & {"authorization", "x-twake-user"}
