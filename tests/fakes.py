"""What the service reaches over HTTP, faked at that boundary: the signing keys of LemonLDAP-NG,
the Calendar side service, with the address books of Twake Contacts behind it, TMail, Synapse behind
the gateway's outbound route, the owner's cozy-stack instance and Twake Tasks. Also the clock the
token checks read."""

import base64
import hashlib
import json
import posixpath
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qs, unquote

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from twake_space_agent_contracts.applications import APPLICATIONS
from twake_space_agent_contracts.settings import Settings

# The key of the contracts' consumer, which the gateway's outbound route to Synapse admits
CHAT_GATEWAY_KEY = "key-of-the-contracts-consumer"
# LemonLDAP-NG as the token broker's tokens come from it: issuer, audiences and signing key
ISSUER = "https://sign-up.test/"
AUDIENCE = "twake-space-agents"
SETTINGS = Settings(
    issuer=ISSUER,
    audience=AUDIENCE,
    jwks_url="https://sign-up.test/oauth2/jwks",
    calendar_url="https://calendar.test",
    tasks_url="https://tasks.test",
    # Every application the service has, so that the tests reach all their contracts
    published_apps=frozenset(application.domain for application in APPLICATIONS),
    chat_url="https://gateway.test/synapse",
    chat_gateway_key=CHAT_GATEWAY_KEY,
    # Apart from the mail domain, as a deployment may have them
    matrix_server_name="chat.twake.test",
    matrix_mail_domain="twake.test",
    mail_url="https://tmail.test",
    # Where the users' cozy-stack instances are, one name each under it
    drive_instance_domain="twake.test",
)
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "sig-1"

MMAUDET_CALENDAR_ID = "6650a1b2c3d4e5f6a7b8c9d0"
# The domain of the owner in Calendar and Contacts, whose address books its members read
MMAUDET_DOMAIN_ID = "6650a1b2c3d4e5f6a7b8c9e0"
# Another user of the platform, who may share their address books
ALICE_CALENDAR_ID = "6650a1b2c3d4e5f6a7b8c9d1"


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


# The owner's Drive: the host of their cozy-stack instance, and the access token of that instance
# the token broker holds for them
MMAUDET_INSTANCE = "mmaudet.twake.test"
MMAUDET_DRIVE_TOKEN = "cozy-access-token-of-mmaudet"


def as_drive_owner() -> dict[str, str]:
    """What APISIX sends on a Drive contract: the user's token, as on every contract, then the
    Drive token and instance that the broker gives."""
    return as_user(email_of("mmaudet")) | {
        "X-Twake-Drive-Token": MMAUDET_DRIVE_TOKEN,
        "X-Twake-Drive-Instance": MMAUDET_INSTANCE,
    }


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


def is_calendar_object(jcal: list[Any]) -> bool:
    """Whether esn-sabre would store this jCal, as iCalendar requires: a calendar with its version
    and producer, holding events that each have a UID, a time stamp and a start."""

    def names(component: list[Any]) -> set[str]:
        return {prop[0] for prop in component[1]}

    vevents = [component for component in jcal[2] if component[0] == "vevent"]
    return (
        jcal[0] == "vcalendar"
        and {"version", "prodid"} <= names(jcal)
        and bool(vevents)
        and all({"uid", "dtstamp", "dtstart"} <= names(vevent) for vevent in vevents)
    )


class FakeCalendar:
    """The Calendar side service, as the contracts go through it with the bearer's token.

    The user lookup by email; the user's settings, of which their time zone, always given, the
    deployment's when they set none; the JSON free/busy of esn-sabre, which leaves out the events
    whose UIDs it is given, with times written as esn-sabre writes them, 20261006T150000Z; and the
    user's own events, found by UID with esn-sabre's JSON REPORT, and written back, or added to
    their default calendar, with PUT.
    """

    def __init__(self) -> None:
        self.users: dict[str, str] = {email_of("mmaudet"): MMAUDET_CALENDAR_ID}
        self.domains: dict[str, str] = {email_of("mmaudet"): MMAUDET_DOMAIN_ID}
        """The domain of each user, by email."""
        self.contacts = FakeContacts(self)
        """Twake Contacts, which the side service proxies under /dav and searches."""
        self.time_zones: dict[str, str] = {email_of("mmaudet"): "Europe/Paris"}
        """The time zone each user set in Calendar, by email: the deployment's, here UTC, for a
        user who set none."""
        self.settings_down = False
        """Whether the side service fails to give the users' settings, and answers the rest."""
        self.busy: dict[str, list[dict[str, str]]] = {}
        """Busy slots by user id: uid, start, end."""
        self.down = False
        self.refused_tokens = False
        self.free_busy_requests: list[dict[str, Any]] = []
        self.objects: dict[str, CalendarObject] = {}
        """Events by href, as esn-sabre writes it: without the /dav of the side service."""
        self.writes: list[str] = []
        """The hrefs written, in order."""
        self.failing_writes: Literal["landed", "lost"] | None = None
        """How a write fails, if it does: "landed", kept, but answered after the service gave up
        waiting, as when esn-sabre is slow; "lost", refused with a 503 and not kept."""

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
            user = self.users.get(email or "")
            domains = [
                {"domain_id": domain, "joined_at": "1970-01-01T00:00:00.000Z"}
                for domain in [self.domains.get(email or "")]
                if domain is not None
            ]
            found = {"_id": user, "preferredEmail": email, "domains": domains}
            return httpx.Response(200, json=[found] if user else [])
        if request.method == "POST" and request.url.path == "/api/configurations":
            return self._configurations(request, caller)
        if request.method == "POST" and request.url.path == "/dav/calendars/freebusy":
            return self._free_busy(request, self.users.get(caller))
        if request.method == "REPORT" and request.url.path.endswith(".json"):
            return self._find_by_uid(request, self.users.get(caller))
        if request.method == "PUT" and request.url.path.startswith("/dav/calendars/"):
            return self._write(request, self.users.get(caller))
        if request.url.path.startswith("/dav/addressbooks/") or request.url.path.startswith(
            "/contacts/api/"
        ):
            return self.contacts.handle(request, caller)
        return httpx.Response(404)

    def _configurations(self, request: httpx.Request, caller: str) -> httpx.Response:
        """The settings asked for, by module, as the side service gives them: here the user's
        date and time settings alone."""
        if self.settings_down:
            return httpx.Response(503)
        settings = {
            ("core", "datetime"): {
                "timeZone": self.time_zones.get(caller, "UTC"),
                "use24hourFormat": True,
            }
        }
        modules = [
            {
                "name": module["name"],
                "configurations": [
                    {"name": key, "value": settings.get((module["name"], key))}
                    for key in module["keys"]
                ],
            }
            for module in json.loads(request.content)
        ]
        return httpx.Response(200, json=modules)

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
        # A new event goes in the caller's default calendar, whose id esn-sabre makes their own
        default_calendar = f"/calendars/{user}/{user}"
        if stored is None and user is not None and posixpath.dirname(href) == default_calendar:
            stored = CalendarObject(user, [])
        if stored is None or stored.owner != user:
            return httpx.Response(403)
        # esn-sabre reads jCal whenever the body starts with "[", whatever its Content-Type
        if not request.content.startswith(b"["):
            return httpx.Response(415)
        jcal = json.loads(request.content)
        if not is_calendar_object(jcal):
            return httpx.Response(415)
        if self.failing_writes == "lost":
            return httpx.Response(503)
        created = href not in self.objects
        stored.jcal = jcal
        self.objects[href] = stored
        self.writes.append(href)
        if self.failing_writes == "landed":
            raise httpx.ReadTimeout("Calendar answered too late", request=request)
        return httpx.Response(201 if created else 204)

    def _free_busy(self, request: httpx.Request, user: str | None) -> httpx.Response:
        # esn-sabre answers JSON for this exact Accept only
        if request.headers.get("accept") != "application/json":
            return httpx.Response(406)
        body = json.loads(request.content)
        self.free_busy_requests.append(body)
        # The caller asks for people who exist in Calendar, and sees their free/busy alone
        if (
            user is None
            or not body.get("users")
            or not set(body["users"]) <= set(self.users.values())
        ):
            return httpx.Response(403)
        free_busy = [
            {
                "id": person,
                "calendars": [
                    {
                        "id": person,
                        "busy": [
                            slot
                            for slot in self.busy.get(person, [])
                            if slot["uid"] not in body.get("uids", [])
                            and slot["start"] < body["end"]
                            and slot["end"] > body["start"]
                        ],
                    }
                ],
            }
            for person in body["users"]
        ]
        return httpx.Response(
            200, json={"start": body["start"], "end": body["end"], "users": free_busy}
        )


# What esn-sabre takes in a request at most: nginx's default client_max_body_size, which the image
# of esn-sabre keeps
SABRE_BODY_LIMIT = 1024 * 1024
# The ids of an address book that the side service's search takes: plain segments of a path
SEARCH_SEGMENT = re.compile(r"[a-zA-Z0-9._~-]+")
# The share access of a delegation, as sabre/dav and esn-sabre write it: reading, reading and
# writing, and administration with them
READ_ACCESS, READ_WRITE_ACCESS, ADMINISTRATION_ACCESS = 2, 3, 5
# The status of the invitation a delegation starts with: waiting for an answer, accepted, declined
INVITE_NORESPONSE, INVITE_ACCEPTED, INVITE_DECLINED = 1, 2, 3


def contact_id(card_name: str) -> str:
    """The id the contracts give the contact whose card has that name: the name in base64url,
    without padding."""
    return base64.urlsafe_b64encode(card_name.encode()).decode().rstrip("=")


