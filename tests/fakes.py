"""What the service reaches over HTTP, faked at that boundary: the signing keys of LemonLDAP-NG,
the Calendar side service, TMail, and Synapse behind the gateway's outbound route. Also the clock
the token checks read."""

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from twake_space_agent_contracts.applications import APPLICATIONS
from twake_space_agent_contracts.settings import Settings

# LemonLDAP-NG as the token broker's tokens come from it: issuer, audiences and signing key
ISSUER = "https://sign-up.test/"
AUDIENCE = "twake-space-agents"
SETTINGS = Settings(
    issuer=ISSUER,
    audience=AUDIENCE,
    jwks_url="https://sign-up.test/oauth2/jwks",
    calendar_url="https://calendar.test",
    # Every application the service has, so that the tests reach all their contracts
    published_apps=frozenset(application.domain for application in APPLICATIONS),
    chat_url="https://gateway.test/synapse",
    # Apart from the mail domain, as a deployment may have them
    matrix_server_name="chat.twake.test",
    matrix_mail_domain="twake.test",
    mail_url="https://tmail.test",
)
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "sig-1"

MMAUDET_CALENDAR_ID = "6650a1b2c3d4e5f6a7b8c9d0"


def email_of(uid: str) -> str:
    return f"{uid}@twake.test"


def matrix_id(uid: str) -> str:
    return f"@{uid}:{SETTINGS.matrix_server_name}"


def token_for(
    email: str,
    *,
    key: rsa.RSAPrivateKey = SIGNING_KEY,
    key_id: str = KEY_ID,
    token_type: str = "at+JWT",
    **claims: Any,
) -> str:
    """An access token of the twake-space-agents client, as APISIX gets it from the broker.

    LemonLDAP-NG types its access tokens at+JWT, and names the client in client_id.
    """
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": [AUDIENCE, "tcalendar"],
        "client_id": AUDIENCE,
        "sub": email,
        "iat": now,
        "exp": now + 3600,
    } | claims
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": key_id, "typ": token_type})


def as_user(email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(email)}"}


class FakeClock:
    """Seconds, as time.monotonic counts them, moved forward by the tests."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class FakeIssuer:
    """The signing keys LemonLDAP-NG publishes: the test key, unless a test rotates it away."""

    def __init__(self) -> None:
        self.keys = {KEY_ID: SIGNING_KEY}
        self.down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        published = [
            jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
            | {"kid": key_id, "use": "sig"}
            for key_id, key in self.keys.items()
        ]
        return httpx.Response(200, json={"keys": published})


@dataclass
class CalendarObject:
    """An event in a user's calendar: whose calendar, and the event in jCal."""

    owner: str
    jcal: list[Any]


def uids_of(jcal: list[Any]) -> set[str]:
    return {prop[3] for component in jcal[2] for prop in component[1] if prop[0] == "uid"}


