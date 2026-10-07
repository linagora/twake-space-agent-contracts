"""TMail, the Twake Mail backend, called as the user with their own token through its JMAP API.

JMAP gives a token no scope: this client makes only the method calls the mail contracts need,
never any other. None uses James's shares capability, so that TMail keeps to the user's own
mailboxes, and every call goes to the user's personal account."""

import hashlib
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
SUBMISSION = "urn:ietf:params:jmap:submission"
# The capability a method needs beyond core and mail, which only the requests that call it use:
# Identity/get reads the addresses the user sends from under submission, and sends nothing
NEEDS = {"Identity/get": SUBMISSION}
# The JMAP of RFC 8620 and 8621, as James's clients ask for it
JMAP_JSON = "application/json;jmapVersion=rfc-8621"
# What a JMAP id is made of (RFC 8620): TMail is never asked for anything else
JMAP_ID = r"^[A-Za-z0-9_-]{1,255}$"

BODY_BYTES = 32 * 1024
"""How much of an email's text read_email gives, at most."""
THREAD_BODY_BYTES = 8 * 1024
"""How much of each email's text read_thread gives, at most."""
# How much of the rest of what others wrote comes back, at most: characters of a subject or a
# preview, of a name and of an address, and the addresses of a header
LONGEST_LINE = 1000
LONGEST_NAME = 200
LONGEST_ADDRESS = 320
MOST_ADDRESSES = 100

MAILBOX_PROPERTIES = ["id", "name", "parentId", "role", "totalEmails", "unreadEmails"]
# What TMail itself tells of an email, then what other people wrote of it
FACTS = ["id", "threadId", "mailboxIds", "keywords", "receivedAt", "hasAttachment"]
SUMMARY_PROPERTIES = [*FACTS, "from", "subject", "preview"]
EMAIL_PROPERTIES = [*FACTS, "from", "to", "cc", "replyTo", "subject", "textBody", "bodyValues"]
# What a reply needs of the email it answers: whom it was from and to, and the conversation
REPLY_PROPERTIES = [
    *FACTS,
    "from",
    "to",
    "cc",
    "replyTo",
    "subject",
    "messageId",
    "inReplyTo",
    "references",
]
# A subject that says it is a reply already, such as Re: or the RE : of French mail clients
REPLY_PREFIX = re.compile(r"\s*re\s*:", re.IGNORECASE)
NEWEST_FIRST = [{"property": "receivedAt", "isAscending": False}]
_MAILBOXES = ("Mailbox/get", {"ids": None, "properties": MAILBOX_PROPERTIES})
# What a list or a search leaves out unless asked for: James names the role of the spam mailbox
# spam, where JMAP's registry names it junk
LEFT_OUT = {"trash", "spam", "junk"}
SPAM_ROLES = {"spam", "junk"}

# What a reader does not see (Unicode's Cc, Cf and Cs): the control characters but for whitespace,
# which is collapsed; the format characters, which are invisible and can reorder text, such as
# bidirectional marks, zero-width spaces and tags; and surrogates left alone, which no text holds
UNSEEN = {"Cc", "Cf", "Cs"}


def _mail_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


def _unavailable(detail: str) -> Problem:
    return _mail_problem("mail_unavailable", "Mail unavailable", detail)


def _not_found(code: str, title: str, detail: str) -> Problem:
    return Problem(status=404, code=code, title=title, detail=detail)


def _email_not_found(email_id: str) -> Problem:
    return _not_found(
        "email_not_found",
        "Email not found",
        f"The user has no email {email_id} in their own mailboxes.",
    )


def _seen(text: str) -> str:
    """The text without what a reader does not see."""
    return "".join(c for c in text if c.isspace() or unicodedata.category(c) not in UNSEEN)


def _line(text: str | None, longest: int = LONGEST_LINE) -> str:
    """Text other people wrote, on one line, without what a reader does not see, cut after
    `longest` characters."""
    return " ".join(_seen(text or "").split())[:longest]