def card_name(contact_id: str) -> str:
    """The name of the card of the contact of that id."""
    return base64.urlsafe_b64decode(contact_id + "=" * (-len(contact_id) % 4)).decode()


def jcard(uid: str, name: str | None, *properties: list[Any], version: str = "4.0") -> list[Any]:
    """A contact in jCard, as esn-sabre gives it: its version, its UID and its formatted name, if
    any, then the given properties."""
    named = [["fn", {}, "text", name]] if name is not None else []
    return [
        "vcard",
        [["version", {}, "text", version], ["uid", {}, "text", uid], *named, *properties],
    ]


def vcard_text(card: list[Any]) -> str:
    """A card in vCard text, as esn-sabre keeps it and its search reads it: a property on each
    line, its values escaped as vCard escapes them, each line folded as sabre/vobject folds it."""
    lines = ["BEGIN:VCARD"]
    for name, parameters, _type, *values in card[1]:
        written = "".join(
            f";{key.upper()}={','.join(value) if isinstance(value, list) else value}"
            for key, value in parameters.items()
        )
        lines.append(f"{name.upper()}{written}:{','.join(map(_vcard_value, values))}")
    lines.append("END:VCARD")
    return "\r\n".join(map(_folded, lines)) + "\r\n"


def _folded(line: str) -> str:
    """A line of vCard text folded as sabre/vobject folds it: after its first 75 bytes, then after
    each 74 bytes that follow the space a folded line starts with, never inside a character."""
    data, parts, start, size = line.encode(), [], 0, 75
    while len(data) - start > size:
        end = start + size
        # A byte that continues a character goes with it to the next line
        while data[end] & 0xC0 == 0x80:
            end -= 1
        parts.append(data[start:end])
        start, size = end, 74
    parts.append(data[start:])
    return "\r\n ".join(part.decode() for part in parts)


def _vcard_value(value: Any) -> str:
    """A value as vCard writes it: a structured one, such as a name or an address, as its
    components separated by semicolons, the values of each separated by commas."""
    if isinstance(value, list):
        return ";".join(
            ",".join(map(_vcard_escaped, part)) if isinstance(part, list) else _vcard_escaped(part)
            for part in value
        )
    return _vcard_escaped(value)


def _vcard_escaped(value: Any) -> str:
    text = str(value)
    for character, escaped in (("\\", "\\\\"), (",", "\\,"), (";", "\\;"), ("\n", "\\n")):
        text = text.replace(character, escaped)
    return text


def is_contact(card: Any) -> bool:
    """Whether esn-sabre would keep this jCard as a contact: a vCard 4.0 with a UID and a formatted
    name, its properties each a name, parameters, a type and a value."""
    if not (isinstance(card, list) and len(card) == 2 and card[0] == "vcard"):
        return False
    properties = card[1]
    if not isinstance(properties, list) or not all(
        isinstance(prop, list)
        and len(prop) >= 4
        and isinstance(prop[0], str)
        and isinstance(prop[1], dict)
        and isinstance(prop[2], str)
        for prop in properties
    ):
        return False
    names = [prop[0] for prop in properties]
    return (
        ["version", {}, "text", "4.0"] in properties
        and names.count("version") == 1
        and "uid" in names
        and "fn" in names
    )


def _etag(card: list[Any]) -> str:
    return '"' + hashlib.md5(vcard_text(card).encode()).hexdigest() + '"'


def _accepted(request: httpx.Request) -> list[str]:
    return [kind.strip() for kind in request.headers.get("accept", "").split(",")]


@dataclass
class FakeAddressBook:
    """An address book of esn-sabre, in the home of a user or of a domain. A delegation or a
    subscription in a user's home shows the cards of someone else's book, its source."""

    home: str
    name: str
    display_name: str = ""
    description: str = ""
    cards: dict[str, list[Any]] = field(default_factory=dict)
    """Its cards by name, such as 1a2b.vcf, in jCard of the vCard version their writer gave, which
    esn-sabre keeps."""
    privileges: list[str] = field(default_factory=lambda: ["dav:read", "dav:write"])
    """What its owner may do in it, as esn-sabre lists it in dav:acl: read, and write by default."""
    group: bool = False
    """Whether it is a book of a domain, which the domain's members read."""
    members_write: bool = False
    """Whether the domain lets its members write in it: never in domain-members, which only
    technical tokens write."""
    disabled: bool = False
    source: "FakeAddressBook | None" = None
    access: int | None = None
    """The share access of a delegation."""
    invite_status: int = INVITE_ACCEPTED
    subscribed: bool = False
    """Whether it is a subscription to a published book."""
    publicly_writable: bool = False
    """Whether the book a subscription shows is published in write."""

    @property
    def shown(self) -> dict[str, list[Any]]:
        """The cards it shows: those of its source, for a delegation or a subscription."""
        return self.source.cards if self.source is not None else self.cards