class FakeCalendar:
    """The Calendar side service, as the contracts go through it with the bearer's token.

    The user lookup by email; the JSON free/busy of esn-sabre, which leaves out the events whose
    UIDs it is given, with times written as esn-sabre writes them, 20261006T150000Z; and the
    user's own events, found by UID with esn-sabre's JSON REPORT and written back with PUT.
    """

    def __init__(self) -> None:
        self.users: dict[str, str] = {email_of("mmaudet"): MMAUDET_CALENDAR_ID}
        self.busy: dict[str, list[dict[str, str]]] = {}
        """Busy slots by user id: uid, start, end."""
        self.down = False
        self.refused_tokens = False
        self.free_busy_requests: list[dict[str, Any]] = []
        self.objects: dict[str, CalendarObject] = {}
        """Events by href, as esn-sabre writes it: without the /dav of the side service."""
        self.writes: list[str] = []
        """The hrefs written, in order."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            caller = jwt.decode(bearer, options={"verify_signature": False})["sub"]
        except jwt.InvalidTokenError:
            return httpx.Response(401)
        if self.refused_tokens:
            return httpx.Response(401)
        if request.method == "GET" and request.url.path == "/api/users":
            email = request.url.params.get("email")
            if email != caller:
                return httpx.Response(403)
            user = self.users.get(caller)
            return httpx.Response(
                200, json=[{"_id": user, "preferredEmail": email}] if user else []
            )
        if request.method == "POST" and request.url.path == "/dav/calendars/freebusy":
            return self._free_busy(request, self.users.get(caller))
        if request.method == "REPORT" and request.url.path.endswith(".json"):
            return self._find_by_uid(request, self.users.get(caller))
        if request.method == "PUT" and request.url.path.startswith("/dav/calendars/"):
            return self._write(request, self.users.get(caller))
        return httpx.Response(404)

    def _find_by_uid(self, request: httpx.Request, user: str | None) -> httpx.Response:
        # esn-sabre answers JSON for this exact Accept only, and searches the calendars of the
        # home it is given, which must be the caller's
        if request.headers.get("accept") != "application/json":
            return httpx.Response(406)
        if user is None or request.url.path != f"/dav/calendars/{user}.json":
            return httpx.Response(403)
        uid = json.loads(request.content).get("uid")
        if not uid:
            return httpx.Response(400)
        for href, stored in self.objects.items():
            if stored.owner == user and uid in uids_of(stored.jcal):
                item = {"_links": {"self": {"href": href}}, "etag": '"1"', "data": stored.jcal}
                return httpx.Response(200, json={"_embedded": {"dav:item": [item]}})
        return httpx.Response(404)

    def _write(self, request: httpx.Request, user: str | None) -> httpx.Response:
        href = request.url.path.removeprefix("/dav")
        stored = self.objects.get(href)
        if stored is None or stored.owner != user:
            return httpx.Response(403)
        # esn-sabre reads jCal whenever the body starts with "[", whatever its Content-Type
        if not request.content.startswith(b"["):
            return httpx.Response(415)
        stored.jcal = json.loads(request.content)
        self.writes.append(href)
        return httpx.Response(204)

    def _free_busy(self, request: httpx.Request, user: str | None) -> httpx.Response:
        # esn-sabre answers JSON for this exact Accept only
        if request.headers.get("accept") != "application/json":
            return httpx.Response(406)
        body = json.loads(request.content)
        self.free_busy_requests.append(body)
        if user is None or body.get("users") != [user]:
            return httpx.Response(403)
        busy = [
            slot
            for slot in self.busy.get(user, [])
            if slot["uid"] not in body.get("uids", [])
            and slot["start"] < body["end"]
            and slot["end"] > body["start"]
        ]
        free_busy = {"id": user, "calendars": [{"id": user, "busy": busy}]}
        return httpx.Response(
            200, json={"start": body["start"], "end": body["end"], "users": [free_busy]}
        )


def matrix_error(status: int, errcode: str, error: str) -> httpx.Response:
    return httpx.Response(status, json={"errcode": errcode, "error": error})


def said(event_id: str, sender: str, at: str, body: str, **content: Any) -> dict[str, Any]:
    """A message as Synapse gives it, sent at an RFC 3339 time; text unless content says."""
    return event(
        event_id, "m.room.message", sender, at, {"msgtype": "m.text", "body": body} | content
    )


def event(
    event_id: str, event_type: str, sender: str, at: str, content: dict[str, Any]
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "type": event_type,
        "sender": sender,
        "origin_server_ts": int(datetime.fromisoformat(at).timestamp() * 1000),
        "content": content,
        "unsigned": {"age": 1000},
    }


@dataclass
class FakeRoom:
    """A room of the homeserver: its joined members, what it says of itself, its events from the
    oldest, and how many messages each member has not read."""

    members: dict[str, str | None]
    """Display names by Matrix id."""
    name: str | None = None
    topic: str | None = None
    encrypted: bool = False
    timeline: list[dict[str, Any]] = field(default_factory=list)
    unread: dict[str, int] = field(default_factory=dict)

    def state(self) -> dict[str, dict[str, Any]]:
        """The contents of its state events with an empty state key, by type, as they are now."""
        state: dict[str, dict[str, Any]] = {}
        if self.name is not None:
            state["m.room.name"] = {"name": self.name}
        if self.topic is not None:
            state["m.room.topic"] = {"topic": self.topic}
        if self.encrypted:
            state["m.room.encryption"] = {"algorithm": "m.megolm.v1.aes-sha2"}
        return state


class FakeSynapse:
    """Synapse's client API behind the gateway's outbound route, which adds the token of the
    contracts' application service: each request acts as the user that user_id names, who must
    have an account.

    Whether the user is in a room is the contracts' own check: the fake answers a room's
    endpoints whoever asks, so that nothing else keeps out a room the user has not joined.
    """

    def __init__(self) -> None:
        self.accounts: dict[str, list[str]] = {matrix_id("mmaudet"): [email_of("mmaudet")]}
        """The email addresses of each account, by Matrix id."""
        self.rooms: dict[str, FakeRoom] = {}
        self.direct: dict[str, dict[str, list[str]]] = {}
        """The m.direct account data of each user: their direct rooms, by whom they are with."""
        self.down = False
        self.answer: httpx.Response | None = None
        """What to answer every request, such as a refusal."""
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("Synapse does not answer", request=request)
        if self.answer is not None:
            return self.answer
        user = request.url.params.get("user_id", "")
        if user not in self.accounts:
            return matrix_error(
                403, "M_FORBIDDEN", f"Application service has not registered this user ({user})"
            )
        path = request.url.path.removeprefix("/synapse/_matrix/client/v3")
        if request.method != "GET":
            return matrix_error(405, "M_UNRECOGNIZED", "Unrecognized request")
        if path == "/account/3pid":
            threepids = [
                {"medium": "email", "address": address, "validated_at": 0, "added_at": 0}
                for address in self.accounts[user]
            ]
            return httpx.Response(200, json={"threepids": threepids})
        if path == "/joined_rooms":
            joined = [room_id for room_id, room in self.rooms.items() if user in room.members]
            return httpx.Response(200, json={"joined_rooms": joined})
        if path == "/sync":
            return self._sync(user)
        room_id, _, endpoint = path.removeprefix("/rooms/").partition("/")
        room = self.rooms.get(room_id) if path.startswith("/rooms/") else None
        if room is None:
            return matrix_error(403, "M_FORBIDDEN", f"User {user} not in room {room_id}")
        if endpoint == "joined_members":
            joined_members = {
                member: {"display_name": name, "avatar_url": f"mxc://chat.twake.test/{member}"}
                for member, name in room.members.items()
            }
            return httpx.Response(200, json={"joined": joined_members})
        if endpoint == "messages":
            return self._messages(request, room)
        content = room.state().get(endpoint.removeprefix("state/").removesuffix("/"))
        if not endpoint.startswith("state/") or content is None:
            return matrix_error(404, "M_NOT_FOUND", "Event not found")
        return httpx.Response(200, json=content)

    def _sync(self, user: str) -> httpx.Response:
        # The filter is not read: every room gives its state and its last event
        joined = {
            room_id: {
                "state": {
                    "events": [
                        {"type": event_type, "state_key": "", "sender": user, "content": content}
                        for event_type, content in room.state().items()
                    ]
                },
                "timeline": {"events": room.timeline[-1:], "limited": len(room.timeline) > 1},
                "unread_notifications": {
                    "notification_count": room.unread.get(user, 0),
                    "highlight_count": 0,
                },
            }
            for room_id, room in self.rooms.items()
            if user in room.members
        }
        direct = self.direct.get(user)
        account_data = [] if direct is None else [{"type": "m.direct", "content": direct}]
        return httpx.Response(
            200,
            json={
                "next_batch": "s1",
                "rooms": {"join": joined},
                "account_data": {"events": account_data},
            },
        )

    def _messages(self, request: httpx.Request, room: FakeRoom) -> httpx.Response:
        """A room's events backwards from a token, as many as asked for, of the types the filter
        names; the tokens count the events, from the oldest."""
        params = request.url.params
        types = json.loads(params.get("filter", "{}")).get("types")
        events = [event for event in room.timeline if types is None or event["type"] in types]
        start = params.get("from", f"t{len(events)}")
        if params.get("dir") != "b" or not start.startswith("t") or not start[1:].isdigit():
            return matrix_error(400, "M_INVALID_PARAM", "Invalid stream token")
        end = min(int(start[1:]), len(events))
        first = max(0, end - int(params.get("limit", "10")))
        answer: dict[str, Any] = {"chunk": events[first:end][::-1], "start": start}
        if first > 0:
            answer["end"] = f"t{first}"
        return httpx.Response(200, json=answer)


CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SHARES = "urn:apache:james:params:jmap:mail:shares"

MMAUDET = email_of("mmaudet")
# mmaudet's own mailboxes, as TMail creates them
INBOX = "mbx-inbox"
TRASH = "mbx-trash"
SPAM = "mbx-spam"


def account_of(username: str) -> str:
    """The JMAP account id James gives a user: the SHA-256 of their username, in hex."""
    return hashlib.sha256(username.encode()).hexdigest()


@dataclass
class StoredMailbox:
    """A mailbox in TMail: whose it is, who it is shared with, and what Mailbox/get gives of it."""

    owner: str
    jmap: dict[str, Any]
    shared_with: set[str] = field(default_factory=set)


@dataclass
class MethodCall:
    name: str
    arguments: dict[str, Any]


class MethodError(Exception):
    """A JMAP method that fails, which TMail answers with an error response of this type."""


def _addresses(email: dict[str, Any], *headers: str) -> str:
    return " ".join(
        f"{address.get('name') or ''} {address['email']}"
        for header in headers
        for address in email.get(header) or []
    ).lower()


def _words(email: dict[str, Any]) -> str:
    """What text looks in, as James searches it, but for attachments."""
    return " ".join(
        [_addresses(email, "from", "to", "cc"), email["subject"], email["body"]]
    ).lower()


# What Email/query filters on here: one condition, as James wants it, of these properties only
CONDITIONS: dict[str, Callable[[dict[str, Any], Any], bool]] = {
    "inMailbox": lambda email, mailbox: mailbox in email["mailboxIds"],
    # In at least one mailbox other than those
    "inMailboxOtherThan": lambda email, mailboxes: bool(set(email["mailboxIds"]) - set(mailboxes)),
    "hasKeyword": lambda email, keyword: bool(email["keywords"].get(keyword)),
    "notKeyword": lambda email, keyword: not email["keywords"].get(keyword),
    "from": lambda email, text: text.lower() in _addresses(email, "from"),
    "text": lambda email, text: text.lower() in _words(email),
    "after": lambda email, time: email["receivedAt"] >= time,
    "before": lambda email, time: email["receivedAt"] < time,
}


def _pointed(value: Any, path: list[str]) -> Any:
    """What a JMAP result reference points at (RFC 8620): a * maps the rest of the path over a
    list, whose lists it flattens."""
    if not path:
        return value
    if path[0] == "*":
        found = [_pointed(item, path[1:]) for item in value]
        return [part for item in found for part in (item if isinstance(item, list) else [item])]
    return _pointed(value[path[0]], path[1:])


class FakeTMail:
    """TMail's JMAP API, as the contracts go through it with the bearer's token, answering the way
    James does.

    The session opens the account of the address TMail finds for the token, its username: the
    token's subject unless a test says otherwise. It lists the accounts delegated to the user
    first, then theirs, the primary one. Mailbox/get and Email/query keep to the user's own
    mailboxes unless the request uses the shares capability; Email/get and Thread/get give any
    email the user may read, in a mailbox shared with them too. Email/query takes one condition,
    sorts by receivedAt, newest first, and refuses what it does not know.
    """

    def __init__(self) -> None:
        self.mailboxes: dict[str, StoredMailbox] = {
            mailbox_id: StoredMailbox(
                MMAUDET,
                {"name": name, "parentId": None, "role": role, "totalEmails": 0, "unreadEmails": 0},
            )
            for mailbox_id, name, role in [
                (INBOX, "INBOX", "inbox"),
                (TRASH, "Trash", "trash"),
                (SPAM, "Spam", "spam"),
            ]
        }
        self.emails: dict[str, dict[str, Any]] = {}
        """Emails by id, as JMAP gives them, with the text of their only part as body."""
        self.usernames: dict[str, str] = {}
        """The username TMail finds for a token's subject, when it is not that subject."""
        self.delegations: dict[str, list[str]] = {}
        """The accounts delegated to a user, by their owners' usernames."""
        self.down = False
        self.refused_tokens = False
        self.failing_method: str | None = None
        self.searches_shared = False
        """Whether Email/query also searches the mailboxes shared with the user without the shares
        capability, which James does not do: what the contracts' own check is for."""
        self.sessions = 0
        """How many times the session was read."""
        self.calls: list[MethodCall] = []
        """The JMAP method calls received, in order, as they were sent."""

    def deliver(self, email_id: str, mailbox: str, **jmap: Any) -> None:
        """Puts an email from Paul in a mailbox, with these JMAP properties over the defaults."""
        self.emails[email_id] = {
            "id": email_id,
            "threadId": f"thread-{email_id}",
            "mailboxIds": {mailbox: True},
            "keywords": {},
            "receivedAt": "2026-10-06T09:00:00Z",
            "from": [{"name": "Paul Martin", "email": "paul.martin@twake.test"}],
            "to": [{"name": "Michel-Marie", "email": MMAUDET}],
            "cc": [],
            "replyTo": None,
            "subject": "Budget Q4",
            "preview": "Hello, here is the budget",
            "hasAttachment": False,
            "body": "Hello,\n\nhere is the budget.",
        } | jmap

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            subject = jwt.decode(bearer, options={"verify_signature": False})["sub"]
        except jwt.InvalidTokenError:
            return httpx.Response(401)
        if self.refused_tokens:
            return httpx.Response(401)
        username = self.usernames.get(subject, subject)
        if request.method == "GET" and request.url.path == "/jmap/session":
            return self._session(username)
        if request.method == "POST" and request.url.path == "/jmap":
            return self._api(username, json.loads(request.content))
        return httpx.Response(404)

    def _session(self, username: str) -> httpx.Response:
        self.sessions += 1
        accounts = {
            account_of(owner): {"name": owner, "isPersonal": False, "isReadOnly": False}
            for owner in self.delegations.get(username, [])
        } | {account_of(username): {"name": username, "isPersonal": True, "isReadOnly": False}}
        session = {
            "capabilities": {CORE: {}, MAIL: {}, SHARES: {}},
            "accounts": accounts,
            "primaryAccounts": {CORE: account_of(username), MAIL: account_of(username)},
            "username": username,
            # TMail's public address, which the contracts do not go through
            "apiUrl": "https://jmap.public.test/jmap",
            "state": "0",
        }
        return httpx.Response(200, json=session)

    def _api(self, username: str, request: dict[str, Any]) -> httpx.Response:
        shares = SHARES in request["using"]
        # Whose mailbox each account opens: the user's own, or one delegated to them
        owners = {account_of(owner): owner for owner in self.delegations.get(username, [])}
        owners[account_of(username)] = username
        results: dict[str, dict[str, Any]] = {}
        responses: list[list[Any]] = []
        for name, arguments, call_id in request["methodCalls"]:
            self.calls.append(MethodCall(name, arguments))
            try:
                owner = owners.get(arguments["accountId"])
                if owner is None:
                    raise MethodError("accountNotFound")
                if name == self.failing_method:
                    raise MethodError("serverFail")
                result = self._method(name, owner, self._resolved(arguments, results), shares)
            except MethodError as error:
                responses.append(["error", {"type": str(error)}, call_id])
                continue
            results[call_id] = result
            responses.append([name, result, call_id])
        # In ASCII, as JSON may write any text, a surrogate left alone included
        answer = json.dumps({"methodResponses": responses, "sessionState": "0"})
        return httpx.Response(200, content=answer, headers={"Content-Type": "application/json"})

    @staticmethod
    def _resolved(arguments: dict[str, Any], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """The arguments, their back-references to earlier results replaced by their values."""
        resolved = {}
        for key, value in arguments.items():
            if key.startswith("#"):
                if value["resultOf"] not in results:
                    raise MethodError("invalidResultReference")
                path = value["path"].strip("/").split("/")
                key, value = key[1:], _pointed(results[value["resultOf"]], path)
            resolved[key] = value
        return resolved

    def _method(
        self, name: str, owner: str, arguments: dict[str, Any], shares: bool
    ) -> dict[str, Any]:
        answer: dict[str, Any] = {"accountId": arguments["accountId"], "state": "0"}
        if name == "Mailbox/get":
            return answer | self._mailboxes(owner, arguments, shares)
        if name == "Email/query":
            return answer | self._query(owner, arguments, shares)
        if name == "Email/get":
            return answer | self._emails(owner, arguments)
        if name == "Thread/get":
            return answer | self._threads(owner, arguments)
        raise MethodError("unknownMethod")

    def _readable(self, email: dict[str, Any], owner: str, shares: bool = True) -> bool:
        return any(
            self.mailboxes[mailbox].owner == owner
            or (shares and owner in self.mailboxes[mailbox].shared_with)
            for mailbox in email["mailboxIds"]
        )

    def _mailboxes(self, owner: str, arguments: dict[str, Any], shares: bool) -> dict[str, Any]:
        visible = {
            mailbox_id: mailbox.jmap | {"id": mailbox_id}
            for mailbox_id, mailbox in self.mailboxes.items()
            if mailbox.owner == owner or (shares and owner in mailbox.shared_with)
        }
        wanted = list(visible) if arguments.get("ids") is None else arguments["ids"]
        return {
            "list": [
                {key: visible[mailbox_id][key] for key in arguments["properties"]}
                for mailbox_id in wanted
                if mailbox_id in visible
            ],
            "notFound": [mailbox_id for mailbox_id in wanted if mailbox_id not in visible],
        }

    def _query(self, owner: str, arguments: dict[str, Any], shares: bool) -> dict[str, Any]:
        condition = arguments.get("filter") or {}
        if not set(condition) <= set(CONDITIONS):
            raise MethodError("unsupportedFilter")
        if arguments.get("sort") != [{"property": "receivedAt", "isAscending": False}]:
            raise MethodError("unsupportedSort")
        found = sorted(
            (
                email
                for email in self.emails.values()
                if self._readable(email, owner, shares or self.searches_shared)
                and all(CONDITIONS[key](email, value) for key, value in condition.items())
            ),
            key=lambda email: str(email["receivedAt"]),
            reverse=True,
        )
        position, limit = arguments.get("position", 0), arguments.get("limit", 256)
        return {
            "queryState": "0",
            "canCalculateChanges": False,
            "position": position,
            "ids": [email["id"] for email in found[position : position + limit]],
        }

    def _emails(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        readable = {
            email_id: email
            for email_id, email in self.emails.items()
            if self._readable(email, owner)
        }
        return {
            "list": [
                self._properties(readable[email_id], arguments)
                for email_id in arguments["ids"]
                if email_id in readable
            ],
            "notFound": [email_id for email_id in arguments["ids"] if email_id not in readable],
        }

    @staticmethod
    def _properties(email: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        """The email as Email/get gives it: its only text part, cut at maxBodyValueBytes, counted
        in characters, which are bytes in the ASCII of the tests' long bodies."""
        body = email["body"]
        cut = arguments.get("maxBodyValueBytes") or len(body)
        parts = {
            "textBody": [{"partId": "1", "type": "text/plain"}],
            "bodyValues": (
                {
                    "1": {
                        "value": body[:cut],
                        "isEncodingProblem": email.get("encodingProblem", False),
                        "isTruncated": len(body) > cut,
                    }
                }
                if arguments.get("fetchTextBodyValues")
                else {}
            ),
        }
        return {key: (email | parts)[key] for key in arguments["properties"]}

    def _threads(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        threads = {
            thread_id: [
                email["id"]
                for email in sorted(self.emails.values(), key=lambda email: email["receivedAt"])
                if email["threadId"] == thread_id and self._readable(email, owner)
            ]
            for thread_id in arguments["ids"]
        }
        return {
            "list": [
                {"id": thread_id, "emailIds": email_ids}
                for thread_id, email_ids in threads.items()
                if email_ids
            ],
            "notFound": [thread_id for thread_id, email_ids in threads.items() if not email_ids],
        }


class FakeBoundary:
    """Routes the service's HTTP calls to the fakes."""

    def __init__(self) -> None:
        self.issuer = FakeIssuer()
        self.calendar = FakeCalendar()
        self.synapse = FakeSynapse()
        self.tmail = FakeTMail()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url == SETTINGS.jwks_url:
            return self.issuer.handle(request)
        if request.url.host == "calendar.test":
            return self.calendar.handle(request)
        if request.url.host == "gateway.test" and request.url.path.startswith("/synapse/"):
            return self.synapse.handle(request)
        if request.url.host == "tmail.test":
            return self.tmail.handle(request)
        return httpx.Response(404)
