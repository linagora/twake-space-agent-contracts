"""What the service reaches over HTTP, faked at that boundary: the signing keys of LemonLDAP-NG,
the Calendar side service, and Synapse behind the gateway's outbound route. Also the clock the
token checks read."""

import json
import time
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


class FakeBoundary:
    """Routes the service's HTTP calls to the fakes."""

    def __init__(self) -> None:
        self.issuer = FakeIssuer()
        self.calendar = FakeCalendar()
        self.synapse = FakeSynapse()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url == SETTINGS.jwks_url:
            return self.issuer.handle(request)
        if request.url.host == "calendar.test":
            return self.calendar.handle(request)
        if request.url.host == "gateway.test" and request.url.path.startswith("/synapse/"):
            return self.synapse.handle(request)
        return httpx.Response(404)
