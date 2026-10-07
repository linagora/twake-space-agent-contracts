import json
import re
from collections.abc import Callable
from typing import Any

import pytest
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


def plain_text_problem(text: object, longest: int) -> str | None:
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


def plain_text_problems(texts: dict[str, Any], longest: int) -> list[str]:
    """Why the harness would not show a text in each language it speaks, as it is."""
    if set(texts) != {"en", "fr"}:
        return [f"in {sorted(texts)}, not in en and fr"]
    found = (
        (language, text, plain_text_problem(text, longest)) for language, text in texts.items()
    )
    return [f"{language}: {problem}: {text!r}" for language, text, problem in found if problem]


# A worked call: after "Example" and what it is an example of, name=value pairs separated by commas,
# to the end of the description. A list gives its name once per value, a body is body=<JSON>.
PAIR = re.compile(r"(?P<name>\w+)=(?P<value>.+?)(?=, \w+=|$)")
# The formats the gateway checks, as JSON Schema defines them
FORMATS = {
    "date-time": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})"),
    "date": re.compile(r"\d{4}-\d{2}-\d{2}"),
}
TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}
# What a schema says that checks nothing
ANNOTATIONS = {"title", "description", "default", "examples", "deprecated"}
# How the description of an operation without parameters ends: a worked call with no value
NO_PARAMETERS = "Example: (no parameters)."


def worked_call(description: str) -> dict[str, list[str]]:
    """The values of the worked call a description ends with, by name, as they are written."""
    _, example, call = description.rpartition("Example")
    values: dict[str, list[str]] = {}
    if example:
        for pair in PAIR.finditer(call.removesuffix(".")):
            values.setdefault(pair["name"], []).append(pair["value"])
    return values


def refusal(value: Any, schema: dict[str, Any], document: dict[str, Any]) -> str | None:
    """Why the gateway, which checks each call against the document, would refuse this value, or
    None. A keyword the test does not know is a refusal too, so that it never passes a value it
    could not check."""
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/components/schemas/")
        return refusal(value, document["components"]["schemas"][name], document)
    if "anyOf" in schema:
        refusals = [refusal(value, branch, document) for branch in schema["anyOf"]]
        return None if None in refusals else " or ".join(map(str, refusals))
    types = schema.get("type", list(TYPES))
    if not any(TYPES[name](value) for name in (types if isinstance(types, list) else [types])):
        return f"not of type {types}"
    for keyword, expected in schema.items():
        match keyword:
            case "type":
                continue
            case "format":
                accepted = expected in FORMATS and FORMATS[expected].fullmatch(value) is not None
            case "pattern":
                accepted = re.search(expected, value) is not None
            case "enum":
                accepted = value in expected
            case "const":
                accepted = value == expected
            case "minLength" | "minItems":
                accepted = len(value) >= expected
            case "maxLength" | "maxItems":
                accepted = len(value) <= expected
            case "minimum":
                accepted = value >= expected
            case "maximum":
                accepted = value <= expected
            case "exclusiveMinimum":
                accepted = value > expected
            case "exclusiveMaximum":
                accepted = value < expected
            case "required":
                accepted = set(expected) <= set(value)
            case "oneOf":
                refusals = [refusal(value, branch, document) for branch in expected]
                accepted = refusals.count(None) == 1
            case "items" | "properties" | "additionalProperties":
                if found := part_refusal(keyword, value, schema, document):
                    return found
                continue
            case _ if keyword in ANNOTATIONS:
                continue
            case _:
                return f"{keyword}, which the test cannot check"
        if not accepted:
            return f"refused by {keyword} {expected!r}"
    return None


def part_refusal(
    keyword: str, value: Any, schema: dict[str, Any], document: dict[str, Any]
) -> str | None:
    """Why the gateway would refuse a part of a list or of an object, or None."""
    expected = schema[keyword]
    parts: dict[int | str, Any]
    schemas: dict[int | str, Any]
    if keyword == "items":
        parts = dict(enumerate(value))
        schemas = dict.fromkeys(parts, expected)
    elif keyword == "properties":
        parts = {name: value[name] for name in value.keys() & expected.keys()}
        schemas = expected
    else:
        parts = {name: value[name] for name in value.keys() - schema.get("properties", {}).keys()}
        if expected is False and parts:
            return f"{', '.join(sorted(map(str, parts)))}: not a property it takes"
        schemas = dict.fromkeys(parts, expected if isinstance(expected, dict) else {})
    for name, part in parts.items():
        if found := refusal(part, schemas[name], document):
            return f"{name}: {found}"
    return None


