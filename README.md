# twake-space-agent-contracts

APISIX contract backend for the Twake Space personal agents.

Agents never call Twake applications directly: they call versioned contracts through APISIX, which authenticates the agent, attaches the access token of the user it acts for and audits every call. This service answers those contracts, as that user. Its OpenAPI document, served at `/openapi.json`, is what agents turn into their tools, one tool per operation id.

## The user

APISIX gets the user's access token from the token broker (forward-auth) and passes it as `Authorization: Bearer …`. A user without a valid delegation never reaches the service: the broker answers APISIX with its typed error and the consent link, which APISIX returns as is.

The service checks the token against the signing keys of LemonLDAP-NG:

- an access token (`typ` `at+JWT`), signed with RS256, from the issuer, with the audience `twake-space-agents` and the `client_id` of that client, the token broker's own;
- the keys are fetched at the first request, which the others wait for, then again after an hour, so that a key the issuer withdraws stops being trusted, and at most once a minute for a key id they do not hold;
- while the issuer does not answer, the keys already fetched still serve, and it is asked again every 30 seconds.

The user is the token's subject, their email, lowercased. Neither the token nor the user appears in the OpenAPI document: an agent never holds a user's token, nor chooses whom it acts for.

## Applications

A contract belongs to the application its id starts with, its domain, such as `calendar` for `calendar.freebusy.read.v1`. Each application is declared once, in [`applications.py`](src/twake_space_agent_contracts/applications.py): its domain, the words the harness names it with, and the routers of its contracts, one per contract.

The service publishes only the applications `PUBLISHED_APPS` names, `events` and `calendar` when it is unset: it serves their contracts and describes them in its OpenAPI document, while the paths of any other application answer 404 `not_found`, like a path the service never had. The operator keeps it equal to the applications APISIX routes, so that an application leaves the agents' tools when it leaves the gateway. A name the service does not know stops it from starting.

Before an assistant first reads in an application, and before it first writes there, the harness asks its owner, naming the application and saying what reading or writing covers there. It takes those words from the root of the OpenAPI document, in `x-twake-domains`, which holds the published applications only:

```json
"x-twake-domains": {
  "calendar": {
    "name": { "en": "Twake Calendar", "fr": "Twake Agenda" },
    "read": {
      "en": "see your free and busy times in your calendars",
      "fr": "voir tes créneaux libres et occupés dans tes agendas"
    },
    "write": {
      "en": "accept the invitations you received, which tells their organizer",
      "fr": "accepter les invitations que tu as reçues, ce qui prévient leur organisateur"
    }
  }
}
```