def _paragraphs(text: str) -> str:
    """Text other people wrote, without what a reader does not see, its blank runs collapsed."""
    lines = (" ".join(line.split()) for line in _seen(text).splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _utc(time: datetime | None) -> str | None:
    """The time as JMAP writes dates, in UTC."""
    return None if time is None else time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Jmap(BaseModel):
    """What TMail answers, read from the camelCase of JMAP, written in the snake_case of the
    contracts."""

    model_config = ConfigDict(alias_generator=AliasGenerator(validation_alias=to_camel))


def _parsed[Model: BaseModel](model: type[Model], value: Any, what: str) -> Model:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise _unavailable(f"Mail gave {what} in an unexpected form.") from error


class _Session(_Jmap):
    username: str
    primary_accounts: dict[str, str]


class Mailbox(_Jmap):
    """One of the user's own mailboxes."""

    id: str
    name: str
    # James leaves out what a mailbox lacks: the role of most, the parent of a top-level one
    role: str | None = None
    parent_id: str | None = None
    total_emails: int
    unread_emails: int


class _Mailboxes(_Jmap):
    found: list[Mailbox] = Field(validation_alias="list")


class Address(BaseModel):
    name: str | None
    email: str | None


class EmailSummaryText(BaseModel):
    """What other people wrote of an email, as a list shows it."""

    sender: list[Address] = Field(serialization_alias="from")
    subject: str
    preview: str


class EmailText(BaseModel):
    """What other people wrote of an email."""

    sender: list[Address] = Field(serialization_alias="from")
    to: list[Address]
    cc: list[Address]
    reply_to: list[Address]
    subject: str
    body: str


class _Facts(BaseModel):
    """What TMail itself tells of an email: ids, time and flags."""

    id: str
    thread_id: str
    mailbox_ids: list[str]
    received_at: datetime
    unread: bool
    flagged: bool
    has_attachment: bool


class EmailSummary(_Facts):
    """An email of the user, as a list shows it."""

    untrusted: EmailSummaryText


class Email(_Facts):
    """An email of the user, as text."""

    external_sender: bool
    reply_to_differs: bool
    body_truncated: bool
    body_unreadable: bool
    """Whether TMail could not decode the text, which may then read wrong."""
    recipients_truncated: bool
    """Whether to or cc gives only the first of its addresses."""
    untrusted: EmailText


class _Address(_Jmap):
    name: str | None = None
    email: str | None = None


def _cleaned(addresses: list[_Address] | None) -> list[Address]:
    return [
        Address(
            name=_line(address.name, LONGEST_NAME) or None,
            email=_line(address.email, LONGEST_ADDRESS) or None,
        )
        for address in (addresses or [])[:MOST_ADDRESSES]
    ]


def _emails(addresses: list[Address]) -> set[str]:
    return {(address.email or "").lower() for address in addresses}


def _domain(address: Address) -> str:
    return (address.email or "").lower().rpartition("@")[2]


class _BodyPart(_Jmap):
    part_id: str | None = None


class _BodyValue(_Jmap):
    value: str
    is_truncated: bool = False
    is_encoding_problem: bool = False


class _Email(_Jmap):
    id: str
    thread_id: str
    mailbox_ids: dict[str, bool]
    keywords: dict[str, bool] = Field(default_factory=dict)
    received_at: datetime
    has_attachment: bool = False
    sender: list[_Address] | None = Field(default=None, validation_alias="from")
    to: list[_Address] | None = None
    cc: list[_Address] | None = None
    reply_to: list[_Address] | None = None
    subject: str | None = None
    preview: str | None = None
    text_body: list[_BodyPart] = Field(default_factory=list)
    body_values: dict[str, _BodyValue] = Field(default_factory=dict)
    message_id: list[str] | None = None
    in_reply_to: list[str] | None = None
    references: list[str] | None = None

    def _facts(self, own: set[str]) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            # A mailbox shared with the user is not theirs to tell of
            "mailbox_ids": [mailbox for mailbox in self.mailbox_ids if mailbox in own],
            "received_at": self.received_at,
            "unread": not self.keywords.get("$seen", False),
            "flagged": self.keywords.get("$flagged", False),
            "has_attachment": self.has_attachment,
        }

    def summary(self, own: set[str]) -> EmailSummary:
        """The email as a list shows it, for a user whose own mailboxes are those."""
        return EmailSummary(
            **self._facts(own),
            untrusted=EmailSummaryText(
                sender=_cleaned(self.sender),
                subject=_line(self.subject),
                preview=_line(self.preview),
            ),
        )

    def text(self, own: set[str], domain: str, longest: int) -> Email:
        """The email as text, for a user of that domain whose own mailboxes are those, its text
        cut after `longest` characters."""
        values = [
            self.body_values[part.part_id]
            for part in self.text_body
            if part.part_id in self.body_values
        ]
        body = _paragraphs("\n\n".join(value.value for value in values))
        senders, reply_to = _cleaned(self.sender), _cleaned(self.reply_to)
        return Email(
            **self._facts(own),
            external_sender=not senders or any(_domain(sender) != domain for sender in senders),
            reply_to_differs=bool(_emails(reply_to) - _emails(senders)),
            body_truncated=any(value.is_truncated for value in values) or len(body) > longest,
            body_unreadable=any(value.is_encoding_problem for value in values),
            recipients_truncated=any(
                len(header or []) > MOST_ADDRESSES for header in (self.to, self.cc)
            ),
            untrusted=EmailText(
                sender=senders,
                to=_cleaned(self.to),
                cc=_cleaned(self.cc),
                reply_to=reply_to,
                subject=_line(self.subject),
                body=body[:longest],
            ),
        )


