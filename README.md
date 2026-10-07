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

On a Drive contract, APISIX also passes the token of the user's Drive instance, and its host: see [Drive, as the user](#drive-as-the-user).

## Applications

A contract belongs to the application its id starts with, its domain, such as `calendar` for `calendar.freebusy.read.v1`. Each application is declared once, in [`applications.py`](src/twake_space_agent_contracts/applications.py): its domain, the words the harness names it with, and the routers of its contracts, one per contract.

The service publishes only the applications `PUBLISHED_APPS` names, `calendar` when it is unset or empty: it serves their contracts and describes them in its OpenAPI document, while the paths of any other application answer 404 `not_found`, like a path the service never had. The operator keeps it equal to the applications APISIX routes, so that an application leaves the agents' tools when it leaves the gateway. A name the service does not know stops it from starting, `events` among them since [`events.read.v1` was retired](#eventsreadv1-retired).

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
      "en": "accept the invitations you received, which tells their organizer, and add events to your calendar, with nobody invited",
      "fr": "accepter les invitations que tu as reçues, ce qui prévient leur organisateur, et ajouter des événements à ton agenda, sans y inviter personne"
    }
  }
}
```

The words are in English and in French, addressed to the owner, and plain text on one line without a final period: no markup character (`` \ ` * _ ~ [ ] < > & ``), and nothing that looks like a link, an address or a domain name, such as a dot inside a word. A name takes at most 64 characters and what a level covers 200. `read` and `write` are given for the levels the application offers only: a `GET` contract reads, any other writes. The harness ignores an entry that breaks these rules; the tests refuse it first.

### Adding an application

An application comes as a module of its own, as Calendar does: its client, its `<APP>_URL` setting, its typed problems, its routers, one per contract, its tests at the HTTP boundary and its section below. It is declared by one entry of `APPLICATIONS`, in `applications.py`: its domain, its words, and a function that builds its routers from the service's settings, HTTP client and caller dependency. The tests publish every declared application; a deployment publishes it once `PUBLISHED_APPS` names it.

### Putting an application in service

In this order:

1. **LemonLDAP-NG.** Give the `twake-space-agents` client the audience the application checks and the attributes it needs, then restart the token broker: it keeps each user's access token until shortly before it expires, and a token carries a new audience only from its next refresh.
2. **The gateway's routes**, in the `apisix-contracts` values of the deployment repository, applied before the new image of this service or with it: the agents see a contract's tool as soon as the service publishes it, and without its route a call answers 404.
3. **The new image and `PUBLISHED_APPS`**, with the application's domain added to the setting, kept equal to the applications the gateway routes.
4. **The address of the OpenAPI document** in the gateway's values, against which it checks every call, once the new pods serve. APISIX fetches the document at the first call that needs it and keeps it an hour by its address: changed earlier, the new address could keep an old pod's document for that hour. Each new document takes a new address: with each image, as `?image=<digest>`, and with each change of `PUBLISHED_APPS`.
5. **The network path** from this service to the application, at its `<APP>_URL`: the application must accept traffic from this service's namespace.
6. **A check end to end** on dev, with a test owner's real token, through the gateway and the harness: the consent question, the answer, the call and its audit record.

To switch an application off, take it out of `PUBLISHED_APPS`: its paths answer 404 as soon as the new pods serve, then give the document a new address, and its tools leave the agents when the harness next reads the document, within minutes. Then remove its routes.

## Contracts

Every contract keeps the rules of the capability catalog:

- A `GET` contract reads, and any other writes. Every write declares in `x-twake-risk` whether it is `low`, which the owner's consent to write in its application covers, or `high`, which the owner confirms call by call; the harness takes a write that declares neither for a high one, and the tests refuse it.
- Every operation's description ends with a worked call, its values in the exact format the gateway checks: `Example: email_id=0f9c….`, or `Example, <what it is an example of>: name=value, name=value.`, and `Example: (no parameters).` for an operation that takes none. A list gives its name once per value, and a body is written `body=<JSON>`. The tests check each value against the operation's schema in the document, as the gateway does.
- The schema of a parameter or of a body is written whole in its operation, without a reference to the document's components: the harness gives it to the model as it is.
- A contract that makes the application notify other people says so in its description, as `accept_invitation` does of the organizer.
- Text other people wrote, which an agent reads as data and never as instructions, comes back in an `untrusted` object, separately from what the contract computed.
- A write that can tell what a call would do without doing it declares `x-twake-preview: true`, and its owner reads that rather than the call when the harness asks them: see [Previews](#previews).

### `events.read.v1`, retired

`read_event` and `list_events` read the workplace events stored for the user, which a Kafka bus brought and its storage wrote in the `workplace_events` table of a PostgreSQL database. Nothing writes that table since the bus was removed in October 2026: the harness now hears from RabbitMQ of what concerns an assistant, such as an invitation, with the UID of its event, which `accept_invitation` takes. The two contracts are gone from the service and from its OpenAPI document, and the service's only use of a database with them:

- `events` is no longer an application of the service. It is not published whatever `PUBLISHED_APPS` says, as it was, and a setting that still names it stops the service from starting.
- An operator takes `events` out of `PUBLISHED_APPS` with the new image, gives the OpenAPI document a new address, then removes the gateway's routes of `events`, as for [switching an application off](#putting-an-application-in-service).
- The service reads neither `DATABASE_URL` nor any other setting of a database, and needs no user of the events database: a deployment can drop both, and the service ignores a `DATABASE_URL` it is still given.
- `sql/workplace_events.sql`, the reference schema of the table it read, is gone.

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
| `accept_invitation` | `POST /contracts/v1/calendar/invitations/accept` `{"uid"}` | `{"uid", "partstat": "ACCEPTED"}` |

- `uid` is the UID of the calendar event the invitation is for, which the harness reads in the invitation esn-sabre publishes on RabbitMQ for each invitee, and gives the model with it. It comes in the body, which holds any text iCalendar allows in a UID, slashes included: the gateway routes a path parameter as one segment. The body takes no other field.
- The service finds the user's own copy of the event with the JSON `REPORT /dav/calendars/<user id>.json` of esn-sabre on `{"uid"}`, sets `PARTSTAT=ACCEPTED` on the user's `ATTENDEE`, and puts the event back in jCal. esn-sabre then sends the iTIP reply to the organizer.
- A user who is not invited, as their calendars have no copy of the event, as their copy does not list them as an attendee, or as they organize it, whom Twake Calendar lists among its attendees too, as its chair, is answered `invitation_not_found` exactly as for an unknown UID, before anything else is checked: the contract never reveals that an event exists, and the organizer's assistant cannot accept the meeting the organizer called.
- Nothing else in the event changes: esn-sabre refuses an attendee who changes what the organizer set.
- A recurring invitation is refused, since a UID names the whole series and not which of its occurrences the invitation is about: the user answers it in Calendar. So is a cancelled event, which stays in the user's calendar but whose organizer esn-sabre would not tell.
- The side service does not forward `If-Match`, so the write cannot be conditional: it follows the read at once.
- Agents call it only once the user has said yes to this invitation; approval happens in the conversation for now.
- It is a low-risk write (`x-twake-risk: low`): the user's own answer, which the owner's consent to write in Calendar covers without a confirmation each time.
- It tells what it would do ([Previews](#previews)): the event's title, when it takes place and who organizes it, from the user's copy of the event, its times in the user's time zone. That zone is the one Calendar gives (`POST /api/configurations`, `core.datetime`), the deployment's when the user set none; without one the IANA database has, the times are the event's own, its zone named beside them. The digest covers the event as the user would accept it, where it is: a call made after the organizer changed it answers `changed_since_preview`.
- It changed in place in October 2026, before anything used it in production, and stays `calendar.invitation.accept.v1`: it took the id of an invitation stored in the events database, in its path (`POST /contracts/v1/calendar/invitations/{event_id}/accept`), and answered `event_id` too. `invitation_not_in_calendar` and `not_an_attendee`, which it answered then, are `invitation_not_found` now.

### `calendar.event.create.v1`

Adds an event to the user's default calendar, as the user, with nobody invited.

| Operation | Request | Answer |
|---|---|---|
| `create_event` | `POST /contracts/v1/calendar/events` `{"title", "start", "end", "time_zone", "busy", "location", "description"}` | 201, `{"uid", "start", "end", "time_zone", "all_day", "busy", "untrusted": {"title", "location", "description"}}`; 200 for the event the same call added already |

- `start` and `end` are RFC 3339 times with their offset, the event ending after it starts and lasting at most 31 days; or both days, written as `2026-10-19`, for an event of whole days, `end` being its last day, which iCalendar ends on the day after. A time without its offset, even at midnight, is not read as a day, and a time and a day together are an invalid request, as is an event outside 1900-01-01 to 9998-12-31, which its times could not be moved across time zones from.
- A timed event is written in `time_zone`, an IANA time zone, by default the user's own, the one Calendar gives (`POST /api/configurations`, `core.datetime`), the deployment's when the user set none, and in UTC when Calendar gives none the IANA database has. Calendar failing to give it answers `calendar_unavailable`: the event is not written in another zone for it. Its times carry the zone's `TZID`, and the event a `VTIMEZONE` that describes the zone from its last change of offset within the year before the event until the event ends, as iCalendar requires of each `TZID`, so that any calendar app reads its times right. An event of whole days names no time zone.
- `busy`, true by default, makes the event opaque, so that free/busy counts the user busy then; false makes it transparent. The title takes 500 characters at most, the location 500 and the description 10,000, without the blanks around them.
- The event has no organizer and no attendee, no repetition, alarm or attachment: Calendar tells nobody of it. The body takes no other field.
- It goes to the user's default calendar, whose id esn-sabre makes the user's own, `/calendars/<user id>/<user id>/`, and which esn-sabre creates when it lists the calendars of a user who has none.
- The event's UID comes from the user, its title and its times, as a UUID v5. The service looks for that UID with the JSON `REPORT` of esn-sabre before writing, so that a call made again neither adds the event twice nor writes over what the user changed since: it answers 200 with the event when the calendar holds it as the call asks, and `event_exists` when it holds it with other details, as when the user changed it, or a call asks for the same title at the same times with another location. The side service does not forward `If-None-Match`, so the write itself cannot be conditional. An event with another title, or other times, is another event.
- The service writes the event in jCal with `PUT /dav/calendars/<user id>/<user id>/<uid>.ics`, then reads it back with the same `REPORT`. The side service waits for esn-sabre up to 120 seconds, the service for the side service 10: a write it gave up waiting for is read back all the same, and answered as added when Calendar kept it.
- It is a low-risk write (`x-twake-risk: low`): the user's own time, which nobody else is told of and which the owner's consent to write in Calendar covers without a confirmation each time.
- It tells what it would do ([Previews](#previews)), once it checked the call as it would: the event's title, when it takes place, its times in the user's time zone, else in the event's, named beside them, whether it leaves the user free, where it is, and what it is for, whole when it fits; for the event the same call added already, that nothing is added. The digest covers the event, by its UID, the zone it is written in, and the event as the calendar holds it, if at all: a call made after the user's zone changed, for an event written in it, or after the event was added or removed, answers `changed_since_preview`.

### Chat, as the user

Synapse, the homeserver of Twake Chat, accepts no token of LemonLDAP-NG. The service calls its client API through the gateway's outbound route (`CHAT_URL`), which adds the token of the contracts' application service, and names the user in `user_id`: the service never holds that token. The route admits this service alone, by its key (`CHAT_GATEWAY_KEY`), so that only this service uses the application service's token.

- Chat is published once `PUBLISHED_APPS` names `chat`. The service then needs `CHAT_URL`, `CHAT_GATEWAY_KEY` and `MATRIX_SERVER_NAME`, and does not start without them; while Chat is not published, it needs none of them.
- The service presents its key on each call to `CHAT_URL`, in the `apikey` header, where APISIX's key-auth takes it by default, never in an address, which logs show. No call to another application carries it, and neither a log nor a problem shows it. A key the route does not admit answers `chat_refused`.
- The user's Matrix id is `@<local part>:<MATRIX_SERVER_NAME>` for the email `<local part>@<MATRIX_MAIL_DOMAIN>`, as the harness maps its users. A user of another mail domain has no Chat account.
- That id serves once the account lists the user's email among its addresses (`GET /_matrix/client/v3/account/3pid`), checked at the user's first call, then kept while the service runs. An account that does not list it may be someone else's: the contracts refuse it rather than guess.
- A room the user has not joined, which they may have left, answers exactly like an unknown one: the service checks each room against those the user has joined (`GET /joined_rooms`).
- What people wrote, room names and topics, display names and messages, comes back in `untrusted`, cut at 2,000 characters.
- The service calls these paths of the client API only, all with `GET`, so that the outbound route can allow them alone: `/_matrix/client/v3/account/3pid`, `/joined_rooms`, `/sync`, `/rooms/{room_id}/state/m.room.name/`, `/rooms/{room_id}/state/m.room.topic/`, `/rooms/{room_id}/state/m.room.encryption/`, `/rooms/{room_id}/joined_members` and `/rooms/{room_id}/messages`.

### `chat.rooms.read.v1`

Lists and reads the rooms the user has joined in Twake Chat, without their messages.

| Operation | Request | Answer |
|---|---|---|
| `list_rooms` | `GET /contracts/v1/chat/rooms?limit=…&unread_only=…&cursor=…` | `{"rooms": [{"room_id", "encrypted", "direct_with", "unread", "last_activity", "untrusted": {"name", "topic"}}], "next"}`, the most recently active first |
| `read_room` | `GET /contracts/v1/chat/rooms/{room_id}` | `{"room_id", "encrypted", "member_count", "untrusted": {"name", "topic"}}` |

- `limit` goes from 1 to 100 and is 20 by default. `next` is the `cursor` of the rooms that follow, `null` after the last.
- `direct_with` names whom a direct chat is with, as the user's clients marked it in `m.direct`; `unread` is how many messages the user has not read there, as Chat counts them.
- `list_rooms` reads one sync of the user (`GET /sync?timeout=0&set_presence=offline`), filtered down to each room's name, topic, encryption and last event: the user stays offline, and the presence of others is never asked for.
- `read_room` reads the room's name, topic and encryption (`GET /rooms/{room_id}/state/…`) and counts its joined members.

### `chat.members.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `list_room_members` | `GET /contracts/v1/chat/rooms/{room_id}/members?limit=…&cursor=…` | `{"members": [{"user_id", "untrusted": {"display_name"}}], "next"}`, by Matrix id |

- `limit` goes from 1 to 100 and is 50 by default. `next` is the `cursor` of the members that follow, `null` after the last.
- Nothing else of a member's profile comes back, such as their avatar.

### `chat.messages.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `list_messages` | `GET /contracts/v1/chat/rooms/{room_id}/messages?limit=…&before=…` | `{"messages": [{"event_id", "sender", "time", "kind", "untrusted": {"body"}}], "next"}`, the newest first |

- The service holds none of the user's keys, so the messages of an encrypted room, one with `m.room.encryption`, are never read: the answer is the problem `room_encrypted`, whose `room` is what `read_room` gives.
- `limit` goes from 1 to 100 and is 20 by default. Pass `next`, Synapse's own pagination token, as `before` for older messages; it is `null` once there are none.
- Only messages are read (`GET /rooms/{room_id}/messages?dir=b`, filtered on `m.room.message`). Their text is their `body`, never their HTML (`formatted_body`) nor the address of a file: for an attachment, it is its name, and `kind` says what it is, such as `image` or `file`. A deleted message, which has no text left, is left out.

### Mail, as the user

The mail contracts go through TMail's JMAP API as the user, with their token:

- The service reads the JMAP session first (`GET /jmap/session`). Its `username` must be the token's subject, whatever its case, or the contract answers `mail_account_mismatch`. Only its primary mail account (`primaryAccounts["urn:ietf:params:jmap:mail"]`) is used, the user's own: never an account delegated to the user. The account is kept 5 minutes at most per token.
- Everything else is one `POST /jmap` per step, with the method calls the contract needs and no other: `Mailbox/get`, `Email/query`, `Email/get`, `Thread/get`, `Identity/get` and `Email/set`, never `EmailSubmission/set`: no contract sends mail. None uses James's shares capability, so that TMail keeps to the user's own mailboxes, and only the request of `Identity/get` uses the submission capability, which that method needs. The service goes to the base URL it is given, never to the URLs of the session.
- A mailbox, an email or a conversation outside the user's own mailboxes, such as in a mailbox shared with them, answers exactly like an unknown one: 404, or, for one of several emails moved at once, `not_found`.
- Text other people wrote comes back under `untrusted`: names and addresses, subjects, previews and bodies, without what a reader does not see, Unicode's control and format characters, invisible or bidirectional. A subject or a preview stops at 1,000 characters, a name at 200, an address at 320, and a header gives 100 addresses at most. What TMail itself tells, ids, times and flags, stays outside.
- Reading never marks an email as read.
- Mail is published once `PUBLISHED_APPS` names `mail`, and then needs `MAIL_URL`: the service does not start without it.

### `mail.mailboxes.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `list_mailboxes` | `GET /contracts/v1/mail/mailboxes` | `{"mailboxes": [{"id", "name", "role", "parent_id", "total_emails", "unread_emails"}]}` |

- `role` names the special mailboxes as TMail does, such as `inbox`, `sent`, `drafts`, `archive`, `trash` and `spam`.

### `mail.emails.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `list_emails` | `GET /contracts/v1/mail/emails?mailbox=…&unread=…&flagged=…&from=…&after=…&before=…&limit=…&cursor=…` | `{"emails": [...], "next_cursor"}`, newest first |
| `search_emails` | `GET /contracts/v1/mail/search?text=…&mailbox=…&after=…&before=…&limit=…&cursor=…` | the same |
| `read_email` | `GET /contracts/v1/mail/emails/{email_id}` | the email, as text |

- Without `mailbox`, trash and spam are left out; with it, the mailbox must be one of the user's own.
- `unread=true` and `flagged=true` keep the unread and the flagged emails. `from` keeps the emails whose sender's address or name holds its text, and `text` the emails whose addresses, subject, body or attachments hold it, both from 2 to 200 characters. `after` and `before` are RFC 3339 times with their offset, `before` later than `after`.
- `limit` goes from 1 to 100 and is 20 by default. When `next_cursor` is not null, it reads the next emails with the same parameters, up to 500.
- A listed email gives its `id`, `thread_id`, `mailbox_ids`, `received_at`, `unread`, `flagged` and `has_attachment`, and `from`, `subject` and `preview` under `untrusted`.
- `read_email` adds `to`, `cc`, `reply_to` and `body` under `untrusted`, the body being the text TMail gives of the email, its HTML turned into text, cut after 32 KiB (`body_truncated`); `body_unreadable` tells that TMail could not decode it. `external_sender` tells that a From address is outside the user's domain, `reply_to_differs` that a reply would go to another address than the sender's, and `recipients_truncated` that `to` or `cc` had more than 100 addresses.
- A list or a search is one `Email/query`, with a single filter condition since James refuses mailboxes inside a filter operator, then `Email/get` by back-reference, which leaves out any email outside the user's own mailboxes.

### `mail.threads.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `read_thread` | `GET /contracts/v1/mail/threads/{thread_id}?limit=…` | `{"thread_id", "emails": [...]}`, oldest first |

- The last emails of the conversation, `limit` going from 1 to 20 and being 10 by default, each as `read_email` gives it, its text cut after 8 KiB.
- `Thread/get`, with `Email/get` of the mailboxes of all its emails by back-reference, so that the last emails are taken among those in the user's own mailboxes; then `Email/get` of their text only. A conversation that has none in them is not found.

### `mail.draft.create.v1`

| Operation | Request | Answer |
|---|---|---|
| `create_reply_draft` | `POST /contracts/v1/mail/emails/{email_id}/reply-draft` `{"text", "reply_all"}` | 201, `{"email_id", "draft_id", "reply_to_differs", "recipients_truncated", "untrusted": {"to", "cc", "subject"}}` |

- Prepares a reply to one of the user's own emails as a draft in their Drafts mailbox, which the user reviews and sends from Twake Mail: the contract never sends anything.
- The draft answers the email's Reply-To address, else its sender; with `reply_all`, also those the email went to, in To, and those it copied, in Cc. Never the user, by any address of their identities. As in Twake Mail, a reply to an email the user wrote goes to those they sent it to: the user wrote an email of their Sent mailbox, by role, from one of their addresses, and any other email is one they received, answered at its Reply-To address or else its sender, whatever its From says. The agent never chooses the recipients: the body takes `text` and `reply_all` only.
- It stays in the conversation: its `In-Reply-To` is the email's `Message-ID`, its `References` those of the email then the email itself, and its subject the email's, with `Re: ` before it unless it says it is a reply already.
- `text` is plain text of at most 20 KiB in UTF-8 (20480 bytes), and the draft holds nothing else: no quote of the email, no signature, no attachment. It is from the user's address, under the name of its identity, with the `$draft` and `$seen` keywords Twake Mail gives its drafts.
- `Mailbox/get`, `Email/get` and `Identity/get` in one request, then `Email/set` creates the draft. An email outside the user's own mailboxes answers 404 `email_not_found`, like an unknown one, and a user without a Drafts mailbox gets `mailbox_not_found`.
- `reply_to_differs` tells that the draft answers another address than the sender's. The recipients and the subject come back under `untrusted`, 100 addresses at most per header, `recipients_truncated` telling that the draft has more.
- It is a low-risk write (`x-twake-risk: low`): nothing leaves the mailbox until the user sends the draft.
- It tells what it would do ([Previews](#previews)): that the draft is never sent, whom it answers, its subject, that it goes to a Reply-To address rather than to the sender, when it does, and its text, whole when it fits. The digest covers the draft as it would be created, but for its text, which is the call's own.

### `mail.email.move.v1`

| Operation | Request | Answer |
|---|---|---|
| `move_email` | `POST /contracts/v1/mail/emails/{email_id}/move` `{"mailbox_id"}` or `{"mailbox_name"}` | `{"email_id", "mailbox_id", "mailbox_name"}`, the mailbox the email is now in |
| `archive_email` | `POST /contracts/v1/mail/emails/{email_id}/archive` | the same |
| `move_emails` | `POST /contracts/v1/mail/emails/move` `{"email_ids", "mailbox_id"}` or `{"email_ids", "mailbox_name"}` | `{"mailbox_id", "mailbox_name", "counts", "emails"}`, the mailbox and what became of each email ([several emails at once](#several-emails-at-once)) |
| `archive_emails` | `POST /contracts/v1/mail/emails/archive` `{"email_ids"}` | the same |

- `move_email` and `move_emails` take the mailbox by `mailbox_id`, its id as `list_mailboxes` gives it, or by `mailbox_name`, its name whatever its case, from 1 to 200 characters: exactly one of them, as the schema of their body says, so that the gateway refuses a body that names none or both. A name that several of the user's mailboxes have is refused (`mailbox_ambiguous`) rather than guessed.
- `archive_email` and `archive_emails` take the mailbox whose role is `archive`: without one, nothing is moved (`mailbox_not_found`), nor with several (`mailbox_ambiguous`).
- No move takes an email to drafts, sent, outbox, templates, trash or spam (`mailbox_forbidden`): the first four hold what the user writes and sends, `trash_email` and `trash_emails` put emails in the trash, and TMail reports an email moved into spam to the rspamd filter that all users share, which is for `report_spam`, a later high-risk contract.
- None takes an email out of spam (`email_in_spam`): TMail reports an email moved out of spam as ham to that shared filter, which is for `report_not_spam`, a later high-risk contract, not for a move. `trash_email` and `trash_emails` still can, since a move to the trash reports nothing.
- `Mailbox/get` and `Email/get` find the user's mailboxes, those the email is in, and its subject and senders, then `Email/set` patches its `mailboxIds`: the email leaves the user's other mailboxes, while a mailbox of someone else that is shared with the user keeps it.
- All four are low-risk writes (`x-twake-risk: low`): the emails can be moved back.
- `move_email` and `archive_email` tell what they would do ([Previews](#previews)): which email, by its subject and its senders, goes to which mailbox. The digest covers the email, the mailboxes it is in and the one it would go to: a call made once the email moved answers `changed_since_preview`.

### `mail.email.trash.v1`

| Operation | Request | Answer |
|---|---|---|
| `trash_email` | `POST /contracts/v1/mail/emails/{email_id}/trash` | `{"email_id", "mailbox_id", "mailbox_name"}`, the trash |
| `trash_emails` | `POST /contracts/v1/mail/emails/trash` `{"email_ids"}` | `{"mailbox_id", "mailbox_name", "counts", "emails"}`, the trash and what became of each email ([several emails at once](#several-emails-at-once)) |

- Moves the emails to the mailbox whose role is `trash`, from spam too, as `archive_email` and `archive_emails` do to the archive. It never destroys an email, which `move_email` or `move_emails` can move back.
- Without a trash, nothing is moved (`mailbox_not_found`), nor with several (`trash_ambiguous`): no move takes an email to a trash, so the user keeps a single one in Twake Mail.
- `trash_email` tells what it would do as `move_email` does ([Previews](#previews)), and that the email can be taken out of the trash.
- Both are low-risk writes (`x-twake-risk: low`).

### Several emails at once

`move_emails`, `archive_emails` and `trash_emails` move up to 50 emails in one call, where the contracts for one email would take a call per email, more than the harness lets a model make in one message. The description of each contract for one email sends the model to its batch for several emails.

- `email_ids` lists 1 to 50 ids, as `list_emails` or `search_emails` give them, and the body takes nothing else but the mailbox of `move_emails`. An id given twice is moved once.
- One request reads the user's mailboxes and all the emails (`Mailbox/get` and `Email/get`), then one `Email/set` moves those that have to: TMail takes 500 objects in each by default (`get.max.size` and `set.max.size` in its `jmap.properties`).
- Each email keeps the rules of the contracts for one email: only the user's own mailboxes, a mailbox shared with them keeping its copy, never destroyed, and out of spam to the trash only.
- What concerns all the emails refuses the whole call, and no email moves: a body the schema refuses, or a mailbox not found, ambiguous or forbidden, with the problem the contract for one email answers. A contract its detail names to go on with is the batched one, such as `move_emails` with a `mailbox_id`.
- Otherwise the call answers `200`, and `emails` tells what became of each email, in the order given: `{"email_id", "outcome", "code", "detail"}`, `outcome` being `moved`; `already_there`, for an email in that mailbox only, which is not written; `not_found`, for an email outside the user's own mailboxes, unknown, or gone before the write; or `refused`, with the `code` and the `detail` of a problem: `email_in_spam` for an email in spam that `move_emails` or `archive_emails` keeps there, `mail_unavailable` for one TMail did not move, whatever it answered. `code` and `detail` are null for the other outcomes, and `counts` gives how many emails had each. No email is left out, so that no failure is silent.
- A failure of the whole request, such as TMail answering an error to `Email/set` itself, answers `502`, as for one email: the call made again answers the emails that moved as `already_there`.
- Each tells what it would do ([Previews](#previews)): how many emails go to which mailbox, each on a line of its own, named by its subject and its senders as the preview of one email names it, ten at most and as many as fit in the summary, then how many others; then how many stay where they are, there already, outside the user's own mailboxes, or in spam. The digest covers each email, the user's own mailboxes it is in or that it is not found, and the mailbox they go to: a call made once one of them moved, or turned up, answers `changed_since_preview` and moves none.

### Drive, as the user

The Drive contracts act in the user's cozy-stack instance, which accepts only its own tokens. There is no `DRIVE_URL`: the gateway's route for a Drive contract asks the token broker for the user's Drive token, and passes three headers:

| Header | |
|---|---|
| `Authorization` | the user's access token, checked as on every contract: it gives the user |
| `X-Twake-Drive-Token` | an access token of the user's cozy-stack instance, which the broker holds for them |
| `X-Twake-Drive-Instance` | the host of that instance, from the user's `workplaceFqdn` in LemonLDAP-NG |

- The gateway removes these headers from what an agent sends, so only the broker sets them. The service reads them like the bearer token, out of the OpenAPI document, and never stores them.
- Drive is published once `PUBLISHED_APPS` names `drive`. The service then needs `DRIVE_INSTANCE_DOMAIN`, and does not start without it; while Drive is not published, it needs none of its settings.
- An instance is one name under `DRIVE_INSTANCE_DOMAIN`, such as `alice.<domain>`. Without an instance, or with any other host, such as an address or a host of another domain, a Drive contract answers `drive_instance_unknown`, and the Drive token goes nowhere. Without a Drive token, it answers `missing_drive_token`.
- The service calls the instance over HTTPS, with the Drive token as a bearer token: it must reach the users' instances. What is in the trash, or out of the token's reach, answers exactly like what does not exist.
- The contracts need no more of the Drive token than `io.cozy.files:GET,POST`: `GET` on all the user's files, to read them, their index of recent files and their links, and `POST`, to create a file. A token without `POST` finds every folder out of its reach: `create_file` answers `folder_not_found`.
- Names, paths, types and contents come in an `untrusted` object, apart from what the contract computed: the user wrote them, or anyone who shared a file with them, and the type of a file is the one its uploader declared. They come without control characters, but for the tabs and line breaks of a text, each line break a line feed: a carriage return alone would have a terminal write what follows it over the line.
- `web_url` opens the item in the Drive web app, for the user: on `<name>-drive.<domain>` when the stack serves its apps on flat subdomains, as its capabilities say, else on `drive.<instance>`.
- Lists hold 1 to 100 items, 20 by default. When `next_cursor` is not null, more follow: pass it as `cursor`.
- Its words in `x-twake-domains` say what reading and writing cover there.

### `drive.file.read.v1`

Reads the user's files and folders, as they see them in Drive.

| Operation | Request | Answer |
|---|---|---|
| `list_folder_items` | `GET /contracts/v1/drive/folders/{folder_id}/items?limit=…&cursor=…` | `{"folder", "items": [...], "next_cursor"}`, folders first, then by name |
| `read_file` | `GET /contracts/v1/drive/files/{file_id}` | the file or folder |
| `search_files` | `GET /contracts/v1/drive/files?name=…&kind=…&class=…&limit=…&cursor=…` | `{"items": [...], "next_cursor"}` |
| `list_recent_files` | `GET /contracts/v1/drive/recent-files?since=…&limit=…&cursor=…` | `{"items": [...], "next_cursor"}`, the most recent first |

- An item is `{"id", "type", "folder_id", "size", "created_at", "updated_at", "web_url", "untrusted": {"name", "path", "mime", "class"}}`, its times in UTC; the class of a file follows from its type. Nothing else of what the stack keeps comes back, such as checksums, the location of a photo or the links to thumbnails.
- `folder_id` is `root` for the top of the user's Drive. The trash is never listed.
- `search_files` finds the names that hold `name`, 1 to 100 characters, whatever their case, out of the trash. `kind` (`file` or `directory`) and `class` (of files, such as `text`, `pdf` or `image`) narrow it. CouchDB holds no index of names: a search reads all the user's files.
- `list_recent_files` gives the files changed since `since`, an RFC 3339 time with its offset, at most 31 days back and 7 by default, out of the trash and of the shared drives, as the recent view of Drive does. CouchDB sorts them along an index like the one that view makes: the first call adds it to the user's database (`POST /data/io.cozy.files/_index`), and is slower.
- The service lists a folder with `GET /files/{folder_id}` and `page[skip]`, `0` on the first page: without it, the stack pages with its own cursor, which carries the name of the next item, and that name must come under `untrusted` only. It reads an item with `POST /files/_all_docs`, which gives the path of a file, and searches with `POST /files/_find`, on a selector it builds, the text escaped. It reads `GET /settings/capabilities` once per instance, for `web_url`.

### `drive.content.read.v1`

Reads the text of one of the user's files, such as to summarise it: a text as it is, a document as text laid out the same whatever application wrote it.

| Operation | Request | Answer |
|---|---|---|
| `read_file_content` | `GET /contracts/v1/drive/contents/{file_id}?max_bytes=…` | `{"id", "size", "truncated", "untrusted": {"name", "mime", "content"}}` |

A text, `text/*`, JSON, XML or YAML, comes as it is: at most `max_bytes` bytes are read from the stack, 65,536 by default and 262,144 at most, and `truncated` tells that the file is longer. A character cut at the end is left out, and bytes that are not UTF-8 are replaced.

A document of one of these types, as its uploader declared it, comes as this text:

| Type | `content` |
|---|---|
| Word (`docx`), OpenDocument text (`odt`) | its paragraphs in order, its headings marked as in Markdown (`# Title`, `## Section`), from their outline level or their style, its list items after a dash, two spaces further for each list they are nested in, and its tables as rows of cells parted by tabs, between blank lines |
| PowerPoint (`pptx`), OpenDocument presentation (`odp`) | slide by slide, each under a heading that numbers it and gives its title, such as `# Slide 2: Roadmap`, and says `(hidden)` of a slide the slide show skips, then the text of its other shapes in their order, its tables as rows, and its speaker notes under `## Notes`; a slide without text by its heading alone |
| Excel (`xlsx`), OpenDocument spreadsheet (`ods`) | sheet by sheet, each under a heading that numbers it and gives its name, such as `# Sheet 1: Budget`, and says `(hidden)` of a hidden sheet, then a line for each row that holds values, its values parted by tabs from the first column: the values last computed, never the formulas, a number up to 15 digits, a date as `2026-10-01 14:30`, whatever language showed them. Only the first 1,000 rows that hold values and the first 50 columns of a sheet are read, as a line between brackets under its heading says |
| PDF | page by page, each under a heading such as `# Page 3`, the text of its text layer, as pypdf extracts it, a page without text, such as a scanned one, by its heading alone. Only the first 200 pages are read, as a first line between brackets says |

- What a reader does not see where it sits is left out: text deleted under tracked changes, field codes, the copies Office writes for older readers, footnotes, comments, the readings set above words (ruby), and what a slide's master fills in, such as its number. Headers, footers and charts are not read either.
- The text comes without Unicode's control and format characters, invisible or bidirectional, which a reader does not see either, but for its tabs and line feeds.
- `max_bytes` bounds the text that comes back, in UTF-8, a character cut at the end left out, and `truncated` tells that the document holds more: its text was cut there, or the reading stopped at one of its bounds. The reading process gives `max_bytes` characters at most: it cuts the line that goes beyond them, however long, and stops, so that a long document is read no further than asked. The service reads no more of its answer than those characters take once JSON escapes them, and 4 KiB besides, and cuts the text at `max_bytes` characters before cleaning it.
- A document is downloaded whole, 20 MiB at most, and read in a process of its own: an owner has one document read at a time, all owners two, and a request waits for its turn 10 seconds at most, past which it answers 503 `reading_busy`. The process gets the document on its standard input and nothing of the service's environment, and the service, marked as not dumpable, keeps its environment and its memory from it too: whatever a document crafted against a parser makes it do happens there, within 160 MiB of address space, past which an allocation fails and the document answers `content_not_extractable`. Once started, the process takes some 60 MiB, and up to some 80 MiB while it reads the longest texts: two processes and the service stay well within the 512 MiB of a pod.
- A zip's XML parts are parsed by Python's own zip and XML modules as they unpack, letting go of what each reader is done with, and refused if they declare a document type, where XML bombs and external entities hide, or nest their elements more than 500 deep. PDFs are read with pypdf, which runs no other program.
- The reading stops after 10 seconds, and gives the text it read, `truncated`, ending with a line between brackets that says why; the service stops a process that has not answered after 15 seconds, and the kernel one that ran 20 seconds on a processor.
- A document over 20 MiB answers 413 `file_too_large`, before any download when the stack's size says so, as does a document whose zip lists more than 10,000 files, unpacks into more than 256 MiB, or holds a file of more than 1 MiB compressed over 100 times, as a zip bomb does. A zip that compresses a file otherwise than stored or deflated, as Office and LibreOffice do, or of the ZIP64 format, for zips beyond 65,535 files or 4 GiB, is not unpacked: it answers `content_not_extractable`.
- A file encrypted on the user's devices answers 409 `file_encrypted`, as does a document protected by a password: Office keeps such a document in a compound file, LibreOffice says so in its manifest, and a PDF needs its password to open. A PDF its owner only restricts, such as from being printed, opens without one, and is read.
- A file of another type, such as a note, an image or an older Office document, answers 415 `content_not_extractable`, as does a document that is damaged or not of its type, a PDF that holds no text, as a scanned one, and a document that gave no text in the time or the memory its reading has: the detail says which, and never what the file holds.
- A file the antivirus found infected, or whose download it blocks, answers `file_blocked`.
- The service reads the file with `POST /files/_all_docs`, then its content with `GET /files/download/{file_id}`.

### `drive.file.create.v1`

Creates a text file in the user's own Drive, in a folder that nobody else sees.

| Operation | Request | Answer |
|---|---|---|
| `create_file` | `POST /contracts/v1/drive/files` with `{"folder_id", "name", "content", "mime"}` | 201, the new file, as `read_file` gives it |

- `mime` is `text/markdown` or `text/plain`, and the name ends with its extension, in lower case: `.md` or `.markdown`, or `.txt`. A name takes at most 255 characters, is neither a path nor a hidden file, and holds no control, invisible or direction-changing character, such as U+202E or U+200B: none of Unicode's Cc, Cf and Cs, as in the text others wrote, nor a line or paragraph separator. The pattern of `name` in the OpenAPI document, which the gateway checks, holds its form, and its description says the rest. The content takes at most 1 MiB once encoded in UTF-8. A field the contract does not take is refused.
- `folder_id` is `root` or a folder of the user's Drive, out of the trash, else `folder_not_found`, as for a folder out of the token's reach.
- The file appears in the user's Drive, and nobody else is notified. A folder shared with other people answers `folder_shared`, whether it is shared itself or lies in a shared folder: by a sharing the user sent or received, as a shared drive, or by a link that has not expired, whose holders read what the folder holds.
- The service tells a shared folder as the stack does: the stack references the sharing from the folder it shares, on the side of each member, until the sharing ends. The service reads the folder and each folder above it with `POST /files/_all_docs`, and the links with `GET /permissions/doctype/io.cozy.files/shared-by-link`, keeping only the ids each one shares.
- Two cases escape that check. The stack references only the root of a sharing's first files rule, and only logs a failure to write that reference: a folder that a sharing shares by another rule, or whose reference the stack failed to write, is not seen as shared. And the check then the write are not atomic: a folder shared between them takes the file.
- A name already in the folder, of a file or of a folder, answers `name_taken`: nothing is replaced, nor renamed. A Drive without room left for the file answers `quota_exceeded`.
- The service reads the instance's capabilities, for `web_url`, before the write, so that a failure there leaves no file behind: the call made again creates it. It writes the file with `POST /files/{folder_id}?Type=file&Name=…`, with its `Content-MD5`, which the stack checks on arrival, and never executable.
- It is a low-risk write (`x-twake-risk: low`): a new file in the user's own folders, never over another, which the owner's consent to write in Drive covers without a confirmation each time.
- It tells what it would do ([Previews](#previews)), once it checked the folder as it would: the file's name, its type and size, the folder it goes to, by its path, and its content, whole when it fits. The digest covers the folder, where it is: a call made once it moved answers `changed_since_preview`. Only the write finds a name taken in the folder, or a Drive without room: the call answers `name_taken` or `quota_exceeded` then.

### Tasks, as the user

The Tasks contracts call the REST API of Twake Tasks 0.1.1 with the user's token. Tasks accepts it once the token broker's client has the audience `twaketasks` and LemonLDAP-NG gives Tasks the user's `uuid`, `org_id` and `sid`: Tasks then acts for the user's `uuid` in their `org_id`, and shows them the boards of the projects they are a member of. Tasks also refuses the token unless the `sub` LemonLDAP-NG gives its own client, `twaketasks-backend`, which introspects the token, is the one userinfo gives for the token broker's client: both clients must take the same identifier attribute. The service publishes the Tasks contracts once `PUBLISHED_APPS` names `tasks`, and then needs `TASKS_URL`: without it, it refuses to start.

- A user whose token gives Tasks no `org_id` is a personal account for Tasks, which then shows them only what lies outside any organization: an organization's boards answer like unknown ones. Nothing in Tasks' answers tells the contracts which of the two the user is.
- Tasks 0.1.1 lists the user's boards only with `GET /api/boards`, which, as opening its web app does, creates the user's Inbox if they have none and accepts their pending invitations to projects. Listing boards is therefore an act, which a read never does: `open_boards` does it, as a write.

### `tasks.board.open.v1`

Opens Twake Tasks as the user, as its web app does when they open it, then lists their boards.

| Operation | Request | Answer |
|---|---|---|
| `open_boards` | `POST /contracts/v1/tasks/boards/open?include_archived=…` | `{"boards": [...], "truncated"}`, the user's Inbox first, then their favorite boards, then by name |

- The first time, Tasks sets up the user's Inbox; each time, it makes them a member of the projects they were invited to. It notifies nobody.
- It is a low-risk write (`x-twake-risk: low`): the user's own Inbox and the invitations made to them, which the owner's consent to write in Tasks covers without a confirmation each time. It takes no body.
- Archived boards are left out unless `include_archived=true`. The list holds 100 boards at most.
- Each board gives the user's `role` (`viewer`, `editor` or `admin`), whether it is their Inbox, its project's `project_id`, whether that project is a Twake Space's (`space`), and how many of its tasks are open. The names of boards and projects come under `untrusted`.
- It declares no preview ([Previews](#previews)): Tasks lists boards only by doing what opening does, so the contract cannot tell, without doing it, whether the user's Inbox would be set up or which invitations they would join. Its owner reads the call itself, which names no board.

### `tasks.task.read.v1`

Reads the user's tasks, on the boards of the projects they are a member of.

| Operation | Request | Answer |
|---|---|---|
| `list_my_tasks` | `GET /contracts/v1/tasks/mine?due=…&days=…&zone=…&limit=…` | `{"tasks": [...], "truncated"}`, by due date, undated ones last |
| `search_tasks` | `GET /contracts/v1/tasks/search?q=…&include_closed=…&limit=…` | `{"tasks": [...], "truncated"}` |
| `read_task` | `GET /contracts/v1/tasks/boards/{board_id}/tasks/{task_id}?comments=…` | the task, with its description and latest comments |

- `due=all`, the default, lists the open tasks assigned to the user (`GET /api/my-tasks`). `overdue`, `today` and `upcoming` read their agenda (`GET /api/agenda?zone=&days=`), which also counts the unassigned tasks of their own projects outside spaces, as `assigned_to_me` tells: the contract keeps the tasks due before today, today, or from today within `days` (1 to 31, 7 by default).
- `zone`, required, is the user's IANA time zone, such as `Europe/Paris`: due dates are days in it. A zone Tasks does not know is an invalid request.
- `search_tasks` finds the tasks whose key starts with `q`, or whose title or description holds it (`GET /api/search?q=`, 1 to 200 characters), and keeps the closed ones only with `include_closed=true`.
- `assigned_to_me` follows what Tasks says, never an email: every task of `due=all` is assigned to the user, and in the agenda a task with assignees is assigned to them while one without is one of their own unassigned tasks. A search holds anyone's tasks, and Tasks does not say which of their assignees is the user: it gives `false` for a task without assignees and `null` otherwise.
- Lists hold 20 tasks by default, 100 at most, and 50 for a search, the most Tasks gives. Tasks gives no cursor: `truncated` says when a list holds less than all.
- `read_task` reads the whole board (`GET /api/boards/{board_id}`), then the task's description and comments. The description is cut at 10,000 characters, and each of the latest `comments` comments (0 to 50, 10 by default) at 2,000.
- A board the user is not a member of answers exactly like an unknown one, and an archived or trashed task like a missing one.
- The user on a board is the member who joined with their email, whatever its case. `read_task` needs them only to tell whether a task with assignees is the user's, and is refused rather than guessing when no member has that email, or more than one, as when the user joined under an alias of their address.
- Ids are the UUIDs that reads give; a key, such as `WEB-12`, names a task for people only.
- Titles, descriptions, comments, and the names of boards, projects, sections and labels are written by members: they come under `untrusted`, apart from what the contract computed.

### `tasks.task.create.v1`, `tasks.task.update.v1` and `tasks.task.complete.v1`

Create, change and complete tasks as the user, on the boards they may edit.

| Operation | Request | Answer |
|---|---|---|
| `create_task` | `POST /contracts/v1/tasks/boards/{board_id}/tasks` `{"title", "section_id", "parent_id", "priority", "due_date", "due_time", "due_zone"}` | 201, the new task |
| `update_task` | `PATCH /contracts/v1/tasks/boards/{board_id}/tasks/{task_id}` `{"title", "priority", "due_date", "due_time", "due_zone", "deadline"}` | the task |
| `complete_task` | `POST /contracts/v1/tasks/boards/{board_id}/tasks/{task_id}/complete` | the task, with its `next_due_date` |

- A board's `board_id` comes from `open_boards`, where the user's Inbox has `inbox: true`, or from a task the reads give.
- Each write reads the board first (`GET /api/boards/{board_id}`), and writes nothing on a board the user is not a member of, which answers like an unknown one, nor on one where no member, or more than one, joined with their email, the rule `read_task` follows (`owner_not_member`), that they only view (`forbidden_role`) or that is archived (`board_archived`). A task the board does not show, archived or in the trash, is not found.
- The answer is the task as Tasks then shows it, read again from the board: what `read_task` gives but its description and comments.
- `create_task` puts the task at the end of the section given, else of the board's first `unstarted` section, or outside sections on a board without any, such as the Inbox. On a board with sections but none `unstarted`, `section_required` lists them, each with its `section_id`, its `category` and its name under `untrusted`. Every task the reads give comes with its `section_id` too. `parent_id` makes the task a subtask, outside sections, of a task the board shows.
- Tasks creates a task from its title alone (`POST /api/boards/{board_id}/tasks`): the contract then sets its priority and due date (`PATCH`), and answers `task_created_partially`, with the task's `board_id`, `task_id` and `key`, when that fails. A task created in full that cannot be read again answers `tasks_unavailable`. Tasks takes no idempotency key, so no write is ever replayed.
- `update_task` changes the fields given and no other, `null` clearing one but the title, with `PATCH /api/boards/{board_id}/tasks/{task_id}`: the last write wins. Clearing `due_date` clears its time, zone and recurrence too. No contract replaces a description, which could not be undone.
- `complete_task` completes a task outside sections with `POST …/complete` `{"state": "completed"}`, and one in a section by its move to the board's first `completed` section (`POST …/move`), as the Tasks web app does: without one, `no_completed_section`. The open subtasks complete with their parent. A recurring task is completed for its due date and stays open, moved to the next one, which `next_due_date` gives, null for any other task. A completed task is answered as it is, and nothing is written.
- A title takes 500 characters at most, and a priority goes from 1 to 4. Due dates and deadlines are days (`2026-10-09`); a due time, `HH:MM`, needs a due date, and its zone a due time. The zone must be in the IANA time zone database, as Tasks requires, which the `tzdata` package completes: an unknown one is an invalid request, before anything is written.
- Tasks notifies nobody of a new task, which the user follows. It notifies the other people who follow a task of each change and of its completion, in Tasks and by email: by default its creator, its assignees and those who commented on it.
- Each is a low-risk write (`x-twake-risk: low`): the user's own work, which the owner's consent to write in Tasks covers without a confirmation each time.
- Each tells what it would do ([Previews](#previews)), once it read the board as it would. `create_task` tells the task's title, its board and its section, or the task it goes under, and its priority and due date. `update_task` tells each field it changes, as it would be and as it was, a due date with its time and zone, and that clearing it clears its recurrence. `complete_task` tells the section the task moves to and how many open subtasks complete with it, or, for a recurring task, the due date it is completed for, or that a completed task stays as it is. The digest covers where a new task goes, its board, section and parent, and for a change or a completion the task as it is, with the section a completion moves it to and the subtasks it takes along: a call made once a member changed them answers `changed_since_preview`.

### Contacts, as the user

Twake Contacts keeps the user's address books in esn-sabre, which the contracts reach as Calendar does: through the Calendar side service (`CALENDAR_URL`), with the user's token. They use its `/dav` proxy, which acts in esn-sabre as the token's user, in esn-sabre's JSON dialect of CardDAV, and its search across several address books (`POST /contacts/api/contacts/search`). Contacts has no setting of its own: it is published once `PUBLISHED_APPS` names `contacts`.

The reads cover every address book the user reads in the Contacts web app: their own, their organization's directory, and the books other people share with them or that they subscribed to. The words of Contacts for reading name them, in `x-twake-domains`: "list, search and read your contacts, your organization's directory and the address books shared with you". Its writes cover the user's own books alone.

- The user's email gives their id and their domain's (`GET /api/users?email=`), which name their home of address books in esn-sabre and their domain's.
- The address books the user reads are those of their home (`GET /dav/addressbooks/{user id}.json?personal=true&shared=true&subscribed=true&inviteStatus=2&contactsCount=true`): their own, `contacts`, the default one, which esn-sabre gives every user, `collected`, where Contacts collects addresses, and those they created; the delegations they accepted and their subscriptions, which show someone else's book; then their domain's (`GET /dav/addressbooks/{domain id}.json?personal=true&contactsCount=true`), such as `domain-members`, its directory of members. An address book of someone else, which they did not share with the user, answers exactly like an unknown one: the contracts take no other address book.
- An address book's `book_id` is its home and its name, as `{home}~{name}`. A contact's `contact_id` is opaque: the contracts make it of the name of its card in the book, which whoever wrote the card chose, in base64url, so that it carries no text a reader would take for words, and take no other. A book whose name holds other characters than letters, digits, `.`, `_`, `~` and `-`, and a book or a card whose name holds `.json`, which esn-sabre removes from wherever a URL holds it, are left out: neither the search of Contacts nor a path of the contracts can name them.
- The contracts write in the user's own address books alone, those Contacts lets them write in, which `writable` tells. A contact of a book someone else shares with the user, of one they subscribed to, or of their domain's, is refused with `address_book_read_only`, whatever rights Contacts gives the user there: only its owner changes it.
- Cards come and go in jCard, the JSON of vCard: `GET` with `Accept: application/vcard+json`, which esn-sabre answers in vCard 4.0, and `PUT` in the same form. What people wrote in a contact comes under `untrusted`, on one line but the note, without Unicode's control and format characters: a name, an organization, a job title or a part of an address cut at 200 characters, an email at 320, a phone at 100, a note at 10,000, and 20 emails, phones or addresses at most, which `truncated` tells. Nothing else of a card comes back, such as its photo or its categories.
- The job title is the card's `ROLE`, where the Contacts web app writes and shows it, else its `TITLE`.
- The side service forwards neither `If-Match` nor `If-None-Match`, so no write can be conditional: each write reads the card, checks it against the digest of the preview its owner was shown, if any, then writes over it, or deletes it, at once. That keeps the time between the check and the write short, but not nil: a change made by someone else in that time would be lost.
- A write tells nobody: esn-sabre publishes it inside the platform, so that the apps the user has open show it.

### `contacts.addressbooks.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `list_address_books` | `GET /contracts/v1/contacts/address-books` | `{"address_books": [{"book_id", "kind", "default", "writable", "contact_count", "untrusted": {"name", "description"}}]}` |

- `kind` is `personal`, one of the user's own; `collected`, theirs too, where Contacts collects addresses; `shared`, someone else's, which they share with the user or the user subscribed to; `domain`, one of the user's domain. The user's own come first, the default one at the top, then the shared ones, then their domain's.
- `default` marks the book `create_contact` adds contacts to. `contact_count` is how many contacts a book holds, which esn-sabre counts in the user's own books and their domain's only.

### `contacts.contacts.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `search_contacts` | `GET /contracts/v1/contacts/search?q=…&limit=…` | `{"contacts": [{"book_id", "contact_id", "kind", "untrusted": {"name", "emails", "phones", "organization"}}], "truncated"}`, by name |
| `read_contact` | `GET /contracts/v1/contacts/address-books/{book_id}/contacts/{contact_id}` | `{"book_id", "contact_id", "kind", "writable", "truncated", "untrusted": {"name", "given_name", "family_name", "nickname", "emails": [{"address", "type"}], "phones": [{"number", "type"}], "organization", "title", "addresses": [{"type", "street", "locality", "region", "postal_code", "country"}], "note", "birthday"}}` |

- `search_contacts` finds the contacts whose text holds `q`, 2 to 100 characters, whatever its case, in all the address books `list_address_books` gives. The search of Contacts matches its query as a pattern against the vCard text of each card: the contract sends `q` escaped, its commas, semicolons and backslashes as vCard escapes them, then every other sign but letters and digits by its code, so that it is found as written, never read as a pattern, and that no `.json` reaches the URL esn-sabre reads it in.
- Contacts finds 200 contacts at most, all books together, in each book by the names of their cards. Of those, the contract keeps the ones whose fields hold `q`, rather than the names vCard writes them under, such as `TEL`, and sorts them by name. `limit` goes from 1 to 100 and is 20 by default; `truncated` tells that more contacts may hold `q`. Contacts keeps the text of a card in lines folded after 75 bytes: words a card holds across two of them, as in a long note, are not found.
- A `type` is `work`, `home` or `other` for an email, `cell`, `work`, `home`, `fax` or `other` for a phone, and `home`, `work` or `other` for an address, when the card says it.

### `contacts.contact.create.v1`, `contacts.contact.update.v1` and `contacts.contact.delete.v1`

Create, change and delete contacts in the user's own address books, as the user.

| Operation | Request | Answer |
|---|---|---|
| `create_contact` | `POST /contracts/v1/contacts/contacts` `{"name", "given_name", "family_name", "nickname", "emails", "phones", "organization", "title", "addresses", "note", "birthday"}` | 201, the contact, as `read_contact` gives it; 200 for the contact the same call added already |
| `update_contact` | `PATCH /contracts/v1/contacts/address-books/{book_id}/contacts/{contact_id}` with the fields to change | the contact, and under `untrusted.previous` the fields it changed, as they were |
| `delete_contact` | `DELETE /contracts/v1/contacts/address-books/{book_id}/contacts/{contact_id}` | the contact as it was |

- `emails` are `{"address", "type"}`, `phones` `{"number", "type"}`, and `addresses` `{"type", "street", "locality", "region", "postal_code", "country"}`. A text takes 200 characters at most and a note 10,000, and a contact 10 emails, 10 phones and 5 addresses at most. An email must be an address (`invalid_email`), and a phone 3 to 20 digits, maybe after a `+`, with spaces, dots, dashes, slashes or parentheses (`invalid_phone`); `birthday` is a day, such as `1980-05-17`. Text is written on one line but the note, without what a reader does not see. The body takes no other field.
- `name` is the name Contacts shows. Without it, the contract makes the one the Contacts web app makes: the given and family names, else the organization, the job title, the nickname, the first email or the first phone. A contact with none of them is refused.
- `create_contact` adds the contact to the user's default address book, `contacts`, and to no other. It writes a vCard 4.0 as the Contacts web app writes one, the job title in `ROLE`. Its UID, which names its card too, comes from the user and the fields given, as a UUID v5: the contract looks for it before writing, so that the same call made again answers 200 with the contact it added, and `contact_exists` when the user changed it since. A contact of the default book with one of the emails, whatever their case, answers `contact_exists` too, with its `book_id` and `contact_id`, and nothing is added: `update_contact` changes it. Contacts keeps the text of a card in lines folded after 75 bytes, which its search reads: the contract searches pieces of each email, one more than the folds the email can cross, so that a fold leaves one of them whole, then checks the emails of each contact found, and reads every contact of the book, page by page, when the search finds more than it gives. The contact is read back after the write, also when the side service did not confirm it in time.
- `update_contact` changes the fields given and no other, `null` clearing one; a list given replaces the whole list. All else the card holds stays where it is, such as its photo, its categories or the units of its organization. The name Contacts shows follows what it is made of, unless it was set apart from it: `name` sets it apart, and `null` makes it follow again. A contact left with nothing to show it by is refused. The job title goes to `ROLE`, and to `TITLE` when the card holds one, so that no app shows the former one. A change that changes nothing writes nothing. What a change clears, or leaves out of a list, is erased: the answer gives back under `untrusted.previous` each field the change changed, the name Contacts shows too, as it was, so that it can be put back.
- `delete_contact` deletes the card for good: esn-sabre keeps no trash. It answers the contact as it was, which `create_contact` can add again.
- A card that the write would make larger than the 1 MiB Contacts takes in a request, such as one holding a large photo, answers `contact_too_large`, before the owner is asked.
- `create_contact` and `update_contact` are low-risk writes (`x-twake-risk: low`): the user's own contacts, which nobody is told of, and which the owner's consent to write in Contacts covers without a confirmation each time. `delete_contact` is a high-risk write (`x-twake-risk: high`), which the owner confirms call by call: a contact deleted is lost.
- Each tells what it would do ([Previews](#previews)). `create_contact` tells each field the contact holds, its note whole when it fits, or that the contact is in the address book already; `update_contact`, each field it changes, the name Contacts shows too, as it would be and as it was, what it clears and the entries a list loses named as removed; `delete_contact`, that the contact goes for good, and each field it holds. The digest covers the contact as the address book holds it, for `create_contact` by its UID, if at all: a call made once it changed answers `changed_since_preview`, and writes nothing.

### Space, as the user

The Space contracts call the REST API of the Twake Space backend 0.1.9 with the user's token, at `SPACE_URL`, under which it serves `/spaces` and `/organization/members`. Space introspects the token with its own client, `twakespace-backend` by default, and reads userinfo with the token itself. It accepts the token once the token broker's client has the audience `twakespace` and userinfo gives the user's `sub`, `uuid`, `email` and `sid`: LemonLDAP-NG gives `sub` and, from 2.22, `sid` by itself, and its client exports the others. Space then acts for the user's `uuid`, which must be a UUID, in their `org_id`, and shows them the spaces they are a member of. As Tasks does, it also refuses the token unless the `sub` of its introspection, which LemonLDAP-NG gives for its own client, is the one userinfo gives for the token broker's client: both clients must take the same identifier attribute. The service publishes the Space contracts once `PUBLISHED_APPS` names `space`, and then needs `SPACE_URL`: without it, it refuses to start.

- A token Space refuses, for any of those reasons, answers `space_refused`. So does a user whose token gives Space no `org_id`, whom it refuses on every route, as it serves the members of an organization only.
- A space the user is not a member of answers exactly like an unknown one, and an item outside the feed of the space like a missing one.
- Space does not say which member the user is: the contracts take the member who has the user's email, whatever its case, as the user, to tell their own posts and reactions, which `you` and `mine` mark, and to check a post is theirs before it is changed. With no such member, or several, none is marked, and Space alone refuses to change someone else's post.
- What people wrote comes under `untrusted`, on one line but a post, which keeps its lines, all without what a reader does not see: the names of spaces, groups, people and tokens, descriptions, posts, reactions, and the titles, previews and ids of the objects of the cards. What an app tells of an object, such as an event's times or where it takes place, comes under `untrusted` too, each of its texts so cleaned and cut at 500 characters, 20 entries of each list or object and three levels at most.
- `role` is the user's in a space, or a member's: `viewer`, who reads and reacts, `editor`, who posts too, or `admin`, who also adds, changes and removes members.
- Its words in `x-twake-domains` say what reading and writing cover there: reactions and posts, which the members of a space see, and its members, where the user is an admin.

### `space.spaces.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `list_spaces` | `GET /contracts/v1/space/spaces` | `{"spaces": [{"space_id", "role", "member_count", "untrusted": {"name", "description"}}], "truncated"}`, by name |
| `read_space` | `GET /contracts/v1/space/spaces/{space_id}` | `{"space_id", "role", "created_at", "apps", "tasks_project_id", "chat_room_id", "mailbox_id", "calendar_id", "drive_id", "members": [{"user_id", "username", "email", "role", "you", "untrusted": {"display_name"}}], "groups": [{"group_id", "role", "untrusted": {"name"}}], "untrusted": {"name", "description"}}` |

- The list holds 100 spaces at most, `truncated` telling that the user has more.
- `read_space` gives the members by username, the groups linked to the space, whose people are members with their role, and the tabs the space shows (`apps`). Each of its apps links one of its own to the space: its project in Tasks, which `open_boards` gives as `project_id`, its room in Chat, as the chat contracts take it, its shared mailbox, its calendar and its files; null while the app prepares it, or when the deployment has no such app.
- `GET /spaces`, then `GET /spaces/{id}`.

### `space.people.read.v1`

| Operation | Request | Answer |
|---|---|---|
| `search_organization_people` | `GET /contracts/v1/space/people?q=…&page=…` | `{"people": [{"username", "email", "untrusted": {"display_name"}}], "next_page"}` |

- The active people of the user's organization, as Space's directory, ldap-rest, finds them: those whose username, email or name holds `q`, 2 to 100 characters once the blanks and what a reader does not see are left out, or all of them without `q`. 20 a page, `next_page` the `page` of those who follow, null after the last.
- `username` is what `add_space_members` takes. `GET /organization/members?search=&page=`.

### `space.feed.read.v1`

The feed of a space, its Fil, shows a card per object of the space's apps, such as a file, an event, a task or an email, and the posts its members write, each with the reactions of its members.

| Operation | Request | Answer |
|---|---|---|
| `list_feed_items` | `GET /contracts/v1/space/spaces/{space_id}/feed?category=…&limit=…&before=…` | `{"items": [...], "next"}`, newest first |
| `read_feed_item` | `GET /contracts/v1/space/spaces/{space_id}/feed/items/{item_id}` | the item |

- An item is `{"item_id", "kind", "category", "time", "updated_at", "edited_at", "event_type", "object", "by", "reactions", "untrusted": {"text", "title", "preview", "object_id", "state"}}`. A card shows the latest activity of an app on one object, and keeps the place of its first: `event_type` is the type of its latest event, such as `com.twake.drive.file.updated.v1`, `object` `{"type", "container_kind", "container_id"}` what it is and what of the space's apps it lies in, and its title, preview, id and what its app tells of it come under `untrusted`. A post gives its `text` under `untrusted`, and when it was last edited.
- `by` is `{"kind", "user_id", "you", "untrusted": {"name"}}`: a `user`, with their `user_id` as `read_space` gives it, null for someone outside the space, a `token` of Space an application acts with, or a `deleted_user`; null for an activity no one in particular made. `reactions` are `{"count", "mine", "untrusted": {"key"}}`, in the order they were first added.
- `category` keeps `messages`, the posts and the mail, `files`, `activities`, such as tasks, or `events`, of the calendar. `limit` goes from 1 to 50 and is 20 by default; `next` is the `before` of the older items, null after the last, and a cursor Space did not write is an invalid request.
- Each reads the space first (`GET /spaces/{id}`), for the member who is the user, then `GET /spaces/{id}/feed` or `GET /spaces/{id}/feed/items/{item_id}`.

### `space.reaction.add.v1` and `space.reaction.remove.v1`

React to an item of the feed of one of the user's spaces, and take a reaction back.

| Operation | Request | Answer |
|---|---|---|
| `add_feed_reaction` | `POST /contracts/v1/space/spaces/{space_id}/feed/items/{item_id}/reactions` `{"key"}` | the item, as `read_feed_item` gives it, read again |
| `remove_feed_reaction` | `POST /contracts/v1/space/spaces/{space_id}/feed/items/{item_id}/reactions/remove` `{"key"}` | the same |

- `key` is one of the six reactions the Space web app offers, 👍, ❤️, 😂, 🎉, 👀 and 🙏: an enum of the body, which the gateway checks, so that the model never writes words that members would read. The body takes no other field.
- Any member reacts, viewers too, and the members of the space see it. Only the user's own reaction is taken back. A reaction the user made already writes nothing, and so does taking back one they did not make.
- `PUT` and `DELETE /spaces/{id}/feed/items/{item_id}/reactions/{key}`, the key encoded in the path. The contracts take it in the body, where the gateway checks it whole.
- Both are low-risk writes (`x-twake-risk: low`): the user's own reactions, which they take back at will, and which the owner's consent to write in Space covers without a confirmation each time.
- Each tells what it would do ([Previews](#previews)): which item gets or loses which reaction, in which space, with the text of a post, or that nothing changes. The digest covers the item as shown, a card by its title, a post by its author and its text, and whether the user reacted so: a call made once the post was rewritten answers `changed_since_preview`.

### `space.post.create.v1`, `space.post.update.v1` and `space.post.delete.v1`

Post in the feed of one of the user's spaces, and edit and delete the user's own posts.

| Operation | Request | Answer |
|---|---|---|
| `create_feed_post` | `POST /contracts/v1/space/spaces/{space_id}/feed/posts` `{"text"}` | 201, the post, as `read_feed_item` gives it |
| `update_feed_post` | `PATCH /contracts/v1/space/spaces/{space_id}/feed/posts/{item_id}` `{"text"}` | the post |
| `delete_feed_post` | `DELETE /contracts/v1/space/spaces/{space_id}/feed/posts/{item_id}` | the post as it was |

- `text` is plain text of 1 to 4000 characters once the blanks around it are left out, as Space keeps it; the body takes no other field. The same text as the post's writes nothing.
- Every member of the space sees a post in its feed, and its changes, as they happen: Space sends no notification of them.
- Editors and admins post: a viewer's post answers `forbidden_role`. Only its author edits or deletes a post, whatever their role now: someone else's answers `not_author`, and a card, which shows what an app did, `not_a_post`. All three are refused before anything is written, and Space refusing the same once the user's role changed answers them too.
- Each call to `create_feed_post` posts anew: Space takes no idempotency key. `delete_feed_post` deletes the post for good, with its reactions: Space keeps no trash.
- `POST /spaces/{id}/feed/posts`, `PATCH` and `DELETE /spaces/{id}/feed/posts/{item_id}`, each after `GET /spaces/{id}`, and a change or a deletion after `GET /spaces/{id}/feed/items/{item_id}`.
- All three are high-risk writes (`x-twake-risk: high`), which the owner confirms call by call: every member reads a post.
- Each tells what it would do ([Previews](#previews)): where a post goes and how many members see it, the new text and the former one, or the post that goes for good, each text whole when it fits. The digest covers the space and its members for a new post, the post as it is and who reads it for a change, and the post as it is for a deletion: a call made once a member joined, or the post was rewritten, answers `changed_since_preview`, and writes nothing.

### `space.member.add.v1`, `space.member.update.v1` and `space.member.remove.v1`

Add people of the user's organization to one of their spaces, change the role of its members, and remove them, where the user is an admin.

| Operation | Request | Answer |
|---|---|---|
| `add_space_members` | `POST /contracts/v1/space/spaces/{space_id}/members` `{"usernames", "role"}` | the space, as `read_space` gives it, read again |
| `update_space_member` | `PATCH /contracts/v1/space/spaces/{space_id}/members/{user_id}` `{"role"}` | the member, as `read_space` gives them |
| `remove_space_member` | `DELETE /contracts/v1/space/spaces/{space_id}/members/{user_id}` | the member as they were |

- Only the admins of a space change its members: anyone else is refused with `not_space_admin`, before anything is written, and when Space refuses it once the user's role changed.
- `usernames` lists 1 to 20 usernames, as `search_organization_people` gives them, all added with one `role`. Each is looked for in the directory first (`GET /organization/members?search=`), among the first 100 people it finds, whatever its case: those the user's organization does not have answer `person_not_found`, which names them, and nobody is added. A member of another role answers `member_exists`, as ldap-rest would: `update_space_member` changes it. Members of that role already are left as they are, and nothing is written when all are.
- The people added see the space, its feed and what its apps hold, such as its room, its tasks and its files: Space writes the change in its directory, ldap-rest, which tells the apps.
- `user_id` is a member's, as `read_space` gives it: one the space does not have answers `member_not_found`. A member given the role they have is left as is. A change or a removal that would leave the space without an admin, as ldap-rest refuses it, answers `last_admin`. A member through a linked group only stays one while the group is linked.
- `POST /spaces/{id}/members` `{"usernames", "role"}`, `PATCH` `{"role"}` and `DELETE /spaces/{id}/members/{user_id}`, each after `GET /spaces/{id}`.
- All three are high-risk writes (`x-twake-risk: high`), which the owner confirms call by call: they change who sees what a space holds, and who manages it.
- Each tells what it would do ([Previews](#previews)): whom the space takes in and as what, each by their name and their email, and who is a member already; a member's new role and their former one, and what an admin does; or who leaves the space, the user maybe. The digest covers the people and their membership: a call made once one of them joined, changed role or left answers `changed_since_preview`, and writes nothing.

## Previews

When the harness asks an owner about a write, for a first use, a high-risk write or a write that a turn an event started prepared, it shows them what the call would do rather than the call as the model wrote it, if the write's operation declares `x-twake-preview: true`. It first calls the contract as the call would go, same method, path, query and body, in the owner's name, with `x-twake-preview: true` and the owner's language in `accept-language`:

- The contract checks the call as it would, reads what the call acts on, and writes nothing. It answers `200`, whatever the call itself answers, such as the `201` of a creation, with `x-twake-preview: true` in its headers and `{"summary", "digest"}`, and a call it would refuse before writing with the same problem. What only the write finds, such as an application refusing it, the call itself answers.
- `summary` tells the owner what the call would do, in plain text on a few lines, in French or in English: the first of the two that `accept-language` prefers, English by default. Text that others wrote, such as a title, a name or an address, comes on one line and cut short, without the characters a reader does not see, which the harness refuses in a summary: control characters but line feeds and tabs, and format characters. A title or a name comes between quotation marks, its own quotation marks made plain apostrophes, and an address between angle brackets, without any of its own: none of it can close its quotes and go on as the summary's own words. The harness shows the summary quoted under its own label, as data, never rendered.
- A summary takes at most three quarters of what the harness shows, 16 KiB as it counts them: its bytes in UTF-8 and those of its HTML, which escapes `&`, `<` and `>`. The text a write would put, a reply's or a file's, comes line by line, each line after a tab, so that none passes for the summary's own, and whole unless it takes more than the rest of the summary leaves: the summary then shows its beginning, and a line of its own says how many characters it leaves out. The people of a header, its recipients or its senders, come ten at most, as many as fit in a sixth of the summary, then how many others: a reply to all of a crafted email still leaves room for the rest. A summary that would take more, which no contract writes, is cut, and says so.
- `digest` is `sha256:` and the SHA-256, in hex, of what the call acts on, written in JSON, its keys and lists in an order of their own, such as subtasks by id, so that every replica finds the same digest for the same thing. The harness keeps it with the call it froze, and the call its owner allows carries it in `x-twake-preview-digest`: a contract that finds what the call acts on changed since answers `409` `changed_since_preview` and does nothing, and the harness tells the owner that the action was not done.
- `x-twake-preview` with any other value than `true` is refused (`invalid_request`), rather than taken for the call itself.
- None of these headers is in the OpenAPI document: they are the harness's, never a model's.

| Operation | The summary tells | The digest covers |
|---|---|---|
| `accept_invitation` | the event's title, when it takes place, in the user's time zone, and who organizes it | the event as the user would accept it |
| `create_reply_draft` | whom the draft answers, its subject and its text, never sent | the draft as it would be created, but for its text |
| `move_email`, `archive_email`, `trash_email` | which email, by its subject and senders, goes to which mailbox | the email, where it is, and where it would go |
| `move_emails`, `archive_emails`, `trash_emails` | how many emails go to which mailbox, ten of them at most by their subject and senders, and how many stay where they are, and why | each email, where it is or that it is not found, and where they would go |
| `create_task` | the task's title, where it goes and when it is due | its board, its section or the task it goes under |
| `update_task` | each field it changes, as it would be and as it was | the task as it is |
| `complete_task` | where the task goes, or the due date it moves on from, and the subtasks it completes | the task as it is, its completed section and its open subtasks |
| `create_file` | the file's name, type and size, its folder and its content | the folder, where it is |
| `create_event` | the event's title, when it takes place, in the user's time zone, whether it leaves them free, where it is and what it is for; or that it is in their calendar already | the event, by its UID, the zone it is written in, and the event as the calendar holds it, if at all |
| `create_contact` | each field of the contact, its note whole when it fits; or that it is in the address book already | the contact, by its UID, as the address book holds it, if at all |
| `update_contact` | each field it changes, as it would be and as it was, what it removes named as such | the contact as it is |
| `delete_contact` | that the contact goes for good, and each field it holds | the contact as it is |
| `add_feed_reaction`, `remove_feed_reaction` | which item gets or loses which reaction, in which space, with a post's text; or that nothing changes | the item as shown, and whether the user reacted so |
| `create_feed_post` | the space the post goes to, how many members see it, and its text | the space and its members |
| `update_feed_post` | the post's new text and its former one | the post as it is, and who reads it |
| `delete_feed_post` | that the post goes for good with its reactions, and its text | the post as it is |
| `add_space_members` | whom the space takes in and as what, and who is a member already | the people, and who of them are members already |
| `update_space_member` | the member's new role and their former one, and what an admin does | the member as they are |
| `remove_space_member` | who leaves the space, the user maybe | the member as they are |

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem (`application/problem+json`) with a stable `code`:

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_request` | a parameter, or a field of the body, is invalid |
| 400 | `invalid_email` | an email the call gives is not an address |
| 400 | `invalid_phone` | a phone the call gives is not a number of 3 to 20 digits |
| 401 | `missing_token` | no bearer token |
| 401 | `invalid_token` | the token is not one the broker got for this service: another type, issuer, audience, client or key, or expired |
| 401 | `missing_drive_token` | a Drive contract without the user's Drive token |
| 403 | `forbidden_role` | the user is a viewer of the board, where they only read, or of the space, where they read and react but do not post |
| 403 | `not_space_admin` | the user is not an admin of the space, whose admins alone change its members |
| 403 | `not_author` | someone else wrote the post, which only its author edits or deletes |
| 403 | `address_book_read_only` | the address book is someone else's, shared with the user, their domain's, or one Contacts lets them only read: no contract writes in it |
| 404 | `invitation_not_found` | no invitation to an event of this UID was sent to the user: their calendars have no copy of the event, their copy does not list them as an attendee, or they organize it |
| 404 | `calendar_user_not_found` | Calendar has no user with the user's email |
| 404 | `chat_account_not_found` | Chat has no account for the user's email |
| 404 | `room_not_found` | the user has joined no room with this id |
| 404 | `mailbox_not_found` | the user has no mailbox of their own with this id or name, none with the role archive or trash, or no Drafts mailbox for a draft |
| 404 | `email_not_found` | the user has no email with this id in their own mailboxes; one of several emails moved at once is answered `not_found` instead |
| 404 | `thread_not_found` | the user has no conversation with this id in their own mailboxes |
| 404 | `drive_instance_unknown` | no Drive instance of the platform is known for the user: LemonLDAP-NG gives no `workplaceFqdn` for them, or one outside `DRIVE_INSTANCE_DOMAIN` |
| 404 | `folder_not_found` | no folder with this id in the user's Drive, out of the trash |
| 404 | `file_not_found` | no file with this id in the user's Drive, out of the trash |
| 404 | `board_not_found` | the user is a member of no board with this id |
| 404 | `task_not_found` | the board shows no task with this id: it may be archived or in the trash |
| 404 | `contacts_user_not_found` | Contacts has no user with the user's email |
| 404 | `address_book_not_found` | the user reads no address book with this id: neither their own, nor one shared with them, nor their domain's |
| 404 | `contact_not_found` | the address book has no contact with this id |
| 404 | `space_not_found` | the user is a member of no space with this id |
| 404 | `feed_item_not_found` | the feed of the space has no item with this id |
| 404 | `member_not_found` | the space has no member with this user id |
| 404 | `person_not_found` | the user's organization has no active person with these usernames: `usernames` lists them, and nobody was added |
| 409 | `changed_since_preview` | what the call acts on changed since its owner was shown what it would do: nothing was done |
| 409 | `recurring_invitation` | the invitation repeats, or is one occurrence of a series |
| 409 | `invitation_cancelled` | the organizer cancelled the event |
| 409 | `identity_ambiguous` | the Chat account named after the user's email does not list that email |
| 409 | `room_encrypted` | the room is encrypted, so its messages cannot be read; `room` gives what it shows of itself |
| 409 | `file_encrypted` | the file is encrypted on the user's devices, or the document is protected by a password |
| 409 | `file_blocked` | the antivirus of the user's Drive blocks the file |
| 409 | `folder_shared` | the folder is shared with other people, or lies in a shared folder |
| 409 | `event_exists` | the user's calendar has an event of this title at these times already, with other details: nothing was changed |
| 409 | `name_taken` | a file or folder of that name is already in the folder |
| 409 | `quota_exceeded` | the user's Drive has no room left for the file |
| 409 | `owner_not_member` | no member of the board, or more than one, has the user's email, when a contract writes on it or reads a task with assignees |
| 409 | `board_archived` | the board is archived |
| 409 | `section_required` | a new task names no section, and the board has none `unstarted` to put it in: `sections` lists them |
| 409 | `no_completed_section` | the task is in a section, and the board has no `completed` section to move it to |
| 409 | `mailbox_ambiguous` | several of the user's mailboxes have the name given, or the role archive: the user says which one, and `move_email`, or `move_emails` for several emails, takes its id |
| 409 | `trash_ambiguous` | several of the user's mailboxes have the role trash, which no contract chooses among: the user keeps a single one in Twake Mail |
| 409 | `mailbox_forbidden` | `move_email` and `move_emails` do not move an email to drafts, sent, outbox, templates, trash or spam |
| 409 | `email_in_spam` | the email is in spam, which only `trash_email` and `trash_emails` take it out of; the code of an email refused when several are moved at once |
| 409 | `contact_exists` | the user's default address book has a contact with one of the emails already, or the one the same call added, changed since: `book_id` and `contact_id` name it, and nothing was added |
| 409 | `not_a_post` | the item of the feed is a card, which shows what an app did: only posts are edited or deleted |
| 409 | `member_exists` | some of the people to add are members of the space already, with another role, which `update_space_member` changes: `members` names them, when the contract tells, and nobody was added |
| 409 | `last_admin` | the change or the removal would leave the space without an admin |
| 413 | `file_too_large` | the document takes more than the 20 MiB the service reads, or more than it reads once uncompressed |
| 413 | `contact_too_large` | the contact would take more than the 1 MiB Contacts takes in a card: nothing was written |
| 415 | `content_not_extractable` | the file is neither text nor a document the service reads, or the document is damaged, holds no text, as a scanned PDF, or gave none in the time or the memory its reading has |
| 429 | `chat_rate_limited` | Chat limits the requests made as the user; `retry_after_ms` says when to try again, when Chat says it |
| 502 | `calendar_refused` | Calendar refused the user's token |
| 502 | `calendar_unavailable` | Calendar did not answer, or answered in an unexpected form |
| 502 | `chat_refused` | Chat, or the gateway's route to it, refused the contracts |
| 502 | `chat_unavailable` | Chat did not answer, or answered in an unexpected form |
| 502 | `mail_account_mismatch` | the JMAP session TMail opened for the token is another user's |
| 502 | `mail_refused` | TMail refused the user's token |
| 502 | `mail_unavailable` | TMail did not answer, answered an error, or in an unexpected form; the code of an email TMail did not move when several are moved at once |
| 502 | `drive_refused` | the user's Drive instance refused their Drive token |
| 502 | `drive_unavailable` | the user's Drive instance did not answer, or answered in an unexpected form |
| 502 | `tasks_refused_token` | Tasks refused the user's token |
| 502 | `tasks_unavailable` | Tasks did not answer, or answered in an unexpected form |
| 502 | `task_created_partially` | Tasks created the task, then failed to set its priority or due date: `board_id`, `task_id` and `key` name it |
| 502 | `contacts_refused` | Contacts refused the user's token |
| 502 | `contacts_unavailable` | Contacts did not answer, or answered in an unexpected form |
| 502 | `space_refused` | Space refused the user's token, or knows the user in no organization, as when their token gives it no `org_id` |
| 502 | `space_unavailable` | Space did not answer, answered an error, or in an unexpected form |
| 503 | `keys_unavailable` | the signing keys of LemonLDAP-NG could not be fetched, and none are held |
| 503 | `reading_busy` | the service reads as many documents as it may at once, or one of the user's, and none ended in the 10 seconds a request waits: try again in a few seconds |

Routing errors, such as an unknown path, use the same format, with a `code` named after their HTTP status (`not_found`, `method_not_allowed`).

## Run

```sh
OIDC_ISSUER=https://sign-up.dev.twake.lin-saas.com/ \
CALENDAR_URL=https://calendar-backend.dev.twake.lin-saas.com \
  uv run uvicorn --factory twake_space_agent_contracts.app:create_app_from_env --port 8080
```

| Variable | |
|---|---|
| `OIDC_ISSUER` | the issuer of the users' tokens, exactly as in their `iss` claim |
| `OIDC_AUDIENCE` | the audience the tokens must have, `twake-space-agents` by default |
| `OIDC_JWKS_URL` | the issuer's signing keys, `<issuer>/oauth2/jwks` by default, where LemonLDAP-NG publishes them |
| `CALENDAR_URL` | the Calendar side service, which Calendar and Contacts go through |
| `PUBLISHED_APPS` | the applications the service publishes, by domain, comma separated: `calendar` when unset or empty (see [Applications](#applications)) |
| `CHAT_URL` | the gateway's outbound route to Synapse, which adds the token of the contracts' application service; needed once `PUBLISHED_APPS` names `chat`, and only then |
| `CHAT_GATEWAY_KEY` | the key the gateway's outbound route to Synapse admits, so that only this service uses the application service's token: sent in `apikey` on each call to `CHAT_URL`, and to no other application; needed once `PUBLISHED_APPS` names `chat`, and only then |
| `MATRIX_SERVER_NAME` | the name of Chat's homeserver, which ends its users' Matrix ids; needed once `PUBLISHED_APPS` names `chat`, and only then |
| `MATRIX_MAIL_DOMAIN` | the mail domain of its users, the server name by default |
| `MAIL_URL` | TMail's JMAP API, under which the service calls `/jmap/session` and `/jmap`; needed once `PUBLISHED_APPS` names `mail`, and only then |
| `DRIVE_INSTANCE_DOMAIN` | the domain of the users' cozy-stack instances, each one name under it, such as `dev.twake.lin-saas.com` on dev; needed once `PUBLISHED_APPS` names `drive`, and only then |
| `DRIVE_SCHEME` | how the service reaches the users' Drive instances, `https` by default; `http` for a local cozy-stack |
| `DRIVE_PORT` | the port of those instances, when it is not the scheme's, such as `8080` for a local cozy-stack |
| `TASKS_URL` | Twake Tasks, whose REST API is under `/api`; needed once `PUBLISHED_APPS` names `tasks`, and only then |
| `SPACE_URL` | the Twake Space backend, which serves `/spaces` and `/organization/members` under it; needed once `PUBLISHED_APPS` names `space`, and only then |

The image `ghcr.io/linagora/twake-space-agent-contracts` listens on 8080 as user 10001 and reads the same variables. It is published as `latest` from `main` and with the version from `v*` tags.

## Test

The tests call the HTTP API, and need neither a database nor Docker. LemonLDAP-NG's signing keys, the Calendar side service, with esn-sabre's address books behind its `/dav` proxy and its search across them, TMail, Synapse, behind the gateway's outbound route, the user's cozy-stack instance, Twake Tasks and the Twake Space backend, with the directory of the organization behind it, are faked at the HTTP boundary. The documents the fake cozy-stack serves are built in the tests, by python-docx, python-pptx and openpyxl, which only the tests use, or by hand, as are OpenDocument files, PDFs and the documents crafted against a reader, and each is read in its own process, as the service reads them. [`tests/test_openapi.py`](tests/test_openapi.py) holds the OpenAPI document to the rules of the catalog: a risk for every write, the writes that tell what they would do, the words of every published application, a worked call in every description, and schemas written whole.

```sh
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
```

## License

[AGPL-3.0](LICENSE)
