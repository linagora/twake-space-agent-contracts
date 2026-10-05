# twake-space-agent-contracts

APISIX contract backend for the Twake Space personal agents.

Agents never call Twake applications directly: they call versioned contracts through APISIX, which authenticates the agent, names the user it acts for and audits every call. This service answers those contracts. Its OpenAPI document, served at `/openapi.json`, is what APISIX's `openapi-to-mcp` turns into the agents' MCP tools, one tool per operation id.

## Contracts

### `events.read.v1`

Reads the workplace events stored for the user the agent acts for.

| Operation | Request | Answer |
|---|---|---|
| `read_event` | `GET /contracts/v1/events/{event_id}` | the event, if the user is one of its targets |
| `list_events` | `GET /contracts/v1/events?type=…&limit=…` | `{"events": [...]}`, the user's events newest first |

- APISIX names the user in the `X-Twake-User` header, as a uid, and only APISIX may set it.
- `limit` goes from 1 to 100 and is 20 by default.
- Pass `type=com.twake.calendar.event.invited.v1` to list meeting invitations.
- An event the user is not a target of answers exactly like an unknown one, so the contract never reveals that an event exists.

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem (`application/problem+json`) with a stable `code`:

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_request` | a parameter is invalid |
| 401 | `missing_user` | the `X-Twake-User` header is missing or empty |
| 404 | `event_not_found` | no event with this id concerns the user |

## The table it reads

[`sql/workplace_events.sql`](sql/workplace_events.sql) is the reference schema of `workplace_events`. Storage writes it from `twake.workplace.events.v1`, and storage's migration must create exactly this table. The service only reads it, so give it a read-only user.

## Run

```sh
DATABASE_URL=postgresql://reader:secret@localhost:5432/events \
  uv run uvicorn --factory twake_space_agent_contracts.app:create_app_from_env --port 8080
```

The image `ghcr.io/linagora/twake-space-agent-contracts` listens on 8080 as user 10001 and reads `DATABASE_URL` too. It is published as `latest` from `main` and with the version from `v*` tags.

## Test

The tests call the HTTP API against a real PostgreSQL that they start with Docker.

```sh
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
```

## License

[AGPL-3.0](LICENSE)