def as_sent(text: str, schema: dict[str, Any], document: dict[str, Any]) -> Any:
    """A path or query value as the gateway reads it: the text itself where its schema takes
    text, else the number or the boolean it writes."""
    if refusal(text, schema, document) is None:
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def worked_call_problems(operation: dict[str, Any], document: dict[str, Any]) -> list[str]:
    """What is wrong with the worked call an operation's description ends with: none at all, a
    required value left out, a name the operation does not take, or a value the gateway refuses."""
    parameters = {parameter["name"]: parameter for parameter in operation.get("parameters", [])}
    body = operation.get("requestBody")
    description = operation.get("description", "")
    if not parameters and body is None:
        return [] if description.endswith(NO_PARAMETERS) else [f"no worked call: {NO_PARAMETERS}"]
    call = worked_call(description)
    if not call:
        return ["no worked call, such as Example: name=value, name=value."]
    required = {name for name, parameter in parameters.items() if parameter.get("required")}
    if body is not None and body.get("required"):
        required.add("body")
    problems = [f"leaves out {name}" for name in sorted(required - set(call))]
    for name, written in call.items():
        if name == "body" and body is not None:
            schema = body["content"]["application/json"]["schema"]
            try:
                value = json.loads(written[0])
            except ValueError:
                problems.append(f"body is not JSON: {written[0]}")
                continue
        elif name in parameters:
            schema = parameters[name]["schema"]
            if parameters[name]["in"] == "path" and not all(
                re.fullmatch(r"[^/\s]+", text) for text in written
            ):
                problems.append(f"{name} is not one segment of a path: {written}")
                continue
            if schema.get("type") == "array":
                value = [as_sent(text, schema["items"], document) for text in written]
            elif len(written) > 1:
                problems.append(f"{name} is given {len(written)} times")
                continue
            else:
                value = as_sent(written[0], schema, document)
        else:
            problems.append(f"{name} is not a parameter of the operation")
            continue
        if found := refusal(value, schema, document):
            problems.append(f"{name}={', '.join(written)}: {found}")
    return problems