class _Emails(_Jmap):
    found: list[_Email] = Field(validation_alias="list")


class _Query(_Jmap):
    ids: list[str]


class _Thread(_Jmap):
    id: str
    email_ids: list[str]


class _Place(_Jmap):
    """Where an email is: its mailboxes."""

    id: str
    mailbox_ids: dict[str, bool]


class _Placed(_Place):
    """Where an email is, and what tells it apart: its subject, and whom it is from."""

    subject: str | None = None
    sender: list[_Address] | None = Field(default=None, validation_alias="from")


class _Placements(_Jmap):
    found: list[_Placed] = Field(validation_alias="list")


class _Places(_Jmap):
    found: list[_Place] = Field(validation_alias="list")


class _Threads(_Jmap):
    found: list[_Thread] = Field(validation_alias="list")


class _Identity(_Jmap):
    name: str = ""
    email: str


class _Identities(_Jmap):
    found: list[_Identity] = Field(validation_alias="list")


class _Created(_Jmap):
    id: str


class _EmailSet(_Jmap):
    created: dict[str, _Created] | None = None
    not_created: dict[str, dict[str, Any]] | None = None
    updated: dict[str, Any] | None = None
    not_updated: dict[str, dict[str, Any]] | None = None


class ReplyText(BaseModel):
    """What other people wrote that a reply draft holds: whom it answers, and its subject."""

    to: list[Address]
    cc: list[Address]
    subject: str


class ReplyDraft(BaseModel):
    """A reply prepared in the user's Drafts mailbox, which they review and send themselves."""

    email_id: str
    """The email it answers."""
    draft_id: str
    reply_to_differs: bool
    recipients_truncated: bool
    """Whether to or cc gives only the first of the addresses the draft answers."""
    untrusted: ReplyText


@dataclass(frozen=True)
class PreparedReply:
    """A reply to one of the user's emails as it would go to their Drafts mailbox, but for its
    text: nothing is written until it is saved."""

    email_id: str
    """The email it answers."""
    draft: dict[str, Any]
    """The email Email/set would create, in JMAP, but for its text."""
    reply_to_differs: bool
    """Whether it answers a Reply-To address that the email is not from."""
    to: list[Address]
    cc: list[Address]
    subject: str
    recipients_truncated: bool
    """Whether to or cc gives only the first of the addresses the draft answers."""


def _lowered(addresses: list[_Address]) -> set[str]:
    return {(address.email or "").lower() for address in addresses}


def _recipients(addresses: list[_Address], left_out: set[str]) -> list[_Address]:
    """Those of the addresses that have an email outside those left out, each email once."""
    kept: list[_Address] = []
    seen = set(left_out)
    for address in addresses:
        email = (address.email or "").lower()
        if email and email not in seen:
            seen.add(email)
            kept.append(address)
    return kept