class FakeContacts:
    """Twake Contacts, as the Calendar side service gives it to the contracts: esn-sabre's address
    books behind the side service's /dav proxy, which acts as the bearer and forwards neither
    If-Match nor If-None-Match, and the side service's search across several address books.

    The fake answers JSON when Accept lists application/json, or for a card
    application/vcard+json, in jCard of vCard 4.0. It takes a card in jCard, a body that starts
    with "[", up to what nginx takes in front of esn-sabre, and reads URLs without .json, as
    esn-sabre does. Its search matches .*<query>.* against the vCard text of each card of each
    book, whatever the case, leaves out the books the bearer cannot read, and keeps the first
    cards by name.

    A user reads the address books of their home: those they own, the delegations accepted in it
    and their subscriptions, which show someone else's book; and the books of their domain. They
    write in the books they own, through a delegation of share access 3 or 5, or a subscription to
    a book published in write, and in their domain's books only where it lets its members write,
    never in domain-members. Their home gets its contacts and collected books when first listed.
    """

    def __init__(self, calendar: "FakeCalendar") -> None:
        self._calendar = calendar
        self.books: dict[tuple[str, str], FakeAddressBook] = {}
        self.body_limit = SABRE_BODY_LIMIT
        self.failing_writes: Literal["landed", "lost", "refused"] | None = None
        """How a write or a delete of a card fails, if it does: "landed", kept, but answered after
        the service gave up waiting; "lost", refused with a 503 and not kept; "refused", with the
        403 esn-sabre answers a user without the right to write."""
        self.answers: dict[str, httpx.Response] = {}
        """What to answer every request whose path starts with a key, whatever it asks, such as
        an answer in an unexpected form."""
        self.writes: list[tuple[str, str]] = []
        """The method and the path, under /addressbooks, of each card written or deleted."""
        self.searches: list[dict[str, Any]] = []
        """The body of each search."""

    def book(self, home: str, name: str, **more: Any) -> FakeAddressBook:
        book = FakeAddressBook(home, name, **more)
        self.books[(home, name)] = book
        return book

    def owners(self, name: str = "contacts", **more: Any) -> FakeAddressBook:
        """A book the owner owns, such as their contacts book, which esn-sabre creates."""
        return self.books.get((MMAUDET_CALENDAR_ID, name)) or self.book(
            MMAUDET_CALENDAR_ID, name, **more
        )

    def handle(self, request: httpx.Request, caller: str) -> httpx.Response:
        user = self._calendar.users.get(caller)
        domain = self._calendar.domains.get(caller)
        # esn-sabre knows no such user
        if user is None:
            return httpx.Response(401)
        for start, answer in self.answers.items():
            if request.url.path.startswith(start):
                return answer
        if request.url.path == "/contacts/api/contacts/search":
            if request.method != "POST":
                return httpx.Response(405)
            return self._search(request, user, domain)
        # The fake reads the URL without .json, its query string too
        target = request.url.raw_path.decode().replace(".json", "")
        path, _, query = target.partition("?")
        parts = [unquote(part) for part in path.removeprefix("/dav/addressbooks/").split("/")]
        if len(parts) == 1 and request.method == "GET":
            return self._list(request, parts[0], parse_qs(query), user, domain)
        if len(parts) == 2 and request.method == "GET":
            return self._contacts(request, parts[0], parts[1], parse_qs(query), user, domain)
        if len(parts) == 3:
            return self._card(request, parts[0], parts[1], parts[2], user, domain)
        return httpx.Response(404)

    def _reads(self, book: FakeAddressBook, user: str, domain: str | None) -> bool:
        return book.home == user or (book.group and book.home == domain)

    def _writes(self, book: FakeAddressBook, user: str, domain: str | None) -> bool:
        if book.group:
            return book.home == domain and book.members_write and book.name != "domain-members"
        if book.home != user:
            return False
        if book.access is not None:
            return book.access in (READ_WRITE_ACCESS, ADMINISTRATION_ACCESS)
        return book.publicly_writable if book.subscribed else "dav:write" in book.privileges

    def _list(
        self,
        request: httpx.Request,
        home: str,
        query: dict[str, list[str]],
        user: str,
        domain: str | None,
    ) -> httpx.Response:
        if "application/json" not in _accepted(request):
            return httpx.Response(406)
        if home == user:
            for name in ("contacts", "collected"):
                self.books.setdefault((home, name), FakeAddressBook(home, name))
        # The home of a domain lets its members alone read it
        elif home in self._calendar.domains.values() and home != domain:
            return httpx.Response(403)

        def asked(option: str) -> bool:
            return query.get(option) == ["true"]

        listed = []
        for book in self.books.values():
            if book.home != home:
                continue
            if book.group:
                shown = asked("personal") and not book.disabled
            elif book.subscribed:
                shown = asked("subscribed")
            elif book.access is not None:
                status = query.get("inviteStatus")
                shown = asked("shared") and (status is None or status == [str(book.invite_status)])
            else:
                shown = asked("personal")
            if shown:
                listed.append(self._described(book, asked("contactsCount")))
        if not listed and home != user and home != domain:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "_links": {"self": {"href": f"/addressbooks/{home}.json"}},
                "_embedded": {"dav:addressbook": listed},
            },
        )

    def _contacts(
        self,
        request: httpx.Request,
        home: str,
        name: str,
        query: dict[str, list[str]],
        user: str,
        domain: str | None,
    ) -> httpx.Response:
        """The cards of a book, a page of them, by their names, as esn-sabre lists them with an
        offset and a limit, each in jCard of the vCard version it was written in."""
        if "application/json" not in _accepted(request):
            return httpx.Response(406)
        book = self.books.get((home, name))
        if book is None:
            return httpx.Response(404)
        if not self._reads(book, user, domain):
            return httpx.Response(403)
        offset = int(query.get("offset", ["0"])[0])
        limit = int(query.get("limit", ["0"])[0])
        cards = sorted(book.shown.items())
        page = cards[offset : offset + limit] if limit else cards[offset:]
        listed: dict[str, Any] = {
            "_links": {"self": {"href": f"/addressbooks/{home}/{name}.json"}},
            "_embedded": {
                "dav:item": [
                    {
                        "_links": {"self": {"href": f"/addressbooks/{home}/{name}/{card_name}"}},
                        "etag": _etag(card),
                        "data": card,
                    }
                    for card_name, card in page
                ]
            },
        }
        if limit and offset + limit < len(cards):
            following = f"/addressbooks/{home}/{name}.json?offset={offset + limit}&limit={limit}"
            listed["_links"]["next"] = {"href": following}
        return httpx.Response(200, json=listed)

    def _described(self, book: FakeAddressBook, counted: bool) -> dict[str, Any]:
        """An address book as esn-sabre lists it: its own books and those of a domain share it as
        their owner and count their contacts, a delegation comes with its share access, a
        subscription with none."""
        source = book.source
        described: dict[str, Any] = {
            "_links": {"self": {"href": f"/addressbooks/{book.home}/{book.name}.json"}},
            "dav:name": book.display_name,
            "carddav:description": book.description,
            "dav:acl": (["dav:read", "dav:write"] if book.members_write else ["dav:read"])
            if book.group
            else book.privileges,
            "dav:share-access": None if book.subscribed else book.access or 1,
            "openpaas:subscription-type": "public"
            if book.subscribed
            else "delegation"
            if book.access is not None
            else None,
            "type": "",
            "state": "",
            # Counted by its own books and its domain's alone
            "numberOfContacts": len(book.cards) if counted and source is None else None,
            "acl": [],
            "dav:group": f"principals/domains/{book.home}" if book.group else None,
        }
        if source is not None:
            described["openpaas:source"] = f"/addressbooks/{source.home}/{source.name}.json"
        return described

    def _card(
        self,
        request: httpx.Request,
        home: str,
        name: str,
        card_name: str,
        user: str,
        domain: str | None,
    ) -> httpx.Response:
        book = self.books.get((home, name))
        if book is None:
            return httpx.Response(404)
        if not self._reads(book, user, domain):
            return httpx.Response(403)
        cards = book.shown
        if request.method == "GET":
            if "application/vcard+json" not in _accepted(request):
                return httpx.Response(406)
            found = cards.get(card_name)
            if found is None:
                return httpx.Response(404)
            # Converted to vCard 4.0, as sabre/dav gives jCard
            converted = [
                ["version", {}, "text", "4.0"] if prop[0] == "version" else prop
                for prop in found[1]
            ]
            return httpx.Response(
                200,
                json=["vcard", converted],
                headers={"ETag": _etag(found), "Content-Type": "application/vcard+json"},
            )
        if request.method not in ("PUT", "DELETE"):
            return httpx.Response(405)
        if not self._writes(book, user, domain) or self.failing_writes == "refused":
            return httpx.Response(403)
        written = f"/addressbooks/{home}/{name}/{card_name}"
        if request.method == "DELETE":
            if card_name not in cards:
                return httpx.Response(404)
            del cards[card_name]
            self.writes.append(("DELETE", written))
            return httpx.Response(204)
        if len(request.content) > self.body_limit:
            return httpx.Response(413)
        # sabre/dav reads jCard from a body that starts with "["
        if not request.content.startswith(b"["):
            return httpx.Response(415)
        card = json.loads(request.content)
        if not is_contact(card):
            return httpx.Response(415)
        if self.failing_writes == "lost":
            return httpx.Response(503)
        created = card_name not in cards
        cards[card_name] = card
        self.writes.append(("PUT", written))
        if self.failing_writes == "landed":
            raise httpx.ReadTimeout("Contacts answered too late", request=request)
        return httpx.Response(201 if created else 204)

    def _search(self, request: httpx.Request, user: str, domain: str | None) -> httpx.Response:
        try:
            limit = int(request.url.params.get("limit", "30"))
            offset = int(request.url.params.get("offset", "0"))
            body = json.loads(request.content)
        except ValueError:
            return httpx.Response(400)
        if limit < 1 or offset < 0 or not isinstance(body, dict):
            return httpx.Response(400)
        # The side service reads exactly these two members
        if not set(body) <= {"query", "addressBooks"}:
            return httpx.Response(400)
        self.searches.append(body)
        books: list[tuple[str, str]] = []
        for ref in body.get("addressBooks") or []:
            if not isinstance(ref, dict) or set(ref) != {"userId", "addressBookId"}:
                return httpx.Response(400)
            segments = (ref["userId"], ref["addressBookId"])
            if not all(
                isinstance(segment, str)
                and SEARCH_SEGMENT.fullmatch(segment)
                and segment not in (".", "..")
                for segment in segments
            ):
                return httpx.Response(400)
            if segments not in books:
                books.append(segments)
        # The fake reads the query without .json, as it reads URLs
        query = str(body.get("query") or "").replace(".json", "")
        try:
            pattern = re.compile(".*" + query + ".*", re.IGNORECASE)
        except re.error:
            # A query the fake cannot compile answers 502
            return httpx.Response(502)
        found: list[dict[str, Any]] = []
        for home, name in books:
            book = self.books.get((home, name))
            if book is None or not self._reads(book, user, domain):
                continue
            hits = [
                (card_name, card)
                for card_name, card in sorted(book.shown.items())
                if pattern.search(vcard_text(card))
            ]
            found += [
                {
                    "_links": {"self": {"href": f"/addressbooks/{home}/{name}/{card_name}"}},
                    "etag": _etag(card),
                    "data": card,
                }
                for card_name, card in hits[: offset + limit]
            ]
        return httpx.Response(200, json={"_embedded": {"dav:item": found[offset : offset + limit]}})


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
    """Synapse's client API behind the gateway's outbound route, which admits the contracts'
    consumer alone, by its key, then adds the token of the contracts' application service: each
    request acts as the user that user_id names, who must have an account.

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
        # As APISIX's key-auth does, in its default header, before anything reaches Synapse
        key = request.headers.get("apikey")
        if key != CHAT_GATEWAY_KEY:
            message = "Missing API key in request" if key is None else "Invalid API key in request"
            return httpx.Response(401, json={"message": message})
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
SUBMISSION = "urn:ietf:params:jmap:submission"
SHARES = "urn:apache:james:params:jmap:mail:shares"
# The capability a method needs beyond core and mail, as James checks it
NEEDS = {"Identity/get": SUBMISSION}

MMAUDET = email_of("mmaudet")
# mmaudet's own mailboxes, as TMail creates them
INBOX = "mbx-inbox"
TRASH = "mbx-trash"
SPAM = "mbx-spam"
# Not there unless a test puts them there
DRAFTS = "mbx-drafts"
SENT = "mbx-sent"

# What Email/set takes of an email it creates, as James does, but for attachments and headers
CREATION = {
    "mailboxIds",
    "messageId",
    "references",
    "inReplyTo",
    "from",
    "to",
    "cc",
    "bcc",
    "sender",
    "replyTo",
    "subject",
    "sentAt",
    "keywords",
    "receivedAt",
    "htmlBody",
    "textBody",
    "bodyValues",
}


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


def _present(properties: dict[str, Any]) -> dict[str, Any]:
    """What James gives of an object: the properties it has a value for, leaving the others out
    rather than writing them null."""
    return {key: value for key, value in properties.items() if value is not None}


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
    sorts by receivedAt, newest first, and refuses what it does not know. Identity/get, which
    needs the submission capability, gives the addresses the user sends from. Email/set creates
    emails in the user's own mailboxes only, updates the mailboxes of any email the user may read,
    as a whole or by patch, destroys none, and refuses what it does not know.
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
        self.identities: dict[str, list[dict[str, Any]]] = {
            MMAUDET: [{"id": "identity-mmaudet", "name": "Michel-Marie", "email": MMAUDET}]
        }
        """The identities of each user, by username, as Identity/get gives them."""
        self.created: list[str] = []
        """The ids of the emails Email/set created, in order."""
        self.refused_creation: str | None = None
        """The error Email/set answers to a creation instead, such as overQuota."""
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
        self.refused_update: str | None = None
        """The type of the error Email/set answers each update with, when a test says so."""
        self.refused_updates: dict[str, str] = {}
        """The type of the error Email/set answers the update of each of these emails with, by
        id, when a test says so: of several emails, TMail may refuse some and move the others."""
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
            "messageId": [f"{email_id}@twake.test"],
            "inReplyTo": None,
            "references": None,
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
            "capabilities": {CORE: {}, MAIL: {}, SUBMISSION: {}, SHARES: {}},
            "accounts": accounts,
            "primaryAccounts": {
                capability: account_of(username) for capability in (CORE, MAIL, SUBMISSION)
            },
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
                if NEEDS.get(name, CORE) not in request["using"]:
                    raise MethodError("unknownMethod")
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
        if name == "Identity/get":
            return answer | self._identities(owner, arguments)
        if name == "Email/set":
            return answer | self._set(owner, arguments)
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
                _present({key: visible[mailbox_id].get(key) for key in arguments["properties"]})
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
        return _present({key: (email | parts).get(key) for key in arguments["properties"]})

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

    def _identities(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        properties = ["id", *(arguments.get("properties") or ["name", "email"])]
        return {
            "list": [
                {key: identity[key] for key in properties}
                for identity in self.identities.get(owner, [])
            ],
            "notFound": [],
        }

    def _set(self, owner: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Email/set, which creates emails and updates their mailboxes only here: it destroys
        none."""
        if not set(arguments) <= {"accountId", "create", "update"}:
            raise MethodError("invalidArguments")
        created, not_created = self._create(owner, arguments.get("create") or {})
        updated, not_updated = self._update(owner, arguments.get("update") or {})
        return {
            "oldState": "0",
            "newState": "1",
            "created": created or None,
            "notCreated": not_created or None,
            "updated": updated or None,
            "notUpdated": not_updated or None,
        }

    def _create(
        self, owner: str, creations: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The emails created, and those that were not, by creation id."""
        created: dict[str, Any] = {}
        not_created: dict[str, Any] = {}
        for creation_id, email in creations.items():
            refusal = self.refused_creation or self._creation_refusal(owner, email)
            if refusal is not None:
                not_created[creation_id] = {"type": refusal}
                continue
            email_id = f"created-{len(self.created) + 1}"
            [part] = email["textBody"]
            body = email["bodyValues"][part["partId"]]["value"]
            self.emails[email_id] = {
                "id": email_id,
                "threadId": f"thread-{email_id}",
                "keywords": {},
                "receivedAt": "2026-10-07T10:00:00Z",
                "from": None,
                "to": None,
                "cc": None,
                "replyTo": None,
                "subject": "",
                "preview": body[:256],
                "hasAttachment": False,
                "messageId": [f"{email_id}@twake.test"],
                "inReplyTo": None,
                "references": None,
            } | {key: email[key] for key in email.keys() - {"textBody", "bodyValues"}}
            self.emails[email_id]["body"] = body
            self.created.append(email_id)
            created[creation_id] = {
                "id": email_id,
                "blobId": f"blob-{email_id}",
                "threadId": f"thread-{email_id}",
                "size": len(body.encode()),
            }
        return created, not_created

    def _update(
        self, owner: str, updates: dict[str, Any]
    ) -> tuple[dict[str, None], dict[str, dict[str, str]]]:
        """The emails updated, and those that were not, by id: their mailboxes only."""
        updated: dict[str, None] = {}
        not_updated: dict[str, dict[str, str]] = {}
        for email_id, patch in updates.items():
            email = self.emails.get(email_id)
            if email is None or not self._readable(email, owner):
                not_updated[email_id] = {"type": "notFound"}
            elif (refused := self.refused_update or self.refused_updates.get(email_id)) is not None:
                not_updated[email_id] = {"type": refused}
            elif (mailbox_ids := self._patched(email["mailboxIds"], patch, owner)) is None:
                not_updated[email_id] = {"type": "invalidPatch"}
            else:
                email["mailboxIds"] = mailbox_ids
                updated[email_id] = None
        return updated, not_updated

    def _patched(
        self, mailbox_ids: dict[str, bool], patch: dict[str, Any], owner: str
    ) -> dict[str, bool] | None:
        """The mailboxes of an email once patched, or None for a patch James refuses: of another
        property, or that leaves the email in no mailbox or in one the user may not read."""
        patched = dict(mailbox_ids)
        for path, value in patch.items():
            mailbox = path.removeprefix("mailboxIds/")
            if path == "mailboxIds" and isinstance(value, dict):
                patched = dict(value)
            elif mailbox != path and value is True:
                patched[mailbox] = True
            elif mailbox != path and value is None:
                patched.pop(mailbox, None)
            else:
                return None
        readable = all(
            mailbox in self.mailboxes
            and owner in {self.mailboxes[mailbox].owner, *self.mailboxes[mailbox].shared_with}
            for mailbox in patched
        )
        return patched if patched and readable else None

    def _creation_refusal(self, owner: str, email: dict[str, Any]) -> str | None:
        """Why James would not create this email, or None: a property it does not take, a
        mailbox that is not the user's own, or a text body other than one text/plain part."""
        mailboxes = email.get("mailboxIds") or {}
        parts = email.get("textBody") or []
        if (
            not set(email) <= CREATION
            or not mailboxes
            or any(
                mailbox not in self.mailboxes or self.mailboxes[mailbox].owner != owner
                for mailbox in mailboxes
            )
            or len(parts) != 1
            or parts[0].get("type") != "text/plain"
            or parts[0].get("partId") not in (email.get("bodyValues") or {})
        ):
            return "invalidArguments"
        return None


ROOT_ID = "io.cozy.files.root-dir"
TRASH_ID = "io.cozy.files.trash-dir"
SHARED_WITH_ME_ID = "io.cozy.files.shared-with-me-dir"
SHARED_DRIVES_ID = "io.cozy.files.shared-drives-dir"
# The class cozy-stack gives a file, when it is not the first part of its MIME type
CLASSES = {
    "application/pdf": "pdf",
    "application/vnd.oasis.opendocument.spreadsheet": "spreadsheet",
}


@dataclass
class DriveDoc:
    """A file or folder of the owner's instance, as cozy-stack keeps it in CouchDB."""

    id: str
    type: str
    name: str
    dir_id: str
    updated_at: str = "2026-10-05T09:00:00Z"
    mime: str = ""
    content: bytes = b""
    trashed: bool = False
    encrypted: bool = False
    antivirus: str | None = None
    """The status of the antivirus scan, once the stack scanned the file."""
    sharing: str | None = None
    """The sharing whose root this is, on either side, which the stack references from it."""
    size: int | None = None
    """The size the stack keeps, the content's own unless a test says otherwise."""


@dataclass
class Link:
    """A share-by-link permission: whoever holds its code reads these files and folders, and
    what is in the folders, until it expires."""

    values: list[str]
    expires_at: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


def folder(doc_id: str, name: str, parent: str = ROOT_ID, **more: Any) -> DriveDoc:
    return DriveDoc(doc_id, "directory", name, parent, **more)


def text_file(
    doc_id: str,
    name: str,
    parent: str = ROOT_ID,
    content: bytes = b"",
    mime: str = "text/plain",
    **more: Any,
) -> DriveDoc:
    return DriveDoc(doc_id, "file", name, parent, mime=mime, content=content, **more)


MISSING = object()


def mango_matches(selector: dict[str, Any], doc: dict[str, Any]) -> bool:
    """Whether a CouchDB document matches a Mango selector, for the operators the contracts use. As
    in CouchDB, a field the document lacks matches no condition but a negated one."""
    for key, condition in selector.items():
        if key == "$or":
            if not any(mango_matches(part, doc) for part in condition):
                return False
        elif not _satisfies(doc.get(key, MISSING), condition):
            return False
    return True


def _satisfies(value: Any, condition: Any) -> bool:
    if not isinstance(condition, dict):
        return value is not MISSING and value == condition
    for operator, operand in condition.items():
        if operator == "$regex":
            matched = isinstance(value, str) and re.search(operand, value) is not None
        elif operator == "$gt":
            matched = isinstance(value, str) and value > operand
        elif operator == "$nin":
            matched = value is not MISSING and value not in operand
        elif operator == "$not":
            matched = not _satisfies(value, operand)
        else:
            raise AssertionError(f"the fake knows no {operator}")
        if not matched:
            return False
    return True


class FakeDrive:
    """The owner's cozy-stack instance, as the Drive contracts reach it with the Drive token.

    Files and folders by id, under the root and the trash. GET /files/:id gives a folder with its
    items, folders first then by name, the trash left out of the root, page by page: by page[skip]
    when the request says it, else by a cursor on the key of the next item. POST /files/_all_docs
    gives items with their path. POST /files/_find evaluates a Mango selector, sorts only along an
    index made with POST /data/io.cozy.files/_index, as CouchDB does, and pages with bookmarks.
    POST /files/:dir-id creates a file, never over another item of its name. GET
    /permissions/doctype/io.cozy.files/shared-by-link pages the links. Also GET
    /files/download/:id and the capabilities.
    """

    def __init__(self) -> None:
        self.docs: dict[str, DriveDoc] = {}
        self.add(DriveDoc(ROOT_ID, "directory", "", ""), folder(TRASH_ID, ".cozy_trash"))
        self.flat_subdomains = True
        self.down = False
        self.capabilities_down = False
        """Whether the instance fails to give its capabilities, and answers the rest."""
        self.refused_tokens = False
        self.read_only_token = False
        """Whether the Drive token may read only, without POST, which the stack answers 403."""
        self.forbidden: set[str] = set()
        """Ids the Drive token may not read, which the stack answers 403."""
        self.blocked: set[str] = set()
        """Ids of the files whose download the antivirus blocks, which the stack answers 451."""
        self.free_space: int | None = None
        """The bytes left in the instance's quota, None without a quota."""
        self.trashed_meanwhile: set[str] = set()
        """Ids of the folders that someone trashes once read, before a file is written there."""
        self.links: list[Link] = []
        self.indexes: dict[str, dict[str, Any]] = {}
        """Mango indexes, by design document."""
        self.bookmarks: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.downloads: list[str] = []
        """The ids of the files whose content was downloaded, in order."""

    def add(self, *docs: DriveDoc) -> None:
        for doc in docs:
            self.docs[doc.id] = doc

    def path_of(self, doc: DriveDoc) -> str:
        if doc.id == ROOT_ID:
            return "/"
        return posixpath.join(self.path_of(self.docs[doc.dir_id]), doc.name)

    def child(self, folder_id: str, name: str) -> DriveDoc | None:
        """The item of that name in the folder."""
        found = [doc for doc in self.docs.values() if doc.dir_id == folder_id and doc.name == name]
        return found[0] if found else None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            return httpx.Response(503)
        bearer = request.headers.get("authorization")
        if self.refused_tokens or bearer != f"Bearer {MMAUDET_DRIVE_TOKEN}":
            return httpx.Response(401)
        method, path = request.method, request.url.path
        if (method, path) == ("GET", "/settings/capabilities"):
            if self.capabilities_down:
                return httpx.Response(503)
            capabilities = {"file_versioning": True, "flat_subdomains": self.flat_subdomains}
            return httpx.Response(
                200,
                json={"data": {"id": "io.cozy.settings.capabilities", "attributes": capabilities}},
            )
        if (method, path) == ("POST", "/files/_all_docs"):
            keys = json.loads(request.content)["keys"]
            found = [self.docs[key] for key in keys if key in self.docs and key != TRASH_ID]
            return httpx.Response(200, json={"data": [self._resource(doc) for doc in found]})
        if (method, path) == ("POST", "/files/_find"):
            return self._find(json.loads(request.content))
        if (method, path) == ("POST", "/data/io.cozy.files/_index"):
            return self._define_index(json.loads(request.content))
        if (method, path) == ("GET", "/permissions/doctype/io.cozy.files/shared-by-link"):
            return self._links_page(request)
        if method == "POST" and re.fullmatch(r"/files/[^/]+", path):
            return self._create(request, path.removeprefix("/files/"))
        if method == "GET" and path.startswith("/files/download/"):
            return self._download(path.removeprefix("/files/download/"))
        if method == "GET" and path.startswith("/files/"):
            return self._read(request, path.removeprefix("/files/"))
        return httpx.Response(404)

    def _couch_doc(self, doc: DriveDoc) -> dict[str, Any]:
        """The document as CouchDB keeps it, which Mango selectors read: a folder keeps its path,
        a file does not."""
        stored: dict[str, Any] = {
            "_id": doc.id,
            "type": doc.type,
            "created_at": "2026-09-01T08:00:00Z",
            "updated_at": doc.updated_at,
            "cozyMetadata": {"createdOn": f"https://{MMAUDET_INSTANCE}/", "sourceAccount": "a1"},
        }
        if doc.id != ROOT_ID:
            stored |= {"name": doc.name, "dir_id": doc.dir_id}
        if doc.sharing is not None:
            stored["referenced_by"] = [{"type": "io.cozy.sharings", "id": doc.sharing}]
        if doc.type == "directory":
            return stored | {"path": self.path_of(doc)}
        stored |= {
            "mime": doc.mime,
            "class": CLASSES.get(doc.mime, doc.mime.partition("/")[0]),
            "size": str(len(doc.content) if doc.size is None else doc.size),
            "md5sum": "ODZmYjI2OWQxOTBkMmM4NQo=",
            "trashed": doc.trashed,
            "encrypted": doc.encrypted,
            "executable": False,
            "metadata": {"gps": {"lat": 48.8566, "long": 2.3522}},
        }
        if doc.antivirus is not None:
            stored["antivirus_scan"] = {"status": doc.antivirus}
        return stored

    def _resource(self, doc: DriveDoc, *, with_path: bool = True) -> dict[str, Any]:
        """The document in the stack's JSON:API: with the path of a file only when asked, its
        references as a relationship, which only a folder also keeps among its attributes, and
        the links to its thumbnails."""
        attributes = {
            key: value for key, value in self._couch_doc(doc).items() if not key.startswith("_")
        }
        references = attributes.get("referenced_by", [])
        if doc.type == "file":
            attributes.pop("referenced_by", None)
        if with_path:
            attributes["path"] = self.path_of(doc)
        return {
            "type": "io.cozy.files",
            "id": doc.id,
            "attributes": attributes,
            "meta": {"rev": "1-6c3f2b"},
            "relationships": {"referenced_by": {"data": references}},
            "links": {
                "self": f"/files/{doc.id}",
                "small": f"/files/{doc.id}/thumbnails/0f9cda56674282ac/small",
            },
        }

    def _in_trash(self, doc: DriveDoc) -> bool:
        path = self.path_of(doc)
        return path == "/.cozy_trash" or path.startswith("/.cozy_trash/")

    def _create(self, request: httpx.Request, dir_id: str) -> httpx.Response:
        """A new file in the folder, as POST /files/:dir-id?Type=file&Name= makes it from the
        request's content: with the mime of its Content-Type, which must not have changed on the
        way when the request gives its Content-MD5."""
        if self.read_only_token:
            return httpx.Response(403)
        parent = self.docs.get(dir_id)
        if (
            parent is None
            or parent.type != "directory"
            or self._in_trash(parent)
            or dir_id in self.trashed_meanwhile
        ):
            return httpx.Response(404)
        name = request.url.params.get("Name", "")
        if (
            request.url.params.get("Type") != "file"
            or name in ("", ".", "..")
            or any(character in name for character in "/\x00\n\r")
        ):
            return httpx.Response(422)
        if self.child(dir_id, name) is not None:
            return httpx.Response(409)
        content = request.content
        md5 = request.headers.get("content-md5")
        if md5 is not None and md5 != base64.b64encode(hashlib.md5(content).digest()).decode():
            return httpx.Response(412)
        if self.free_space is not None and len(content) > self.free_space:
            return httpx.Response(413)
        mime = request.headers.get("content-type", "").partition(";")[0].strip()
        doc = text_file(uuid.uuid4().hex, name, dir_id, content=content, mime=mime)
        self.add(doc)
        return httpx.Response(201, json={"data": self._resource(doc, with_path=False)})

    def _links_page(self, request: httpx.Request) -> httpx.Response:
        """A page of the share-by-link permissions on files, 30 by default and 100 at most, with
        their codes, and a cursor to the next page, as the stack gives them."""
        limit = min(int(request.url.params.get("page[limit]", "30")), 100)
        cursor = request.url.params.get("page[cursor]")
        ids = [link.id for link in self.links]
        start = ids.index(json.loads(cursor)[1]) if cursor else 0
        page = self.links[start : start + limit]
        links = {}
        if start + limit < len(self.links):
            next_cursor = json.dumps([["io.cozy.files", "share"], ids[start + limit]])
            query = httpx.QueryParams({"page[limit]": limit, "page[cursor]": next_cursor})
            links["next"] = f"/permissions/doctype/io.cozy.files/shared-by-link?{query}"
        data = [
            {
                "type": "io.cozy.permissions",
                "id": link.id,
                "attributes": {
                    "type": "share",
                    "permissions": {
                        "files": {"type": "io.cozy.files", "verbs": ["GET"], "values": link.values}
                    },
                    "codes": {"email": "a-secret-code"},
                    "shortcodes": {"email": "abcdeFGHIJ01"},
                }
                | ({"expires_at": link.expires_at} if link.expires_at else {}),
                "meta": {"rev": "1-d46b63"},
                "links": {"self": f"/permissions/{link.id}"},
            }
            for link in page
        ]
        return httpx.Response(200, json={"data": data, "links": links})

    def _read(self, request: httpx.Request, doc_id: str) -> httpx.Response:
        doc = self.docs.get(doc_id)
        if doc is None:
            return httpx.Response(404)
        if doc_id in self.forbidden:
            return httpx.Response(403)
        if doc.type == "file":
            return httpx.Response(200, json={"data": self._resource(doc, with_path=False)})
        params = request.url.params
        limit = int(params.get("page[limit]", "30"))
        items = sorted(
            (item for item in self.docs.values() if item.dir_id == doc_id and item.id != TRASH_ID),
            key=lambda item: (item.type, item.name),
        )
        # As the stack pages: by skip when the request says page[skip], else by the key of the
        # next item, its name included, which the next link carries in page[cursor]
        if "page[skip]" in params:
            start = int(params["page[skip]"])
        elif "page[cursor]" in params:
            following = json.loads(params["page[cursor]"])[1]
            start = [item.id for item in items].index(following)
        else:
            start = 0
        page = items[start : start + limit]
        links = {}
        if start + limit < len(items):
            if "page[skip]" in params:
                query = httpx.QueryParams({"page[limit]": limit, "page[skip]": start + limit})
            else:
                after = items[start + limit]
                cursor = json.dumps([[doc_id, after.type, after.name], after.id])
                query = httpx.QueryParams({"page[limit]": limit, "page[cursor]": cursor})
            links["next"] = f"/files/{doc_id}?{query}"
        return httpx.Response(
            200,
            json={
                "data": self._resource(doc, with_path=False),
                "included": [self._resource(item, with_path=False) for item in page],
                "links": links,
            },
        )

    def _find(self, body: dict[str, Any]) -> httpx.Response:
        index = self.indexes.get(body.get("use_index", ""))
        sort = [(name, order) for part in body.get("sort", []) for name, order in part.items()]
        # CouchDB sorts only along an index, here the one the request names
        if sort and (index is None or [name for name, _ in sort] != index["index"]["fields"]):
            return httpx.Response(400, json={"error": "no_usable_index"})
        bookmark = body.get("bookmark")
        if bookmark is not None and bookmark not in self.bookmarks:
            return httpx.Response(400, json={"error": "invalid_bookmark"})
        partial = index["index"].get("partial_filter_selector", {}) if index else {}
        found = [
            doc
            for doc in self.docs.values()
            if mango_matches(partial, self._couch_doc(doc))
            and mango_matches(body["selector"], self._couch_doc(doc))
        ]
        if sort:
            assert sort == [("updated_at", "desc")], sort
            found.sort(key=lambda doc: doc.updated_at, reverse=True)
        start = self.bookmarks.get(bookmark, 0) if bookmark else 0
        limit = body.get("limit", 100)
        page = found[start : start + limit]
        links = {}
        # As the stack does, a next page whenever this one is full
        if len(page) >= limit:
            next_bookmark = f"g1AAAAB{len(self.bookmarks)}x"
            self.bookmarks[next_bookmark] = start + limit
            links["next"] = f"/files/_find?page[cursor]={next_bookmark}"
        return httpx.Response(
            200,
            json={
                "data": [self._resource(doc) for doc in page],
                "links": links,
                "meta": {"count": start + len(page)},
            },
        )

    def _define_index(self, definition: dict[str, Any]) -> httpx.Response:
        name = hashlib.sha1(json.dumps(definition["index"], sort_keys=True).encode()).hexdigest()
        design = f"_design/{name}"
        result = "exists" if design in self.indexes else "created"
        self.indexes[design] = definition
        return httpx.Response(200, json={"result": result, "id": design, "name": name})

    def _download(self, doc_id: str) -> httpx.Response:
        doc = self.docs.get(doc_id)
        if doc is None or doc.type != "file":
            return httpx.Response(404)
        if doc_id in self.forbidden:
            return httpx.Response(403)
        if doc_id in self.blocked:
            return httpx.Response(451, json={"errors": [{"code": "antivirus_blocked"}]})
        self.downloads.append(doc_id)
        return httpx.Response(200, content=doc.content, headers={"Content-Type": doc.mime})


TASKS_TODAY = "2026-10-07"
"""Today for Tasks, in every time zone it knows."""
TASKS_ZONES = {"Europe/Paris", "UTC"}
SEARCH_LIMIT = 50
"""The most results a search of Tasks gives."""


def tasks_id(name: str) -> str:
    """The UUID Tasks gives a person, a board or a task, the same for the same name."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"tasks:{name}"))


@dataclass(frozen=True)
class TasksMember:
    """A member of a project: their user id in Tasks, the email they joined with, their role."""

    user_id: str
    email: str
    role: str = "editor"
    name: str | None = None
    """The name they chose, else the one they last signed in with; None for someone who never
    signed in. Tasks gives it with each person since 0.2.10."""

    def person(self) -> dict[str, Any]:
        """The member, as Tasks gives a person: an assignee, an author or a member of a board."""
        return {"userId": self.user_id, "email": self.email, "name": self.name, "avatar": None}


def tasks_member(uid: str, role: str = "editor") -> TasksMember:
    """The person of that uid, a member of a project, by the email of their token."""
    return TasksMember(tasks_id(email_of(uid)), email_of(uid), role, uid.capitalize())


@dataclass
class TasksBoard:
    """A board, and the members of its project."""

    id: str
    name: str
    key_prefix: str
    members: list[TasksMember]
    project: str = "Website"
    inbox: bool = False
    managed: bool = False
    """Whether its project is a Twake Space's."""
    organization: str | None = "linagora"
    """The org_id of its project, None outside any organization, as for a personal account."""
    archived: bool = False
    sections: list[dict[str, str]] = field(default_factory=list)
    """Its sections in order, each with its id, name and category."""
    labels: dict[str, str] = field(default_factory=dict)
    """The names of its labels, by id."""
    project_id: str = ""
    """The id of its project: by default, that of every project of that name."""

    def __post_init__(self) -> None:
        self.project_id = self.project_id or tasks_id(f"project {self.project}")


@dataclass
class TasksTask:
    id: str
    board: str
    number: int
    title: str
    description: str = ""
    assignees: list[TasksMember] = field(default_factory=list)
    priority: int | None = None
    due_date: str | None = None
    due_time: str | None = None
    due_zone: str | None = None
    deadline: str | None = None
    section_id: str | None = None
    parent_id: str | None = None
    labels: list[str] = field(default_factory=list)
    """The ids of its labels."""
    recurrence: dict[str, Any] | None = None
    state: str = "open"
    """open, completed or canceled."""
    hidden: bool = False
    """Archived or in the trash: no board and no list shows it."""
    comments: list[dict[str, Any]] = field(default_factory=list)
    """As Tasks gives them, oldest first."""


@dataclass(frozen=True)
class TasksPerson:
    """Whom Tasks acts for: the uuid, the org_id and the email LemonLDAP-NG gives it for the
    token."""

    user_id: str
    organization: str | None
    """None for a personal account, which Tasks takes a user without org_id for."""
    email: str


class FakeTasks:
    """Twake Tasks 0.2.10, as the contracts call its REST API with the bearer's token.

    Tasks acts for the person of the token, by the uuid and org_id LemonLDAP-NG gives it, here
    derived from the token's subject. A board shows only to the members of its project, in their
    organization or, for a personal account, outside any: any other answers 404
    {"error": "not_found"}, as an unknown one does. The agenda, the tasks assigned to the
    person and a search list tasks of every board they are a member of, archived and trashed
    tasks left out; a description and comments are read by the task's id on its board.

    Boards are listed only as opening the web app lists them, which first sets up the person's
    Inbox if they have none, and makes them a member of the projects they were invited to.

    An editor or an admin of a board that is not archived creates a task, changes its fields,
    completes it outside sections, and moves it to a section. A field Tasks would ignore is
    refused here, so that a contract never sends one.
    """

    def __init__(self) -> None:
        self.boards: dict[str, TasksBoard] = {}
        self.tasks: dict[str, TasksTask] = {}
        self.organizations: dict[str, str | None] = {}
        """The org_id of each person, by email: linagora unless set."""
        self.invitations: dict[str, list[tuple[str, str]]] = {}
        """By email, the projects a person was invited to, with the role offered."""
        self.down = False
        """Whether Tasks answers 503, as while it cannot check tokens."""
        self.unreachable = False
        self.refused_tokens = False
        self.unexpected = False
        """Whether Tasks answers in a form the contracts do not know."""
        self.requests: list[tuple[str, dict[str, str]]] = []
        """The paths asked, with their query."""
        self.writes: list[tuple[str, str, Any]] = []
        """The writes received, in order: method, path and JSON body, refused ones included."""
        self.failing: dict[str, int] = {}
        """By method, the error status Tasks answers with, as when it fails, or when the board
        changes, between two calls."""
        self.unreadable_after_write = False
        """Whether Tasks answers 503 to a read once it took a write, as when it fails right after
        one."""
        self.kept_prefixes = {"INBOX"}
        """The key prefixes Tasks keeps for its own boards, such as INBOX for every Inbox, which
        it refuses for a new board."""

    def board(self, name: str, key_prefix: str, *members: TasksMember, **more: Any) -> TasksBoard:
        """Arranges a board of that name, with the members of its project."""
        board = TasksBoard(tasks_id(name), name, key_prefix, list(members), **more)
        self.boards[board.id] = board
        return board

    def task(self, board: TasksBoard, title: str, **more: Any) -> TasksTask:
        """Arranges a task on the board, numbered after the board's others."""
        number = 1 + sum(1 for task in self.tasks.values() if task.board == board.id)
        task = TasksTask(tasks_id(f"{board.key_prefix}-{number}"), board.id, number, title, **more)
        self.tasks[task.id] = task
        return task

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("Tasks is unreachable", request=request)
        if self.down:
            return httpx.Response(503, json={"error": "unavailable"})
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            subject = jwt.decode(bearer, options={"verify_signature": False})["sub"]
        except jwt.InvalidTokenError:
            return httpx.Response(401, json={"error": "unauthorized"})
        if self.refused_tokens:
            return httpx.Response(401, json={"error": "unauthorized"})
        if self.unexpected:
            return httpx.Response(200, json={"items": []})
        self.requests.append((request.url.path, dict(request.url.params)))
        if request.method != "GET":
            self.writes.append(
                (request.method, request.url.path, json.loads(request.content or b"null"))
            )
        if request.method in self.failing:
            status = self.failing[request.method]
            return httpx.Response(status, json={"error": _ERRORS[status]})
        if self.unreadable_after_write and request.method == "GET" and self.writes:
            return httpx.Response(503, json={"error": "unavailable"})
        person = TasksPerson(
            tasks_id(subject), self.organizations.get(subject, "linagora"), subject
        )
        params = request.url.params
        if request.method == "POST" and request.url.path == "/api/boards":
            return self._new_board(self.writes[-1][2], person, subject)
        if request.method != "GET":
            return self._write(request.method, request.url.path, self.writes[-1][2], person)
        if request.url.path == "/api/boards":
            self._welcome(person, subject)
            listed = sorted(
                (board for board in self.boards.values() if self._role(board, person)),
                key=lambda board: (not board.inbox, board.name),
            )
            return httpx.Response(200, json={"boards": [self._listed(b, person) for b in listed]})
        if request.url.path == "/api/projects":
            return httpx.Response(200, json={"projects": self._projects(person)})
        if request.url.path == "/api/my-tasks":
            return httpx.Response(
                200,
                json={
                    "tasks": self._tasks_of(
                        person, lambda t, b: self._open(t, b) and _assigned(t, person)
                    )
                },
            )
        if request.url.path == "/api/agenda":
            return self._agenda(person, params.get("zone"), params.get("days", ""))
        if request.url.path == "/api/search":
            return self._search(person, params.get("q", "").strip())
        return self._board_read(request.url.path, person)

    def _new_board(self, body: Any, person: TasksPerson, email: str) -> httpx.Response:
        """A board outside any project, which starts a project of its own, named after it, with
        the person as its admin and the sections Tasks gives a new board."""
        if not isinstance(body, dict) or set(body) != {"name", "keyPrefix"}:
            return _refused("invalid_request")
        name = body["name"].strip() if isinstance(body["name"], str) else ""
        prefix = body["keyPrefix"]
        if not 1 <= len(name) <= 100 or not re.fullmatch(r"[A-Z][A-Z0-9]{0,9}", str(prefix)):
            return _refused("invalid_request")
        if prefix in self.kept_prefixes:
            return httpx.Response(409, json={"error": "key_prefix_taken"})
        number = len(self.boards)
        board = TasksBoard(
            tasks_id(f"board {number} {name}"),
            name,
            prefix,
            [TasksMember(person.user_id, email, "admin")],
            project=name,
            organization=person.organization,
            sections=[
                {"id": tasks_id(f"section {number} {section}"), "name": section, "category": kind}
                for section, kind in (
                    ("To do", "unstarted"),
                    ("In progress", "started"),
                    ("Done", "completed"),
                )
            ],
            project_id=tasks_id(f"project {number} {name}"),
        )
        self.boards[board.id] = board
        return httpx.Response(201, json=self._board(board, person))

    def _welcome(self, person: TasksPerson, email: str) -> None:
        """What opening Tasks does first: it sets up the person's Inbox if they have none, and
        makes them a member of the projects of their organization they were invited to."""
        if not any(board.inbox and self._role(board, person) for board in self.boards.values()):
            inbox = TasksBoard(
                tasks_id(f"Inbox of {email}"),
                "Inbox",
                "INBOX",
                [TasksMember(person.user_id, email, "admin")],
                project="Personal",
                inbox=True,
                organization=person.organization,
            )
            self.boards[inbox.id] = inbox
        for project, role in self.invitations.pop(email, []):
            for board in self.boards.values():
                if board.project == project and board.organization == person.organization:
                    board.members.append(TasksMember(person.user_id, email, role))

    def _role(self, board: TasksBoard, person: TasksPerson) -> str | None:
        # Each row belongs to an organization, or to none, and shows within it only
        if board.organization != person.organization:
            return None
        return next((m.role for m in board.members if m.user_id == person.user_id), None)

    def _open(self, task: TasksTask, board: TasksBoard) -> bool:
        return task.state == "open" and not board.archived

    def _agenda(self, person: TasksPerson, zone: str | None, days: str) -> httpx.Response:
        if zone not in TASKS_ZONES or not days.isdigit() or not 1 <= int(days) <= 60:
            return httpx.Response(400, json={"error": "invalid_request"})
        end = (date.fromisoformat(TASKS_TODAY) + timedelta(days=int(days))).isoformat()

        # Overdue or due before the end, and the person's: assigned to them, or unassigned in a
        # project that is not a space's
        def due(task: TasksTask, board: TasksBoard) -> bool:
            return (
                self._open(task, board)
                and task.due_date is not None
                and task.due_date < end
                and (_assigned(task, person) or (not task.assignees and not board.managed))
            )

        return httpx.Response(
            200, json={"today": TASKS_TODAY, "tasks": self._tasks_of(person, due)}
        )

    def _search(self, person: TasksPerson, words: str) -> httpx.Response:
        if not 1 <= len(words) <= 200:
            return httpx.Response(400, json={"error": "invalid_request"})

        def matches(task: TasksTask, board: TasksBoard) -> bool:
            return f"{board.key_prefix}-{task.number}".lower().startswith(words.lower()) or any(
                words.lower() in text.lower() for text in (task.title, task.description)
            )

        found = self._tasks_of(person, matches)[:SEARCH_LIMIT]
        # Since 0.2.10, the words of the description around them, for a title that lacks them
        for item in found:
            task = self.tasks[item["id"]]
            at = task.description.lower().find(words.lower())
            item["excerpt"] = (
                task.description[max(0, at - 30) : at + len(words) + 60]
                if at >= 0 and words.lower() not in task.title.lower()
                else None
            )
        return httpx.Response(200, json={"tasks": found})

    def _board_read(self, path: str, person: TasksPerson) -> httpx.Response:
        found = re.fullmatch(r"/api/boards/([^/]+)(?:/tasks/([^/]+)/(description|comments))?", path)
        board = self.boards.get(found[1]) if found else None
        if found is None or board is None or self._role(board, person) is None:
            return httpx.Response(404, json={"error": "not_found"})
        if found[2] is None:
            return httpx.Response(200, json=self._board(board, person))
        # Unlike the board, these read an archived or trashed task too
        task = self.tasks.get(found[2])
        if task is None or task.board != board.id:
            return httpx.Response(404, json={"error": "not_found"})
        if found[3] == "description":
            return httpx.Response(200, json={"markdown": task.description, "version": 0})
        return httpx.Response(200, json={"comments": task.comments})

    def _write(self, method: str, path: str, body: Any, person: TasksPerson) -> httpx.Response:
        found = re.fullmatch(
            r"/api/boards/([^/]+)/tasks(?:/([^/]+)(?:/(complete|move|comments|assignees))?)?", path
        )
        board = self.boards.get(found[1]) if found else None
        role = self._role(board, person) if board else None
        if found is None or board is None or role is None:
            return httpx.Response(404, json={"error": "not_found"})
        # Any member comments, a viewer too, and on an archived board too
        if method == "POST" and found[3] == "comments":
            return self._comment(board, found[2], body, person)
        if role == "viewer":
            return httpx.Response(403, json={"error": "forbidden"})
        if board.archived:
            return httpx.Response(409, json={"error": "archived"})
        if method == "DELETE":
            return self._trash(board, found[2], found[3], body)
        if not isinstance(body, dict):
            return _refused("invalid_request")
        if found[2] is None:
            return self._create(board, body) if method == "POST" else httpx.Response(404)
        # Unlike the board, writes reach an archived or trashed task too
        task = self.tasks.get(found[2])
        if task is None or task.board != board.id:
            return httpx.Response(404, json={"error": "not_found"})
        match method, found[3]:
            case "PATCH", None:
                return self._edit(task, body)
            case "POST", "complete":
                return self._complete(task, body)
            case "POST", "move":
                return self._move(task, board, body)
            case "PUT", "assignees":
                return self._assign(task, board, body)
        return httpx.Response(404)

    def _comment(
        self, board: TasksBoard, task_id: str | None, body: Any, person: TasksPerson
    ) -> httpx.Response:
        """Adds a comment, by the person, to a task of the board, archived or trashed too."""
        task = self.tasks.get(task_id or "")
        if task is None or task.board != board.id:
            return httpx.Response(404, json={"error": "not_found"})
        if not isinstance(body, dict) or set(body) != {"body"} or not isinstance(body["body"], str):
            return _refused("invalid_request")
        text = body["body"].strip()
        if not 1 <= len(text) <= 10_000:
            return _refused("invalid_request")
        member = next((m for m in board.members if m.user_id == person.user_id), None)
        created = {
            "id": tasks_id(f"comment {len(task.comments)} on {task.id}"),
            "author": (member or TasksMember(person.user_id, person.email)).person()
            | {"email": person.email},
            "body": text,
            "createdAt": "2026-10-07T09:30:00.000Z",
        }
        task.comments.append(created)
        return httpx.Response(201, json={"id": created["id"], "createdAt": created["createdAt"]})

    def _trash(
        self, board: TasksBoard, task_id: str | None, then: str | None, body: Any
    ) -> httpx.Response:
        """Moves a task to the board's trash, with the subtasks the board shows at any depth."""
        task = self.tasks.get(task_id or "")
        # Unlike the other writes, it finds no archived or trashed task
        if then or body is not None or task is None or task.board != board.id or task.hidden:
            return httpx.Response(404, json={"error": "not_found"})
        hiding = [task]
        while hiding:
            hidden = hiding.pop()
            hidden.hidden = True
            hiding.extend(
                child
                for child in self.tasks.values()
                if child.parent_id == hidden.id and not child.hidden
            )
        return httpx.Response(204)

    def _assign(self, task: TasksTask, board: TasksBoard, body: dict[str, Any]) -> httpx.Response:
        """Assigns the task to these members of the board, and to no other."""
        user_ids = body.get("userIds")
        if set(body) != {"userIds"} or not isinstance(user_ids, list) or len(user_ids) > 50:
            return _refused("invalid_request")
        members = {member.user_id: member for member in board.members}
        if not all(user_id in members for user_id in user_ids):
            return _refused("invalid_assignee")
        task.assignees = [members[user_id] for user_id in dict.fromkeys(user_ids)]
        return httpx.Response(204)

    def _create(self, board: TasksBoard, body: dict[str, Any]) -> httpx.Response:
        # A task in a section, outside sections when sectionId is null, or a subtask
        if set(body) not in ({"sectionId", "title"}, {"parentId", "title"}):
            return _refused("invalid_request")
        title = body["title"].strip() if isinstance(body["title"], str) else ""
        if not 1 <= len(title) <= 500:
            return _refused("invalid_request")
        sections = {section["id"]: section for section in board.sections}
        section_id = body.get("sectionId")
        if section_id is not None and section_id not in sections:
            return _refused("invalid_section")
        parent = self.tasks.get(body["parentId"]) if "parentId" in body else None
        if "parentId" in body and (parent is None or parent.board != board.id):
            return _refused("invalid_parent")
        if parent is not None and self._depth(parent) >= 4:
            return _refused("too_deep")
        category = sections[section_id]["category"] if section_id else None
        task = self.task(
            board,
            title,
            section_id=section_id,
            parent_id=parent.id if parent else None,
            state=category if category in ("completed", "canceled") else "open",
        )
        return httpx.Response(
            201,
            json={
                "id": task.id,
                "key": f"{board.key_prefix}-{task.number}",
                "title": task.title,
                "sectionId": task.section_id,
            },
        )

    def _depth(self, task: TasksTask) -> int:
        """How many tasks the chain from this one up to its topmost parent holds."""
        depth = 1
        while task.parent_id is not None:
            task = self.tasks[task.parent_id]
            depth += 1
        return depth

    def _edit(self, task: TasksTask, body: dict[str, Any]) -> httpx.Response:
        if (
            not body
            or not set(body) <= set(_CHANGES)
            or not all(_CHANGES[name](value) for name, value in body.items())
        ):
            return _refused("invalid_request")
        if body.get("dueZone") is not None and body["dueZone"] not in TASKS_ZONES:
            return _refused("invalid_request")
        # Clearing the due date clears its time and its recurrence, and clearing the time its zone
        due_date = body.get("dueDate", task.due_date)
        due_time = None if due_date is None else body.get("dueTime", task.due_time)
        if body.get("dueTime") and due_date is None:
            return _refused("invalid_dates")
        task.title = body.get("title", task.title).strip()
        task.priority = body.get("priority", task.priority)
        task.deadline = body.get("deadline", task.deadline)
        task.due_zone = None if due_time is None else body.get("dueZone", task.due_zone)
        task.due_date, task.due_time = due_date, due_time
        if due_date is None:
            task.recurrence = None
        return httpx.Response(204)

    def _recur(self, task: TasksTask) -> bool:
        """Moves a recurring task to its next due date, as completing it does: False for any
        other task."""
        if task.recurrence is None or task.due_date is None:
            return False
        days = {"days": 1, "weeks": 7}[task.recurrence["unit"]] * task.recurrence["every"]
        task.due_date = (date.fromisoformat(task.due_date) + timedelta(days=days)).isoformat()
        return True

    def _complete(self, task: TasksTask, body: dict[str, Any]) -> httpx.Response:
        if set(body) != {"state"} or body["state"] not in ("completed", "canceled", None):
            return _refused("invalid_request")
        # A task in a section completes by moving to a completed section
        if task.section_id is not None:
            return _refused("invalid_section")
        if body["state"] == "completed" and self._recur(task):
            return httpx.Response(204)
        task.state = body["state"] or "open"
        return httpx.Response(204)

    def _move(self, task: TasksTask, board: TasksBoard, body: dict[str, Any]) -> httpx.Response:
        sections = {section["id"]: section for section in board.sections}
        if "sectionId" not in body or not set(body) <= {"sectionId", "afterId", "beforeId"}:
            return _refused("invalid_request")
        section_id = body["sectionId"]
        if (section_id is not None and section_id not in sections) or task.parent_id is not None:
            return _refused("invalid_section")
        category = sections[section_id]["category"] if section_id else None
        if category == "completed" and self._recur(task):
            return httpx.Response(204)
        task.section_id = section_id
        task.state = category if category in ("completed", "canceled") else "open"
        return httpx.Response(204)

    def _tasks_of(
        self, person: TasksPerson, which: Callable[[TasksTask, TasksBoard], bool]
    ) -> list[dict[str, Any]]:
        """The tasks shown to the person that are `which`, dated ones first, as lists give them."""
        found = [
            (task, board)
            for task in self.tasks.values()
            if not task.hidden
            and self._role(board := self.boards[task.board], person)
            and which(task, board)
        ]
        found.sort(
            key=lambda pair: (
                pair[0].due_date is None,
                pair[0].due_date or "",
                pair[0].due_time or "",
                pair[0].priority is None,
                pair[0].priority or 0,
                pair[1].name,
                pair[0].number,
            )
        )
        return [
            self._task(task, board) | {"boardId": board.id, "boardName": board.name}
            for task, board in found
        ]

    def _task(self, task: TasksTask, board: TasksBoard) -> dict[str, Any]:
        assigned = {member.user_id for member in task.assignees}
        return {
            "id": task.id,
            "key": f"{board.key_prefix}-{task.number}",
            "sectionId": task.section_id,
            "parentId": task.parent_id,
            "title": task.title,
            "priority": task.priority,
            "dueDate": task.due_date,
            "dueTime": task.due_time,
            "dueZone": task.due_zone,
            "deadline": task.deadline,
            "duration": None,
            "recurrence": task.recurrence,
            "completedAt": "2026-10-06T16:00:00.000Z" if task.state == "completed" else None,
            "canceledAt": "2026-10-06T16:00:00.000Z" if task.state == "canceled" else None,
            # Someone who left the board stays assigned, but is not shown
            "assignees": [
                member.person()
                for member in sorted(board.members, key=lambda member: member.email)
                if member.user_id in assigned
            ],
            "labels": [{"id": label, "name": board.labels[label]} for label in task.labels],
            "commentCount": len(task.comments),
        }

    def _project(self, board: TasksBoard) -> dict[str, Any]:
        return {
            "id": board.project_id,
            "name": board.project,
            "personal": board.inbox,
            "managed": board.managed,
        }

    def _projects(self, person: TasksPerson) -> list[dict[str, Any]]:
        """The projects the person is a member of, by name, as listing them reads them only."""
        found: dict[str, dict[str, Any]] = {}
        for board in self.boards.values():
            role = self._role(board, person)
            if role is not None:
                found.setdefault(board.project_id, self._project(board) | {"role": role})
        return sorted(found.values(), key=lambda project: str(project["name"]))

    def _listed(self, board: TasksBoard, person: TasksPerson) -> dict[str, Any]:
        return {
            "id": board.id,
            "name": board.name,
            "keyPrefix": board.key_prefix,
            "project": self._project(board),
            "inbox": board.inbox,
            "role": self._role(board, person),
            "archived": board.archived,
            "favorite": False,
            "openTasks": sum(
                1
                for task in self.tasks.values()
                if task.board == board.id
                and task.parent_id is None
                and task.state == "open"
                and not task.hidden
            ),
        }

    def _board(self, board: TasksBoard, person: TasksPerson) -> dict[str, Any]:
        return {
            "layout": "board",
            "defaultLayout": "board",
            "id": board.id,
            "name": board.name,
            "keyPrefix": board.key_prefix,
            "project": self._project(board),
            "inbox": board.inbox,
            "archived": board.archived,
            "version": 1,
            "role": self._role(board, person),
            "members": [
                member.person() for member in sorted(board.members, key=lambda member: member.email)
            ],
            "labels": [{"id": label, "name": name} for label, name in board.labels.items()],
            "sections": board.sections,
            "tasks": [
                self._task(task, board)
                for task in self.tasks.values()
                if task.board == board.id and not task.hidden
            ],
        }


def _assigned(task: TasksTask, person: TasksPerson) -> bool:
    return any(member.user_id == person.user_id for member in task.assignees)


# The error Tasks names each status of a refused write with
_ERRORS = {403: "forbidden", 404: "not_found", 409: "archived", 503: "unavailable"}


def _refused(error: str) -> httpx.Response:
    """Tasks' answer to a request it refuses."""
    return httpx.Response(400, json={"error": error})


def _is_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_time(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) is not None


# What each field of a task that the contracts change takes, null clearing it but the title
_CHANGES: dict[str, Callable[[Any], bool]] = {
    "title": lambda value: isinstance(value, str) and 1 <= len(value.strip()) <= 500,
    "priority": lambda value: value is None or (type(value) is int and 1 <= value <= 4),
    "dueDate": lambda value: value is None or _is_date(value),
    "dueTime": lambda value: value is None or _is_time(value),
    "dueZone": lambda value: value is None or isinstance(value, str),
    "deadline": lambda value: value is None or _is_date(value),
}


class FakeBoundary:
    """Routes the service's HTTP calls to the fakes."""

    def __init__(self) -> None:
        self.issuer = FakeIssuer()
        self.calendar = FakeCalendar()
        self.synapse = FakeSynapse()
        self.tmail = FakeTMail()
        self.drive = FakeDrive()
        self.requests: list[httpx.Request] = []
        """Every request the service sent, wherever to."""
        self.tasks = FakeTasks()
        self.contacts = self.calendar.contacts

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url == SETTINGS.jwks_url:
            return self.issuer.handle(request)
        if request.url.host == "calendar.test":
            return self.calendar.handle(request)
        if request.url.host == "gateway.test" and request.url.path.startswith("/synapse/"):
            return self.synapse.handle(request)
        if request.url.host == "tmail.test":
            return self.tmail.handle(request)
        if request.url.host == MMAUDET_INSTANCE:
            return self.drive.handle(request)
        if request.url.host == "tasks.test":
            return self.tasks.handle(request)
        return httpx.Response(404)