async def test_the_contract_is_described_for_agents(client: AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()

    operations: dict[str, Any] = {
        operation["operationId"]: operation
        for path in document["paths"].values()
        for operation in path.values()
    }
    assert document["info"]["title"] == "Twake Space agent contracts"
    assert {name: operation["tags"] for name, operation in operations.items()} == {
        "read_freebusy": ["calendar.freebusy.read.v1"],
        "accept_invitation": ["calendar.invitation.accept.v1"],
        "create_event": ["calendar.event.create.v1"],
        "list_rooms": ["chat.rooms.read.v1"],
        "read_room": ["chat.rooms.read.v1"],
        "list_room_members": ["chat.members.read.v1"],
        "list_messages": ["chat.messages.read.v1"],
        "list_mailboxes": ["mail.mailboxes.read.v1"],
        "list_emails": ["mail.emails.read.v1"],
        "search_emails": ["mail.emails.read.v1"],
        "read_email": ["mail.emails.read.v1"],
        "read_thread": ["mail.threads.read.v1"],
        "list_folder_items": ["drive.file.read.v1"],
        "read_file": ["drive.file.read.v1"],
        "search_files": ["drive.file.read.v1"],
        "list_recent_files": ["drive.file.read.v1"],
        "read_file_content": ["drive.content.read.v1"],
        "list_my_tasks": ["tasks.task.read.v1"],
        "search_tasks": ["tasks.task.read.v1"],
        "read_task": ["tasks.task.read.v1"],
        "open_boards": ["tasks.board.open.v1"],
        "list_projects": ["tasks.project.read.v1"],
        "create_project": ["tasks.project.create.v1"],
        "create_task": ["tasks.task.create.v1"],
        "update_task": ["tasks.task.update.v1"],
        "complete_task": ["tasks.task.complete.v1"],
        "delete_task": ["tasks.task.delete.v1"],
        "comment_on_task": ["tasks.comment.create.v1"],
        "assign_task": ["tasks.task.assign.v1"],
        "create_reply_draft": ["mail.draft.create.v1"],
        "create_file": ["drive.file.create.v1"],
        "move_email": ["mail.email.move.v1"],
        "archive_email": ["mail.email.move.v1"],
        "trash_email": ["mail.email.trash.v1"],
        "move_emails": ["mail.email.move.v1"],
        "archive_emails": ["mail.email.move.v1"],
        "trash_emails": ["mail.email.trash.v1"],
        "list_address_books": ["contacts.addressbooks.read.v1"],
        "search_contacts": ["contacts.contacts.read.v1"],
        "read_contact": ["contacts.contacts.read.v1"],
        "create_contact": ["contacts.contact.create.v1"],
        "update_contact": ["contacts.contact.update.v1"],
        "delete_contact": ["contacts.contact.delete.v1"],
        "list_spaces": ["space.spaces.read.v1"],
        "read_space": ["space.spaces.read.v1"],
        "search_organization_people": ["space.people.read.v1"],
        "list_feed_items": ["space.feed.read.v1"],
        "read_feed_item": ["space.feed.read.v1"],
        "add_feed_reaction": ["space.reaction.add.v1"],
        "remove_feed_reaction": ["space.reaction.remove.v1"],
        "create_feed_post": ["space.post.create.v1"],
        "update_feed_post": ["space.post.update.v1"],
        "delete_feed_post": ["space.post.delete.v1"],
        "add_space_members": ["space.member.add.v1"],
        "update_space_member": ["space.member.update.v1"],
        "remove_space_member": ["space.member.remove.v1"],
    }


async def test_agents_neither_hold_a_token_nor_choose_the_user(client: AsyncClient) -> None:
    document = (await client.get("/openapi.json")).json()

    parameters = {
        parameter["name"].lower()
        for path in document["paths"].values()
        for operation in path.values()
        for parameter in operation.get("parameters", [])
    }
    assert not parameters & {
        "authorization",
        "x-twake-user",
        "x-twake-drive-token",
        "x-twake-drive-instance",
    }


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


async def test_the_schemas_agents_are_given_hold_no_reference(client: AsyncClient) -> None:
    # The harness gives the model the schema of each parameter and of the body as it finds them in
    # an operation: a reference to the document's components would reach the model unresolved
    document = (await client.get("/openapi.json")).json()

    schemas = {
        f"{operation['operationId']}.{name}": schema
        for _, _, operation in operations_of(document)
        for name, schema in [
            *(
                (parameter["name"], parameter["schema"])
                for parameter in operation.get("parameters", [])
            ),
            *(
                [("body", operation["requestBody"]["content"]["application/json"]["schema"])]
                if "requestBody" in operation
                else []
            ),
        ]
    }

    assert any(name.endswith(".body") for name in schemas), "no body found"
    assert [name for name, schema in schemas.items() if "$ref" in json.dumps(schema)] == []


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
        for problem in plain_text_problems(texts, LONGEST[level])
    ]
    assert problems == []


# What names each write of an application in its words, in English and in French
WRITES_NAMED = {
    "mail": {
        "create_reply_draft": ("drafts", "brouillons"),
        "move_email": ("move", "déplacer"),
        "archive_email": ("archive", "archiver"),
        "trash_email": ("trash", "corbeille"),
        "move_emails": ("move", "déplacer"),
        "archive_emails": ("archive", "archiver"),
        "trash_emails": ("trash", "corbeille"),
    },
    "calendar": {
        "accept_invitation": ("accept", "accepter"),
        "create_event": ("add events", "ajouter des événements"),
    },
    "tasks": {
        "open_boards": ("open your boards", "ouvrir tes tableaux"),
        "create_project": ("create projects", "créer des projets"),
        "create_task": ("create projects and tasks", "créer des projets et des tâches"),
        "update_task": ("edit", "modifier"),
        "complete_task": ("complete", "terminer"),
        "delete_task": ("delete", "supprimer"),
        "comment_on_task": ("comment", "commenter"),
        "assign_task": ("assign", "assigner"),
    },
    "contacts": {
        "create_contact": ("create", "créer"),
        "update_contact": ("change", "modifier"),
        "delete_contact": ("delete", "supprimer"),
    },
    "space": {
        "add_feed_reaction": ("react", "réagir"),
        "remove_feed_reaction": ("take your reactions back", "retirer tes réactions"),
        "create_feed_post": ("post", "publier"),
        "update_feed_post": ("edit", "modifier"),
        "delete_feed_post": ("delete", "supprimer"),
        "add_space_members": ("add members", "ajouter des membres"),
        "update_space_member": ("change their roles", "changer leur rôle"),
        "remove_space_member": ("remove them", "les retirer"),
    },
}