The words are in English and in French, addressed to the owner, and plain text on one line without a final period: no markup character (`` \ ` * _ ~ [ ] < > & ``), and nothing that looks like a link, an address or a domain name, such as a dot inside a word. A name takes at most 64 characters and what a level covers 200. `read` and `write` are given for the levels the application offers only: a `GET` contract reads, any other writes. The harness ignores an entry that breaks these rules; the tests refuse it first.

## Contracts

### `events.read.v1`

Reads the workplace events stored for the user the agent acts for: those whose targets name the user's email (`data.targets[].native_id`). An event sent to another address of the user, such as an alias, is not found.

| Operation | Request | Answer |
|---|---|---|
| `read_event` | `GET /contracts/v1/events/{event_id}` | the event, if the user is one of its targets |
| `list_events` | `GET /contracts/v1/events?type=…&limit=…` | `{"events": [...]}`, the user's events newest first |

- `limit` goes from 1 to 100 and is 20 by default.
- Pass `type=com.twake.calendar.event.invited.v1` to list meeting invitations.
- An event the user is not a target of answers exactly like an unknown one, so the contract never reveals that an event exists.

### `calendar.freebusy.read.v1`

Tells whether the user is free over a period, from all their calendars, as Calendar shows them to the user.

| Operation | Request | Answer |
|---|---|---|
| `read_freebusy` | `GET /contracts/v1/calendar/freebusy?start=…&end=…&exclude=…` | `{"start", "end", "free", "busy": [{"start", "end"}]}`, in UTC |

- `start` and `end` are RFC 3339 times with their offset; the period must end after it starts and last at most 31 days.
- `exclude`, repeated, lists the UIDs of the events to leave out, such as the invitation being decided about, which already sits in the user's calendar.
- The service goes through the Calendar side service with the user's token: `GET /api/users?email=` for the user's id, then `POST /dav/calendars/freebusy`, the JSON free/busy of esn-sabre 2.4.6 or later, which writes its times in UTC.

### `calendar.invitation.accept.v1`

Accepts, as the user, an invitation the user received: only their own participation changes, and Calendar tells the organizer.

| Operation | Request | Answer |
|---|---|---|
| `accept_invitation` | `POST /contracts/v1/calendar/invitations/{event_id}/accept` | `{"event_id", "uid", "partstat": "ACCEPTED"}` |

- `event_id` is the id of a stored invitation (`com.twake.calendar.event.invited.v1`) sent to the user; its `data.object.uid` names the calendar event.
- The service finds the user's own copy of the event with the JSON `REPORT /dav/calendars/<user id>.json` of esn-sabre on `{"uid"}`, sets `PARTSTAT=ACCEPTED` on the user's `ATTENDEE`, and puts the event back in jCal. esn-sabre then sends the iTIP reply to the organizer.
- Nothing else in the event changes: esn-sabre refuses an attendee who changes what the organizer set.
- A recurring invitation is refused, since the stored invitation does not say which occurrence it is about: the user answers it in Calendar. So is a cancelled event, which stays in the user's calendar but whose organizer esn-sabre would not tell.
- The side service does not forward `If-Match`, so the write cannot be conditional: it follows the read at once.
- Agents call it only once the user has said yes to this invitation; approval happens in the conversation for now.

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem (`application/problem+json`) with a stable `code`:

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_request` | a parameter is invalid |
| 401 | `missing_token` | no bearer token |
| 401 | `invalid_token` | the token is not one the broker got for this service: another type, issuer, audience, client or key, or expired |
| 404 | `event_not_found` | no event with this id concerns the user |
| 404 | `invitation_not_found` | no invitation with this id was sent to the user |
| 404 | `invitation_not_in_calendar` | the user's calendars no longer have the invitation, which may have been deleted |
| 404 | `calendar_user_not_found` | Calendar has no user with the user's email |
| 409 | `not_an_attendee` | the invitation in the user's calendar does not list the user as an attendee |
| 409 | `recurring_invitation` | the invitation repeats, or is one occurrence of a series |
| 409 | `invitation_cancelled` | the organizer cancelled the event |
| 502 | `calendar_refused` | Calendar refused the user's token |
| 502 | `calendar_unavailable` | Calendar did not answer, or answered in an unexpected form |
| 503 | `keys_unavailable` | the signing keys of LemonLDAP-NG could not be fetched, and none are held |

Routing errors, such as an unknown path, use the same format, with a `code` named after their HTTP status (`not_found`, `method_not_allowed`).

## The table it reads

[`sql/workplace_events.sql`](sql/workplace_events.sql) is the reference schema of `workplace_events`. Storage writes it from `twake.workplace.events.v1`, and storage's migration must create exactly this table and its indexes, including the one on the targets' emails the service searches by. The service only reads it, so give it a read-only user.

## Run

```sh
DATABASE_URL=postgresql://reader:secret@localhost:5432/events \
OIDC_ISSUER=https://sign-up.dev.twake.lin-saas.com/ \
CALENDAR_URL=https://calendar-backend.dev.twake.lin-saas.com \
  uv run uvicorn --factory twake_space_agent_contracts.app:create_app_from_env --port 8080
```

| Variable | |
|---|---|
| `DATABASE_URL` | the events database, as a read-only user |
| `OIDC_ISSUER` | the issuer of the users' tokens, exactly as in their `iss` claim |
| `OIDC_AUDIENCE` | the audience the tokens must have, `twake-space-agents` by default |
| `OIDC_JWKS_URL` | the issuer's signing keys, `<issuer>/oauth2/jwks` by default, where LemonLDAP-NG publishes them |
| `CALENDAR_URL` | the Calendar side service |
| `PUBLISHED_APPS` | the applications the service publishes, by domain, comma separated: `events,calendar` when unset (see [Applications](#applications)) |

The image `ghcr.io/linagora/twake-space-agent-contracts` listens on 8080 as user 10001 and reads the same variables. It is published as `latest` from `main` and with the version from `v*` tags.

## Test

The tests call the HTTP API against a real PostgreSQL that they start with Docker. LemonLDAP-NG's signing keys and the Calendar side service are faked at the HTTP boundary.

```sh
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
```

## License

[AGPL-3.0](LICENSE)