def _written_by(email: _Email, users: set[str], sent: set[str]) -> bool:
    """Whether the user of these addresses wrote the email, whose Sent mailboxes are those: it is
    in one of them, from one of their addresses. A From alone could be anyone's."""
    return bool(sent & email.mailbox_ids.keys() and _lowered(email.sender or []) & users)


def _reply_recipients(
    email: _Email, users: set[str], written: bool, reply_all: bool
) -> tuple[list[_Address], list[_Address]]:
    """Whom a reply to the email goes to and whom it copies, as Twake Mail answers, never the user
    of these addresses: the Reply-To address, else the sender, and with reply_all, also those the
    email went to, and those it copied; for an email the user wrote, those it went to."""
    if written:
        answered = email.to or []
    else:
        answered = (email.reply_to or email.sender or []) + ((email.to or []) if reply_all else [])
    to = _recipients(answered, users)
    cc = _recipients(email.cc or [], users | _lowered(to)) if reply_all else []
    return to, cc


def _reply_subject(subject: str | None) -> str:
    """The email's subject, with Re: before it unless it says it is a reply already."""
    subject = subject or ""
    return subject if REPLY_PREFIX.match(subject) else f"Re: {subject}"


def _references(email: _Email) -> list[str]:
    """The emails a reply to this one refers to, as RFC 5322 has it: those it refers to, else the
    one it answers, then itself."""
    in_reply_to = email.in_reply_to or []
    parents = email.references or (in_reply_to if len(in_reply_to) == 1 else [])
    return [*parents, *(email.message_id or [])]


def _jmap_address(address: _Address) -> dict[str, str]:
    return {"email": address.email or ""} | ({"name": address.name} if address.name else {})


@dataclass(frozen=True)
class Search:
    """What a list or a search of the user's mail keeps: the emails that match all it says."""

    mailbox: str | None = None
    """One of the user's own mailboxes; without it, all of them but trash and spam."""
    text: str | None = None
    sender: str | None = None
    unread: bool = False
    flagged: bool = False
    after: datetime | None = None
    before: datetime | None = None


def _condition(search: Search, mailboxes: list[Mailbox]) -> dict[str, Any]:
    """The search as one JMAP filter condition: James refuses mailboxes inside an operator."""
    condition: dict[str, Any] = {}
    if search.mailbox is None:
        left_out = [mailbox.id for mailbox in mailboxes if mailbox.role in LEFT_OUT]
        if left_out:
            condition["inMailboxOtherThan"] = left_out
    elif any(mailbox.id == search.mailbox for mailbox in mailboxes):
        condition["inMailbox"] = search.mailbox
    else:
        raise _not_found(
            "mailbox_not_found",
            "Mailbox not found",
            f"The user has no mailbox {search.mailbox} of their own.",
        )
    wanted = {
        "text": search.text,
        "from": search.sender,
        "notKeyword": "$seen" if search.unread else None,
        "hasKeyword": "$flagged" if search.flagged else None,
        "after": _utc(search.after),
        "before": _utc(search.before),
    }
    return condition | {key: value for key, value in wanted.items() if value is not None}


def _own(results: dict[str, Any]) -> set[str]:
    """The ids of the user's own mailboxes, from the results of a request that got them."""
    mailboxes = _parsed(_Mailboxes, results["Mailbox/get"], "the mailboxes").found
    return {mailbox.id for mailbox in mailboxes}


def _in_own(mailbox_ids: dict[str, bool], own: set[str]) -> bool:
    """Whether an email is in one of the user's own mailboxes: TMail gives the emails of mailboxes
    shared with the user too, whatever the capabilities."""
    return bool(own & mailbox_ids.keys())


def _own_emails_as_text(
    user: User, results: dict[str, Any], ids: list[str], own: set[str], longest: int
) -> list[Email]:
    """The emails of those ids Email/get gave, in that order, as text, but for those outside
    the user's own mailboxes: TMail gives the emails of mailboxes shared with the user too."""
    found = {email.id: email for email in _parsed(_Emails, results["Email/get"], "emails").found}
    domain = user.email.rpartition("@")[2]
    return [
        found[email_id].text(own, domain, longest)
        for email_id in ids
        if email_id in found and _in_own(found[email_id].mailbox_ids, own)
    ]