# What the words of an application for reading name beyond the user's own data, in English and in
# French
READS_NAMED = {
    "contacts": [
        ("your organization's directory", "l'annuaire de ton organisation"),
        ("the address books shared with you", "les carnets partagés avec toi"),
    ],
    "tasks": [("your projects", "tes projets")],
}


@pytest.mark.parametrize("domain", sorted(READS_NAMED))
async def test_the_words_of_an_application_name_all_its_reads_cover(
    client: AsyncClient, domain: str
) -> None:
    # The harness asks the owner once for all the reads of an application, in its words: those of
    # Contacts name the directory of the user's organization and the books others share with them,
    # which its reads cover too
    document = (await client.get("/openapi.json")).json()
    words = document["x-twake-domains"][domain]["read"]

    unnamed = [
        (en, fr) for en, fr in READS_NAMED[domain] if en not in words["en"] or fr not in words["fr"]
    ]

    assert unnamed == []


@pytest.mark.parametrize("domain", sorted(WRITES_NAMED))
async def test_the_words_of_an_application_name_each_of_its_writes(
    client: AsyncClient, domain: str
) -> None:
    # The harness asks the owner once for all the writes of an application, in its words: they must
    # name every write there, so that a merge that keeps the words of one contract fails
    document = (await client.get("/openapi.json")).json()
    words = document["x-twake-domains"][domain]["write"]
    named = WRITES_NAMED[domain]

    writes = {
        operation["operationId"]
        for _, method, operation in operations_of(document)
        if method != "get" and operation["tags"][0].startswith(f"{domain}.")
    }

    assert writes == set(named)
    unnamed = [
        name for name, (en, fr) in named.items() if en not in words["en"] or fr not in words["fr"]
    ]
    assert unnamed == []


async def test_each_move_of_one_email_sends_several_to_its_batch(client: AsyncClient) -> None:
    # Asked to trash 35 emails, the model called trash_email once per email, and the harness
    # stopped it at its limit of tool calls per message: each move of one email tells the model
    # to move several in one call, and the batch to call it instead of one call per email
    document = (await client.get("/openapi.json")).json()
    descriptions = {
        operation["operationId"]: operation["description"]
        for _, _, operation in operations_of(document)
    }
    batches = {
        "move_email": "move_emails",
        "archive_email": "archive_emails",
        "trash_email": "trash_emails",
    }

    unsent = [
        single
        for single, batch in batches.items()
        if f"For several emails, call {batch} once with all their ids" not in descriptions[single]
    ]
    unnamed = [
        batch
        for single, batch in batches.items()
        if f"rather than {single} once per email" not in descriptions[batch]
    ]

    assert unsent == []
    assert unnamed == []


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


async def test_the_writes_that_tell_what_they_would_do_declare_it(client: AsyncClient) -> None:
    # The harness asks a contract what a call would do only when its operation declares
    # x-twake-preview: true, since a contract that never promised it would take the question for
    # the call itself; it ignores the declaration on a read
    document = (await client.get("/openapi.json")).json()

    declared = {
        operation["operationId"]: (method, operation["x-twake-preview"])
        for _, method, operation in operations_of(document)
        if "x-twake-preview" in operation
    }

    assert declared == {
        "accept_invitation": ("post", True),
        "create_event": ("post", True),
        "create_reply_draft": ("post", True),
        "move_email": ("post", True),
        "archive_email": ("post", True),
        "trash_email": ("post", True),
        "move_emails": ("post", True),
        "archive_emails": ("post", True),
        "trash_emails": ("post", True),
        "create_task": ("post", True),
        "update_task": ("patch", True),
        "complete_task": ("post", True),
        "create_project": ("post", True),
        "delete_task": ("delete", True),
        "comment_on_task": ("post", True),
        "assign_task": ("put", True),
        "create_file": ("post", True),
        "create_contact": ("post", True),
        "update_contact": ("patch", True),
        "delete_contact": ("delete", True),
        "add_feed_reaction": ("post", True),
        "remove_feed_reaction": ("post", True),
        "create_feed_post": ("post", True),
        "update_feed_post": ("patch", True),
        "delete_feed_post": ("delete", True),
        "add_space_members": ("post", True),
        "update_space_member": ("patch", True),
        "remove_space_member": ("delete", True),
    }


