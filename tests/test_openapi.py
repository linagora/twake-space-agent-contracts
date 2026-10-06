import json
import re
from typing import Any

from httpx import AsyncClient

from tests.conftest import operations_of

# The words of x-twake-domains, as the harness shows them in its consent questions: plain text on
# one line. What would read as anything else there: a break or a control character; a character
# Markdown or HTML gives a meaning to; and anything that looks like a link, an address or a domain
# name, such as a dot inside a word, which a chat client may link on its own.
BREAK = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
MARKUP = re.compile(r"[\\`*_~\[\]<>&]")
LINK = re.compile(r"[a-z][a-z0-9+.-]*:\S|\S@\S|[^\W_]\.[^\W_]", re.IGNORECASE)
LONGEST = {"name": 64, "read": 200, "write": 200}


def unplain(text: object, longest: int) -> str | None:
    """Why the harness would not show these words as they are, or None."""
    if not isinstance(text, str) or not text.strip():
        return "no words"
    if len(text.strip()) > longest:
        return f"over {longest} characters"
    for pattern, found in (
        (BREAK, "a break"),
        (MARKUP, "a markup character"),
        (LINK, "a link, an address or a domain name"),
    ):
        if pattern.search(text):
            return found
    return "a final period" if text.rstrip().endswith(".") else None


def unplain_texts(texts: dict[str, Any], longest: int) -> list[str]:
    """Why the harness would not show a text in each language it speaks, as it is."""
    if set(texts) != {"en", "fr"}:
        return [f"in {sorted(texts)}, not in en and fr"]
    found = ((language, text, unplain(text, longest)) for language, text in texts.items())
    return [f"{language}: {problem}: {text!r}" for language, text, problem in found if problem]


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


async def test_list_parameters_are_plain_arrays_the_gateway_can_check(client: AsyncClient) -> None:
    # APISIX's oas-validator turns a query value into a list only for a schema of type array: a
    # list wrapped in anyOf, as for an optional list, refuses even a valid single value
    document = (await client.get("/openapi.json")).json()

    lists = {
        parameter["name"]: parameter["schema"]
        for path in document["paths"].values()
        for operation in path.values()
        for parameter in operation.get("parameters", [])
        if "array" in json.dumps(parameter["schema"])
    }
    assert lists, "no list parameter found"
    for name, schema in lists.items():
        assert schema.get("type") == "array", f"{name}: {schema}"


async def test_each_published_application_is_named_in_plain_words(client: AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()
    # A GET reads, any other method writes, in the domain the contract id starts with
    offered: dict[str, set[str]] = {}
    for _, method, operation in operations_of(document):
        domain = operation["tags"][0].split(".")[0]
        offered.setdefault(domain, set()).add("read" if method == "get" else "write")

    domains = document.get("x-twake-domains", {})

    # Each application served, and no other, with its name and the levels it offers
    assert {domain: set(words) for domain, words in domains.items()} == {
        domain: {"name", *levels} for domain, levels in offered.items()
    }
    problems = [
        f"{domain}.{level}.{problem}"
        for domain, words in domains.items()
        for level, texts in words.items()
        for problem in unplain_texts(texts, LONGEST[level])
    ]
    assert problems == []


async def test_each_write_declares_its_risk(client: AsyncClient) -> None:
    # The harness confirms a write without a risk it knows each time, as a high one
    document = (await client.get("/openapi.json")).json()

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, method, operation in operations_of(document)
        if method != "get"
    }

    assert risks, "no write found"
    assert {name: risk for name, risk in risks.items() if risk not in ("low", "high")} == {}


async def test_accepting_an_invitation_is_a_low_risk_write(client: AsyncClient) -> None:
    # The user's own answer: once the owner allowed writing in Calendar, it runs without asking
    document = (await client.get("/openapi.json")).json()

    accept = document["paths"]["/contracts/v1/calendar/invitations/{event_id}/accept"]["post"]

    assert accept["x-twake-risk"] == "low"
