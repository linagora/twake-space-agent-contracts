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

The service publishes only the applications `PUBLISHED_APPS` names, `events` and `calendar` when it is unset or empty: it serves their contracts and describes them in its OpenAPI document, while the paths of any other application answer 404 `not_found`, like a path the service never had. The operator keeps it equal to the applications APISIX routes, so that an application leaves the agents' tools when it leaves the gateway. A name the service does not know stops it from starting. `events`, the assistant's own feed, which the harness reads without asking and checks the invitations it brings with, is published whatever the setting says.

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

### Adding an application

An application comes as a module of its own, as Calendar does: its client, its `<APP>_URL` setting, its typed problems, its routers, one per contract, its tests at the HTTP boundary and its section below. It is declared by one entry of `APPLICATIONS`, in `applications.py`: its domain, its words, and a function that builds its routers from the service's settings, events database, HTTP client and caller dependency. The tests publish every declared application; a deployment publishes it once `PUBLISHED_APPS` names it.

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
- Every operation's description ends with a worked call, its values in the exact format the gateway checks: `Example: event_id=f7c9….`, or `Example, <what it is an example of>: name=value, name=value.`, and `Example: (no parameters).` for an operation that takes none. A list gives its name once per value, and a body is written `body=<JSON>`. The tests check each value against the operation's schema in the document, as the gateway does.
- The schema of a parameter or of a body is written whole in its operation, without a reference to the document's components: the harness gives it to the model as it is.
- A contract that makes the application notify other people says so in its description, as `accept_invitation` does of the organizer.
- Text other people wrote, which an agent reads as data and never as instructions, comes back in an `untrusted` object, separately from what the contract computed.

### `events.read.v1`

Reads the workplace events stored for the user the agent acts for: those whose targets name the user's email (`data.targets[].native_id`). An event sent to another address of the user, such as an alias, is not found.

| Operation | Request | Answer |
|---|---|---|
| `read_event` | `GET /contracts/v1/events/{event_id}` | the event, if the user is one of its targets |
| `list_events` | `GET /contracts/v1/events?type=…&limit=…` | `{"events": [...]}`, the user's events newest first |

- `limit` goes from 1 to 100 and is 20 by default.
- Pass `type=com.twake.calendar.event.invited.v1` to list meeting invitations.
- An event the user is not a target of answers exactly like an unknown one, so the contract never reveals that an event exists.
- The title of the event's object, which its author wrote, comes back in `untrusted.title` rather than in `data.object`, where the rest is what the producer computed, such as the `uid` and the times of an invitation.

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
- It is a low-risk write (`x-twake-risk: low`): the user's own answer, which the owner's consent to write in Calendar covers without a confirmation each time.

### Chat, as the user

Synapse, the homeserver of Twake Chat, accepts no token of LemonLDAP-NG. The service calls its client API through the gateway's outbound route (`CHAT_URL`), which adds the token of the contracts' application service, and names the user in `user_id`: the service never holds that token.

- Chat is published once `PUBLISHED_APPS` names `chat`. The service then needs `CHAT_URL` and `MATRIX_SERVER_NAME`, and does not start without them; while Chat is not published, it needs neither.
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
- A mailbox, an email or a conversation outside the user's own mailboxes, such as in a mailbox shared with them, answers exactly like an unknown one: 404.
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
- Names, paths, types and contents come in an `untrusted` object, apart from what the contract computed: the user wrote them, or anyone who shared a file with them, and the type of a file is the one its uploader declared. They come without control characters, but for the tabs and line breaks of a text.
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

Reads the text of one of the user's files.

| Operation | Request | Answer |
|---|---|---|
| `read_file_content` | `GET /contracts/v1/drive/contents/{file_id}?max_bytes=…` | `{"id", "size", "truncated", "untrusted": {"name", "mime", "content"}}` |

- Only text is read: `text/*`, JSON, XML and YAML. Any other file, such as a PDF, an office document or a note, answers `content_not_extractable`.
- At most `max_bytes` bytes are read from the stack, 65,536 by default and 262,144 at most, and `truncated` tells that the file is longer. A character cut at the end is left out, and bytes that are not UTF-8 are replaced.
- A file encrypted on the user's devices answers `file_encrypted`; one the antivirus found infected, or whose download it blocks, `file_blocked`.
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

### Tasks, as the user

The Tasks contracts call the REST API of Twake Tasks 0.1.1 with the user's token. Tasks accepts it once the token broker's client has the audience `twaketasks` and LemonLDAP-NG gives Tasks the user's `uuid`, `org_id` and `sid`: Tasks then acts for the user's `uuid` in their `org_id`, and shows them the boards of the projects they are a member of. Tasks also refuses the token unless the `sub` LemonLDAP-NG gives its own client, `twaketasks-backend`, which introspects the token, is the one userinfo gives for the token broker's client: both clients must take the same identifier attribute. The service publishes the Tasks contracts once `PUBLISHED_APPS` names `tasks`, and then needs `TASKS_URL`: without it, it refuses to start.

- A user whose token gives Tasks no `org_id` is a personal account for Tasks, which then shows them only what lies outside any organization: an organization's boards answer like unknown ones. Nothing in Tasks' answers tells the contracts which of the two the user is.
- No contract lists the user's boards. Tasks 0.1.1 lists them only with `GET /api/boards`, which, as opening its web app does, creates the user's Inbox if they have none and accepts their pending invitations to projects, and a read must never act for the user. Until Tasks lists boards without side effects, an agent finds a board through the tasks it lists or searches.

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

## Errors

Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) problem (`application/problem+json`) with a stable `code`:

| Status | `code` | When |
|---|---|---|
| 400 | `invalid_request` | a parameter, or a field of the body, is invalid |
| 401 | `missing_token` | no bearer token |
| 401 | `invalid_token` | the token is not one the broker got for this service: another type, issuer, audience, client or key, or expired |
| 401 | `missing_drive_token` | a Drive contract without the user's Drive token |
| 404 | `event_not_found` | no event with this id concerns the user |
| 404 | `invitation_not_found` | no invitation with this id was sent to the user |
| 404 | `invitation_not_in_calendar` | the user's calendars no longer have the invitation, which may have been deleted |
| 404 | `calendar_user_not_found` | Calendar has no user with the user's email |
| 404 | `chat_account_not_found` | Chat has no account for the user's email |
| 404 | `room_not_found` | the user has joined no room with this id |
| 404 | `mailbox_not_found` | the user has no mailbox of their own with this id, or no Drafts mailbox for a draft |
| 404 | `email_not_found` | the user has no email with this id in their own mailboxes |
| 404 | `thread_not_found` | the user has no conversation with this id in their own mailboxes |
| 404 | `drive_instance_unknown` | no Drive instance of the platform is known for the user: LemonLDAP-NG gives no `workplaceFqdn` for them, or one outside `DRIVE_INSTANCE_DOMAIN` |
| 404 | `folder_not_found` | no folder with this id in the user's Drive, out of the trash |
| 404 | `file_not_found` | no file with this id in the user's Drive, out of the trash |
| 404 | `board_not_found` | the user is a member of no board with this id |
| 404 | `task_not_found` | the board shows no task with this id: it may be archived or in the trash |
| 409 | `not_an_attendee` | the invitation in the user's calendar does not list the user as an attendee |
| 409 | `recurring_invitation` | the invitation repeats, or is one occurrence of a series |
| 409 | `invitation_cancelled` | the organizer cancelled the event |
| 409 | `identity_ambiguous` | the Chat account named after the user's email does not list that email |
| 409 | `room_encrypted` | the room is encrypted, so its messages cannot be read; `room` gives what it shows of itself |
| 409 | `file_encrypted` | the file is encrypted on the user's devices |
| 409 | `file_blocked` | the antivirus of the user's Drive blocks the file |
| 409 | `folder_shared` | the folder is shared with other people, or lies in a shared folder |
| 409 | `name_taken` | a file or folder of that name is already in the folder |
| 409 | `quota_exceeded` | the user's Drive has no room left for the file |
| 409 | `owner_not_member` | the task has assignees, and no member of its board, or more than one, has the user's email |
| 415 | `content_not_extractable` | the file is not text |
| 429 | `chat_rate_limited` | Chat limits the requests made as the user; `retry_after_ms` says when to try again, when Chat says it |
| 502 | `calendar_refused` | Calendar refused the user's token |
| 502 | `calendar_unavailable` | Calendar did not answer, or answered in an unexpected form |
| 502 | `chat_refused` | Chat, or the gateway's route to it, refused the contracts |
| 502 | `chat_unavailable` | Chat did not answer, or answered in an unexpected form |
| 502 | `mail_account_mismatch` | the JMAP session TMail opened for the token is another user's |
| 502 | `mail_refused` | TMail refused the user's token |
| 502 | `mail_unavailable` | TMail did not answer, answered an error, or in an unexpected form |
| 502 | `drive_refused` | the user's Drive instance refused their Drive token |
| 502 | `drive_unavailable` | the user's Drive instance did not answer, or answered in an unexpected form |
| 502 | `tasks_refused_token` | Tasks refused the user's token |
| 502 | `tasks_unavailable` | Tasks did not answer, or answered in an unexpected form |
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
| `PUBLISHED_APPS` | the applications the service publishes, by domain, comma separated: `events,calendar` when unset or empty, and `events` always (see [Applications](#applications)) |
| `CHAT_URL` | the gateway's outbound route to Synapse, which adds the token of the contracts' application service; needed once `PUBLISHED_APPS` names `chat`, and only then |
| `MATRIX_SERVER_NAME` | the name of Chat's homeserver, which ends its users' Matrix ids; needed once `PUBLISHED_APPS` names `chat`, and only then |
| `MATRIX_MAIL_DOMAIN` | the mail domain of its users, the server name by default |
| `MAIL_URL` | TMail's JMAP API, under which the service calls `/jmap/session` and `/jmap`; needed once `PUBLISHED_APPS` names `mail`, and only then |
| `DRIVE_INSTANCE_DOMAIN` | the domain of the users' cozy-stack instances, each one name under it, such as `dev.twake.lin-saas.com` on dev; needed once `PUBLISHED_APPS` names `drive`, and only then |
| `DRIVE_SCHEME` | how the service reaches the users' Drive instances, `https` by default; `http` for a local cozy-stack |
| `DRIVE_PORT` | the port of those instances, when it is not the scheme's, such as `8080` for a local cozy-stack |
| `TASKS_URL` | Twake Tasks, whose REST API is under `/api`; needed once `PUBLISHED_APPS` names `tasks`, and only then |

The image `ghcr.io/linagora/twake-space-agent-contracts` listens on 8080 as user 10001 and reads the same variables. It is published as `latest` from `main` and with the version from `v*` tags.

## Test

The tests call the HTTP API against a real PostgreSQL that they start with Docker. LemonLDAP-NG's signing keys, the Calendar side service, TMail, Synapse, behind the gateway's outbound route, the user's cozy-stack instance and Twake Tasks are faked at the HTTP boundary. [`tests/test_openapi.py`](tests/test_openapi.py) holds the OpenAPI document to the rules of the catalog: a risk for every write, the words of every published application, a worked call in every description, and schemas written whole.

```sh
uv run pytest
uv run mypy
uv run ruff check . && uv run ruff format --check .
```

## License

[AGPL-3.0](LICENSE)