async def test_the_harness_alone_asks_for_a_preview(client: AsyncClient) -> None:
    # The headers of a preview are the harness's: a model never sets them as parameters
    document = (await client.get("/openapi.json")).json()

    parameters = {
        parameter["name"].lower()
        for _, _, operation in operations_of(document)
        for parameter in operation.get("parameters", [])
    }

    assert not parameters & {"x-twake-preview", "x-twake-preview-digest", "accept-language"}


async def test_accepting_an_invitation_is_a_low_risk_write(client: AsyncClient) -> None:
    # The user's own answer: once the owner allowed writing in Calendar, it runs without asking
    document = (await client.get("/openapi.json")).json()

    accept = document["paths"]["/contracts/v1/calendar/invitations/accept"]["post"]

    assert accept["x-twake-risk"] == "low"


async def test_the_body_of_accepting_an_invitation_is_whole_and_closed(
    client: AsyncClient,
) -> None:
    # The UID comes in the body, which holds any text iCalendar allows in one, slashes included,
    # where the gateway routes a path parameter as one segment: the model gets the body whole,
    # taking the UID and no other field
    document = (await client.get("/openapi.json")).json()

    accept = document["paths"]["/contracts/v1/calendar/invitations/accept"]["post"]
    schema = accept["requestBody"]["content"]["application/json"]["schema"]

    assert "parameters" not in accept
    assert "$ref" not in json.dumps(schema)
    assert sorted(schema["properties"]) == ["uid"]
    assert schema["required"] == ["uid"]
    assert schema["additionalProperties"] is False


async def test_creating_an_event_is_a_low_risk_write(client: AsyncClient) -> None:
    # The user's own time, with nobody invited: once the owner allowed writing in Calendar, it runs
    # without asking
    document = (await client.get("/openapi.json")).json()

    create = document["paths"]["/contracts/v1/calendar/events"]["post"]

    assert create["x-twake-risk"] == "low"


async def test_the_body_of_a_new_event_is_whole_and_closed(client: AsyncClient) -> None:
    # The model gets the body as the document writes it: whole, taking these fields and no other,
    # so that no attendee, repetition or alarm ever reaches Calendar
    document = (await client.get("/openapi.json")).json()

    create = document["paths"]["/contracts/v1/calendar/events"]["post"]
    schema = create["requestBody"]["content"]["application/json"]["schema"]

    assert "$ref" not in json.dumps(schema)
    assert sorted(schema["properties"]) == sorted(
        ["title", "start", "end", "time_zone", "busy", "location", "description"]
    )
    assert sorted(schema["required"]) == ["end", "start", "title"]
    assert schema["additionalProperties"] is False


async def test_creating_and_changing_a_contact_are_low_risk_writes(client: AsyncClient) -> None:
    # The user's own contacts, which nobody is told of: once the owner allowed writing in Contacts,
    # each runs without asking
    document = (await client.get("/openapi.json")).json()

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, _, operation in operations_of(document)
        if operation["operationId"] in ("create_contact", "update_contact")
    }

    assert risks == {"create_contact": "low", "update_contact": "low"}


async def test_deleting_a_contact_is_a_high_risk_write(client: AsyncClient) -> None:
    # Contacts keeps no trash: the owner confirms each contact deleted
    document = (await client.get("/openapi.json")).json()

    path = "/contracts/v1/contacts/address-books/{book_id}/contacts/{contact_id}"
    deleting = document["paths"][path]["delete"]

    assert deleting["x-twake-risk"] == "high"
    assert "requestBody" not in deleting