def _as_text(longest: int) -> dict[str, Any]:
    """Email/get's arguments for emails as text: TMail turns the HTML parts into text, and cuts
    each part after `longest` bytes."""
    return {
        "properties": EMAIL_PROPERTIES,
        "bodyProperties": ["partId"],
        "fetchTextBodyValues": True,
        "maxBodyValueBytes": longest,
    }


class Moved(BaseModel):
    """An email of the user, and the one of their own mailboxes it is now in."""

    email_id: str
    mailbox_id: str
    mailbox_name: str


@dataclass(frozen=True)
class Placement:
    """Where an email of the user is: in which of their own mailboxes."""

    email_id: str
    mailboxes: list[Mailbox]
    """All the user's own mailboxes."""
    mailbox_ids: list[str]
    """Those the email is in."""
    subject: str
    """What others wrote of the email that tells it apart: its subject, and whom it is from."""
    senders: list[Address]

    @property
    def in_spam(self) -> bool:
        return any(
            mailbox.role in SPAM_ROLES
            for mailbox in self.mailboxes
            if mailbox.id in self.mailbox_ids
        )

    def with_id(self, mailbox_id: str) -> Mailbox:
        """The user's own mailbox of that id."""
        for mailbox in self.mailboxes:
            if mailbox.id == mailbox_id:
                return mailbox
        raise _not_found(
            "mailbox_not_found",
            "Mailbox not found",
            f"The user has no mailbox {mailbox_id} of their own.",
        )

    def named(self, name: str) -> Mailbox:
        """The user's own mailbox of that name, whatever its case."""
        wanted = name.casefold()
        found = [mailbox for mailbox in self.mailboxes if mailbox.name.casefold() == wanted]
        return _one(
            found,
            f"The user has no mailbox of their own named {name}.",
            _ambiguous(f"Several of the user's mailboxes are named {name}", found),
        )

    def archive(self) -> Mailbox:
        """The user's archive: their own mailbox whose role is archive."""
        found = self._with_role("archive")
        return _one(
            found,
            "The user has no archive mailbox.",
            _ambiguous("Several of the user's mailboxes have the role archive", found),
        )

    def trash(self) -> Mailbox:
        """The user's trash: their own mailbox whose role is trash."""
        found = self._with_role("trash")
        # move_email moves no email to a trash: only the user can leave a single one
        several = Problem(
            status=409,
            code="trash_ambiguous",
            title="Several trash mailboxes",
            detail=f"Several of the user's mailboxes have the role trash: {_ids(found)}. No"
            " contract chooses among them: ask the user to keep a single trash folder in Twake"
            " Mail, then put the email in the trash again.",
        )
        return _one(found, "The user has no trash mailbox.", several)

    def _with_role(self, role: str) -> list[Mailbox]:
        return [mailbox for mailbox in self.mailboxes if mailbox.role == role]


def _ids(mailboxes: list[Mailbox]) -> str:
    return ", ".join(mailbox.id for mailbox in mailboxes)


def _ambiguous(several: str, found: list[Mailbox]) -> Problem:
    """Several of the user's mailboxes where they mean one: move_email takes the id of that one."""
    return Problem(
        status=409,
        code="mailbox_ambiguous",
        title="Ambiguous mailbox",
        detail=f"{several}: {_ids(found)}. Ask the user which one they mean, then move the email"
        " there with move_email and its mailbox_id.",
    )


def _one(found: list[Mailbox], missing: str, several: Problem) -> Mailbox:
    """The one mailbox found: none is not found, and several are refused rather than guessed,
    with the problem given."""
    if not found:
        raise _not_found("mailbox_not_found", "Mailbox not found", missing)
    if len(found) > 1:
        raise several
    return found[0]