@pytest.mark.parametrize("operation_id", ["create_contact", "update_contact"])
async def test_the_bodies_of_the_contacts_writes_are_whole_and_closed(
    client: AsyncClient, operation_id: str
) -> None:
    # The model gets the body as the document writes it: whole, taking these fields and no other
    document = (await client.get("/openapi.json")).json()
    operations = {
        operation["operationId"]: operation for _, _, operation in operations_of(document)
    }

    schema = operations[operation_id]["requestBody"]["content"]["application/json"]["schema"]

    assert "$ref" not in json.dumps(schema)
    assert sorted(schema["properties"]) == sorted(
        [
            "name",
            "given_name",
            "family_name",
            "nickname",
            "emails",
            "phones",
            "organization",
            "title",
            "addresses",
            "note",
            "birthday",
        ]
    )
    assert schema["additionalProperties"] is False
    items = [
        schema["properties"][name]["anyOf"][0]["items"]
        for name in ("emails", "phones", "addresses")
    ]
    assert [item["additionalProperties"] for item in items] == [False, False, False]


async def test_reacting_and_taking_a_reaction_back_are_low_risk_writes(
    client: AsyncClient,
) -> None:
    # The user's own reaction, which they take back at will: once the owner allowed writing in
    # Space, each runs without asking
    document = (await client.get("/openapi.json")).json()

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, _, operation in operations_of(document)
        if operation["operationId"] in ("add_feed_reaction", "remove_feed_reaction")
    }

    assert risks == {"add_feed_reaction": "low", "remove_feed_reaction": "low"}


@pytest.mark.parametrize("operation_id", ["add_feed_reaction", "remove_feed_reaction"])
async def test_a_reaction_is_one_of_those_twake_space_offers(
    client: AsyncClient, operation_id: str
) -> None:
    # The model reacts with what the Space web app offers, never with words members would read
    document = (await client.get("/openapi.json")).json()
    operations = {
        operation["operationId"]: operation for _, _, operation in operations_of(document)
    }

    schema = operations[operation_id]["requestBody"]["content"]["application/json"]["schema"]

    assert "$ref" not in json.dumps(schema)
    assert schema["required"] == ["key"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["key"]["enum"] == [
        "\N{THUMBS UP SIGN}",
        "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}",
        "\N{FACE WITH TEARS OF JOY}",
        "\N{PARTY POPPER}",
        "\N{EYES}",
        "\N{PERSON WITH FOLDED HANDS}",
    ]


async def test_posting_editing_and_deleting_a_post_are_high_risk_writes(
    client: AsyncClient,
) -> None:
    # Every member of the space reads a post: the owner confirms each one
    document = (await client.get("/openapi.json")).json()

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, _, operation in operations_of(document)
        if operation["operationId"] in ("create_feed_post", "update_feed_post", "delete_feed_post")
    }

    assert risks == dict.fromkeys(
        ("create_feed_post", "update_feed_post", "delete_feed_post"), "high"
    )


async def test_adding_changing_and_removing_members_are_high_risk_writes(
    client: AsyncClient,
) -> None:
    # Who sees what a space holds, and who manages it, changes: the owner confirms each call
    document = (await client.get("/openapi.json")).json()
    writes = ("add_space_members", "update_space_member", "remove_space_member")

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, _, operation in operations_of(document)
        if operation["operationId"] in writes
    }

    assert risks == dict.fromkeys(writes, "high")


async def test_the_bodies_of_the_member_writes_are_whole_and_closed(client: AsyncClient) -> None:
    # The model gets each body as the document writes it: whole, taking these fields and no other
    document = (await client.get("/openapi.json")).json()
    operations = {
        operation["operationId"]: operation for _, _, operation in operations_of(document)
    }

    schemas = {
        name: operations[name]["requestBody"]["content"]["application/json"]["schema"]
        for name in ("add_space_members", "update_space_member")
    }

    assert "requestBody" not in operations["remove_space_member"]
    assert "$ref" not in json.dumps(schemas)
    assert {name: sorted(schema["required"]) for name, schema in schemas.items()} == {
        "add_space_members": ["role", "usernames"],
        "update_space_member": ["role"],
    }
    assert [schema["additionalProperties"] for schema in schemas.values()] == [False, False]
    usernames = schemas["add_space_members"]["properties"]["usernames"]
    assert (usernames["minItems"], usernames["maxItems"]) == (1, 20)
    assert [schema["properties"]["role"]["enum"] for schema in schemas.values()] == [
        ["viewer", "editor", "admin"]
    ] * 2