class TMail:
    """TMail's JMAP API, called as the user with their own token."""

    SESSION_MAX_AGE = 300.0
    """Seconds the account a token opens is kept before the session is read again."""

    def __init__(self, url: str, http: httpx.AsyncClient, clock: Callable[[], float]) -> None:
        self._url = url
        self._http = http
        self._clock = clock
        self._accounts: dict[str, tuple[str, float]] = {}
        """The personal account each token opens, until when, by the SHA-256 of the token."""

    async def _send(self, user: User, method: str, path: str, **request: Any) -> Any:
        """TMail's JSON answer."""
        headers = {"Authorization": f"Bearer {user.token}", "Accept": JMAP_JSON}
        try:
            response = await self._http.request(
                method, self._url + path, headers=headers, **request
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 401:
                raise _mail_problem(
                    "mail_refused",
                    "Mail refused the user's token",
                    f"Mail answered 401 to {method} {path}.",
                ) from error
            raise _unavailable(
                f"Mail answered {error.response.status_code} to {method} {path}."
            ) from error
        except httpx.HTTPError as error:
            raise _unavailable(f"Mail did not answer {method} {path}.") from error
        except ValueError as error:
            raise _unavailable(f"Mail did not answer {method} {path} in JSON.") from error

    async def _account(self, user: User) -> str:
        """The user's personal mail account, from the session TMail opens for their token."""
        key = hashlib.sha256(user.token.encode()).hexdigest()
        now = self._clock()
        held = self._accounts.get(key)
        if held is not None and now < held[1]:
            return held[0]
        session = _parsed(_Session, await self._send(user, "GET", "/jmap/session"), "its session")
        # TMail opens the account of the email its userinfo gives: it must be the token's subject
        if session.username.lower() != user.email:
            raise _mail_problem(
                "mail_account_mismatch",
                "Mail account mismatch",
                "Mail opened the account of another user than the one you act for: nothing was"
                " read.",
            )
        # The user's own account: those delegated to them are listed too, never primary
        account = session.primary_accounts.get(MAIL)
        if account is None:
            raise _unavailable("Mail gave a session without a mail account.")
        # The accounts of expired tokens go as new ones come, so that they do not pile up
        self._accounts = {token: kept for token, kept in self._accounts.items() if now < kept[1]}
        self._accounts[key] = (account, now + self.SESSION_MAX_AGE)
        return account

    async def _call(self, user: User, *calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        """The results of these method calls, made in one request on the user's account, by
        method name. A method is called once per request, so its name is also its call id, which
        a back-reference to its result names."""
        account = await self._account(user)
        request = {
            "using": [CORE, MAIL, *sorted({NEEDS[name] for name, _ in calls if name in NEEDS})],
            "methodCalls": [
                [name, {"accountId": account} | arguments, name] for name, arguments in calls
            ],
        }
        answer = await self._send(user, "POST", "/jmap", json=request)
        try:
            responses = {
                call_id: (name, result) for name, result, call_id in answer["methodResponses"]
            }
        except (KeyError, TypeError, ValueError) as error:
            raise _unavailable("Mail answered in an unexpected form.") from error
        results = {}
        for name, _ in calls:
            answered, result = responses.get(name, ("nothing", None))
            if answered != name or not isinstance(result, dict):
                # A method that fails answers an error of a type instead (RFC 8620)
                what = result.get("type", answered) if isinstance(result, dict) else answered
                raise _unavailable(f"Mail answered {what} to {name}.")
            results[name] = result
        return results

    async def mailboxes(self, user: User) -> list[Mailbox]:
        """The user's own mailboxes: without the shares capability, TMail leaves out those
        shared with them."""
        results = await self._call(user, _MAILBOXES)
        return _parsed(_Mailboxes, results["Mailbox/get"], "the mailboxes").found

    async def emails(
        self, user: User, search: Search, position: int, limit: int
    ) -> tuple[list[EmailSummary], bool]:
        """The emails found, newest first, from that position on; and whether they fill the
        page, so that more may follow."""
        mailboxes = await self.mailboxes(user)
        results = await self._call(
            user,
            (
                "Email/query",
                {
                    "filter": _condition(search, mailboxes),
                    "sort": NEWEST_FIRST,
                    "position": position,
                    "limit": limit,
                },
            ),
            (
                "Email/get",
                {
                    "#ids": {"resultOf": "Email/query", "name": "Email/query", "path": "/ids"},
                    "properties": SUMMARY_PROPERTIES,
                },
            ),
        )
        found = _parsed(_Query, results["Email/query"], "the emails found").ids
        emails = {
            email.id: email
            for email in _parsed(_Emails, results["Email/get"], "the emails found").found
        }
        own = {mailbox.id for mailbox in mailboxes}
        summaries = [
            emails[email_id].summary(own)
            for email_id in found
            if email_id in emails and _in_own(emails[email_id].mailbox_ids, own)
        ]
        return summaries, len(found) == limit

    async def email(self, user: User, email_id: str) -> Email:
        """One of the user's emails, as text, if it is in one of their own mailboxes."""
        results = await self._call(
            user, _MAILBOXES, ("Email/get", {"ids": [email_id]} | _as_text(BODY_BYTES))
        )
        emails = _own_emails_as_text(user, results, [email_id], _own(results), BODY_BYTES)
        if not emails:
            raise _email_not_found(email_id)
        return emails[0]

    async def thread(self, user: User, thread_id: str, limit: int) -> list[Email]:
        """The last emails of one of the user's conversations, at most limit, oldest first, as
        text, but for those outside the user's own mailboxes."""
        results = await self._call(
            user,
            _MAILBOXES,
            ("Thread/get", {"ids": [thread_id]}),
            # Where each of its emails is, so that the last ones are taken among the user's own
            (
                "Email/get",
                {
                    "#ids": {
                        "resultOf": "Thread/get",
                        "name": "Thread/get",
                        "path": "/list/*/emailIds",
                    },
                    "properties": ["id", "mailboxIds"],
                },
            ),
        )
        own = _own(results)
        threads = _parsed(_Threads, results["Thread/get"], "the conversation").found
        places = _parsed(_Places, results["Email/get"], "the conversation").found
        mailboxes = {place.id: place.mailbox_ids for place in places}
        # The ids of a thread's emails come oldest first (RFC 8621)
        in_thread = next((thread.email_ids for thread in threads if thread.id == thread_id), [])
        last = [email_id for email_id in in_thread if _in_own(mailboxes.get(email_id, {}), own)]
        last = last[-limit:]
        emails = []
        if last:
            results = await self._call(
                user, ("Email/get", {"ids": last} | _as_text(THREAD_BODY_BYTES))
            )
            emails = _own_emails_as_text(user, results, last, own, THREAD_BODY_BYTES)
        if not emails:
            raise _not_found(
                "thread_not_found",
                "Conversation not found",
                f"The user has no conversation {thread_id} in their own mailboxes.",
            )
        return emails

    async def prepare_reply(self, user: User, email_id: str, reply_all: bool) -> PreparedReply:
        """A reply to one of the user's emails, if it is in one of their own mailboxes, as it
        would go to their Drafts mailbox, but for its text: this only reads."""
        results = await self._call(
            user,
            _MAILBOXES,
            ("Email/get", {"ids": [email_id], "properties": REPLY_PROPERTIES}),
            ("Identity/get", {"ids": None, "properties": ["name", "email"]}),
        )
        mailboxes = _parsed(_Mailboxes, results["Mailbox/get"], "the mailboxes").found
        own = {mailbox.id for mailbox in mailboxes}
        email = next(
            (
                email
                for email in _parsed(_Emails, results["Email/get"], "the email").found
                if email.id == email_id and _in_own(email.mailbox_ids, own)
            ),
            None,
        )
        if email is None:
            raise _email_not_found(email_id)
        drafts = next((mailbox.id for mailbox in mailboxes if mailbox.role == "drafts"), None)
        if drafts is None:
            raise _not_found(
                "mailbox_not_found",
                "Mailbox not found",
                "The user has no Drafts mailbox of their own, where the draft would go.",
            )
        identities = _parsed(_Identities, results["Identity/get"], "the identities").found
        users = {user.email} | {identity.email.lower() for identity in identities}
        sent = {mailbox.id for mailbox in mailboxes if mailbox.role == "sent"}
        written = _written_by(email, users, sent)
        to, cc = _reply_recipients(email, users, written, reply_all)
        # From the user's own address, with the name of its identity
        name = next((one.name for one in identities if one.email.lower() == user.email), "")
        subject = _reply_subject(email.subject)
        draft = {
            "mailboxIds": {drafts: True},
            # As Twake Mail keeps a draft: one the user need not read
            "keywords": {"$draft": True, "$seen": True},
            "from": [_jmap_address(_Address(name=name or None, email=user.email))],
            "to": [_jmap_address(address) for address in to],
            "cc": [_jmap_address(address) for address in cc],
            "subject": subject,
            "inReplyTo": email.message_id,
            "references": _references(email),
        }
        return PreparedReply(
            email_id=email_id,
            # James writes an empty list as an empty header: what is empty is left out
            draft={key: value for key, value in draft.items() if value},
            # The draft answers a Reply-To address that the email is not from
            reply_to_differs=not written
            and bool(_emails(_cleaned(email.reply_to)) - _emails(_cleaned(email.sender))),
            to=_cleaned(to),
            cc=_cleaned(cc),
            subject=_line(subject),
            recipients_truncated=any(len(header) > MOST_ADDRESSES for header in (to, cc)),
        )

    async def save_reply(self, user: User, reply: PreparedReply, text: str) -> ReplyDraft:
        """Creates the reply with its text in the user's Drafts mailbox, with Email/set: nothing
        sends it."""
        body = {
            "textBody": [{"partId": "text", "type": "text/plain"}],
            "bodyValues": {"text": {"value": text}},
        }
        results = await self._call(user, ("Email/set", {"create": {"draft": reply.draft | body}}))
        answer = _parsed(_EmailSet, results["Email/set"], "the draft")
        created = (answer.created or {}).get("draft")
        if created is None:
            refusal = (answer.not_created or {}).get("draft", {}).get("type", "nothing")
            raise _unavailable(f"Mail answered {refusal} to Email/set.")
        return ReplyDraft(
            email_id=reply.email_id,
            draft_id=created.id,
            reply_to_differs=reply.reply_to_differs,
            recipients_truncated=reply.recipients_truncated,
            untrusted=ReplyText(to=reply.to, cc=reply.cc, subject=reply.subject),
        )

    async def placement(self, user: User, email_id: str) -> Placement:
        """Where one of the user's emails is, if it is in one of their own mailboxes, with what
        tells it apart."""
        properties = ["id", "mailboxIds", "subject", "from"]
        results = await self._call(
            user, _MAILBOXES, ("Email/get", {"ids": [email_id], "properties": properties})
        )
        mailboxes = _parsed(_Mailboxes, results["Mailbox/get"], "the mailboxes").found
        own = {mailbox.id for mailbox in mailboxes}
        place = next(
            (
                place
                for place in _parsed(_Placements, results["Email/get"], "the email").found
                if place.id == email_id
            ),
            None,
        )
        # TMail gives the emails of mailboxes shared with the user too
        mailbox_ids = [mailbox for mailbox in place.mailbox_ids if mailbox in own] if place else []
        if place is None or not mailbox_ids:
            raise _email_not_found(email_id)
        return Placement(
            email_id, mailboxes, mailbox_ids, _line(place.subject), _cleaned(place.sender)
        )

    async def move(self, user: User, placement: Placement, mailbox: Mailbox) -> Moved:
        """Moves the email to one of the user's own mailboxes, out of their others. A patch rather
        than the whole set, so that a mailbox of someone else, shared with the user, keeps it."""
        patch: dict[str, bool | None] = {
            f"mailboxIds/{mailbox_id}": None
            for mailbox_id in placement.mailbox_ids
            if mailbox_id != mailbox.id
        }
        patch[f"mailboxIds/{mailbox.id}"] = True
        results = await self._call(user, ("Email/set", {"update": {placement.email_id: patch}}))
        answer = _parsed(_EmailSet, results["Email/set"], "the update")
        if placement.email_id not in (answer.updated or {}):
            refusal = (answer.not_updated or {}).get(placement.email_id, {}).get("type", "nothing")
            raise _unavailable(f"Mail answered {refusal} to Email/set.")
        return Moved(email_id=placement.email_id, mailbox_id=mailbox.id, mailbox_name=mailbox.name)