@pytest.mark.parametrize("operation_id", ["create_feed_post", "update_feed_post"])
async def test_the_bodies_of_the_posts_are_whole_and_closed(
    client: AsyncClient, operation_id: str
) -> None:
    # The model gets the body as the document writes it: whole, its text and nothing else
    document = (await client.get("/openapi.json")).json()
    operations = {
        operation["operationId"]: operation for _, _, operation in operations_of(document)
    }

    schema = operations[operation_id]["requestBody"]["content"]["application/json"]["schema"]

    assert "$ref" not in json.dumps(schema)
    assert schema["required"] == ["text"]
    assert schema["additionalProperties"] is False
    text = schema["properties"]["text"]
    assert (text["type"], text["minLength"], text["maxLength"]) == ("string", 1, 4000)


async def test_creating_a_file_is_a_low_risk_write(client: AsyncClient) -> None:
    # A new file only the user sees: once the owner allowed writing in Drive, it runs without asking
    document = (await client.get("/openapi.json")).json()

    create = document["paths"]["/contracts/v1/drive/files"]["post"]

    assert create["x-twake-risk"] == "low"


async def test_moving_an_email_is_a_low_risk_write(client: AsyncClient) -> None:
    # The emails can be moved back, one or several at once: once the owner allowed writing in
    # Mail, each move runs without asking
    document = (await client.get("/openapi.json")).json()

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, _, operation in operations_of(document)
    }

    moves = (
        "move_email",
        "archive_email",
        "trash_email",
        "move_emails",
        "archive_emails",
        "trash_emails",
    )
    assert {name: risks.get(name) for name in moves} == dict.fromkeys(moves, "low")


async def test_opening_tasks_is_a_low_risk_write(client: AsyncClient) -> None:
    # Opening Tasks sets up the user's own Inbox and accepts the invitations made to them: once
    # the owner allowed writing in Tasks, it runs without asking
    document = (await client.get("/openapi.json")).json()

    opening = document["paths"]["/contracts/v1/tasks/boards/open"]["post"]

    assert opening["x-twake-risk"] == "low"


async def test_creating_changing_and_completing_a_task_are_low_risk_writes(
    client: AsyncClient,
) -> None:
    # The user's own work on boards they edit: once the owner allowed writing in Tasks, each runs
    # without asking, though Tasks emails those who follow the task
    document = (await client.get("/openapi.json")).json()

    risks = {
        operation["operationId"]: operation.get("x-twake-risk")
        for _, _, operation in operations_of(document)
        if operation["operationId"] in ("create_task", "update_task", "complete_task")
    }

    assert risks == {"create_task": "low", "update_task": "low", "complete_task": "low"}


async def test_creating_a_project_is_a_low_risk_write(client: AsyncClient) -> None:
    # A project the user alone is a member of, which notifies nobody: once the owner allowed
    # writing in Tasks, it runs without asking
    document = (await client.get("/openapi.json")).json()

    create = document["paths"]["/contracts/v1/tasks/projects"]["post"]

    assert create["x-twake-risk"] == "low"


async def test_deleting_a_task_is_a_high_risk_write(client: AsyncClient) -> None:
    # The task and its subtasks leave the board, for the trash Tasks empties after 30 days: the
    # owner confirms each task deleted
    document = (await client.get("/openapi.json")).json()

    path = "/contracts/v1/tasks/boards/{board_id}/tasks/{task_id}"
    deleting = document["paths"][path]["delete"]

    assert deleting["x-twake-risk"] == "high"
    assert "requestBody" not in deleting


async def test_commenting_on_a_task_is_a_high_risk_write(client: AsyncClient) -> None:
    # A comment stays, and Tasks emails the people who follow the task of it: the owner confirms
    # each comment
    document = (await client.get("/openapi.json")).json()

    path = "/contracts/v1/tasks/boards/{board_id}/tasks/{task_id}/comments"
    commenting = document["paths"][path]["post"]

    assert commenting["x-twake-risk"] == "high"


async def test_assigning_a_task_is_a_high_risk_write(client: AsyncClient) -> None:
    # Tasks emails each new assignee: the owner confirms each assignment
    document = (await client.get("/openapi.json")).json()

    path = "/contracts/v1/tasks/boards/{board_id}/tasks/{task_id}/assignees"
    assigning = document["paths"][path]["put"]

    assert assigning["x-twake-risk"] == "high"


async def test_the_bodies_of_the_tasks_writes_are_whole_and_closed(client: AsyncClient) -> None:
    # The model gets each body as the document writes it: whole, taking these fields and no other
    document = (await client.get("/openapi.json")).json()
    operations = {
        operation["operationId"]: operation for _, _, operation in operations_of(document)
    }

    schemas = {
        name: operations[name]["requestBody"]["content"]["application/json"]["schema"]
        for name in (
            "create_project",
            "create_task",
            "update_task",
            "comment_on_task",
            "assign_task",
        )
    }

    assert "requestBody" not in operations["open_boards"]
    assert "requestBody" not in operations["complete_task"]
    assert "$ref" not in json.dumps(schemas)
    assert {name: sorted(schema["properties"]) for name, schema in schemas.items()} == {
        "create_project": ["key_prefix", "name"],
        "create_task": sorted(
            ["title", "section_id", "parent_id", "priority", "due_date", "due_time", "due_zone"]
        ),
        "update_task": sorted(
            ["title", "priority", "due_date", "due_time", "due_zone", "deadline"]
        ),
        "comment_on_task": ["body"],
        "assign_task": ["assignees"],
    }
    assert [schema["additionalProperties"] for schema in schemas.values()] == [False] * 5
    assert schemas["assign_task"]["required"] == ["assignees"]
    assert schemas["assign_task"]["properties"]["assignees"]["maxItems"] == 50


async def test_the_bodies_of_the_batched_mail_moves_are_whole_and_closed(
    client: AsyncClient,
) -> None:
    # The model gets each body as the document writes it, whole, and the gateway checks each call
    # against it: 1 to 50 email ids and nothing else, but the one mailbox of move_emails
    document = (await client.get("/openapi.json")).json()
    operations = {
        operation["operationId"]: operation for _, _, operation in operations_of(document)
    }

    schemas = {
        name: operations[name]["requestBody"]["content"]["application/json"]["schema"]
        for name in ("move_emails", "archive_emails", "trash_emails")
    }

    assert "$ref" not in json.dumps(schemas)
    assert {name: sorted(schema["properties"]) for name, schema in schemas.items()} == {
        "move_emails": ["email_ids", "mailbox_id", "mailbox_name"],
        "archive_emails": ["email_ids"],
        "trash_emails": ["email_ids"],
    }
    assert [schema["additionalProperties"] for schema in schemas.values()] == [False] * 3
    assert [schema["required"] for schema in schemas.values()] == [["email_ids"]] * 3
    for schema in schemas.values():
        ids = schema["properties"]["email_ids"]
        assert (ids["type"], ids["minItems"], ids["maxItems"]) == ("array", 1, 50)
        assert ids["items"] == {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,255}$"}
    # As move_email's: exactly one mailbox, by its id or by its name
    assert schemas["move_emails"]["oneOf"] == [
        {"required": ["mailbox_id"]},
        {"required": ["mailbox_name"]},
    ]


async def test_each_description_ends_with_a_worked_call_the_gateway_accepts(
    client: AsyncClient,
) -> None:
    # Since a demo in which the model sent times that the gateway refused: each value written in
    # the exact format its parameter takes
    document = (await client.get("/openapi.json")).json()

    problems = [
        f"{operation['operationId']}: {problem}"
        for _, _, operation in operations_of(document)
        for problem in worked_call_problems(operation, document)
    ]

    assert problems == []


def test_an_operation_without_parameters_ends_with_an_empty_worked_call() -> None:
    # None has yet: the first, such as a list of the user's boards, shows the model its call too
    operation = {"operationId": "list_boards", "description": "Lists the user's boards."}

    assert worked_call_problems(operation, {}) != []
    operation["description"] += " Example: none."
    assert worked_call_problems(operation, {}) != []
    operation["description"] = "Lists the user's boards. Example: (no parameters)."
    assert worked_call_problems(operation, {}) == []
