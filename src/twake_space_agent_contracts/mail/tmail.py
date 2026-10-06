"""TMail, the Twake Mail backend, called as the user with their own token through its JMAP API.

JMAP gives a token no scope: this client makes only the method calls the mail contracts need,
never any other. None uses James's shares capability, so that TMail keeps to the user's own
mailboxes, and every call goes to the user's personal account."""

import hashlib
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from twake_space_agent_contracts.caller import User
from twake_space_agent_contracts.problems import Problem

CORE = "urn:ietf:params:jmap:core"
MAIL = "urn:ietf:params:jmap:mail"
# The JMAP of RFC 8620 and 8621, as James's clients ask for it
JMAP_JSON = "application/json;jmapVersion=rfc-8621"

MAILBOX_PROPERTIES = ["id", "name", "parentId", "role", "totalEmails", "unreadEmails"]


def _mail_problem(code: str, title: str, detail: str) -> Problem:
    return Problem(status=502, code=code, title=title, detail=detail)


def _unavailable(detail: str) -> Problem:
    return _mail_problem("mail_unavailable", "Mail unavailable", detail)


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
    role: str | None
    parent_id: str | None
    total_emails: int
    unread_emails: int


class _Mailboxes(_Jmap):
    found: list[Mailbox] = Field(validation_alias="list")


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
        self._accounts = {token: held for token, held in self._accounts.items() if now < held[1]}
        self._accounts[key] = (account, now + self.SESSION_MAX_AGE)
        return account

    async def _call(self, user: User, *calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
        """The results of these method calls, made in one request on the user's account, by
        method name. A method is called once per request, so its name is also its call id, which
        a back-reference to its result names."""
        account = await self._account(user)
        request = {
            "using": [CORE, MAIL],
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
            answered, result = responses.get(name, ("error", {"type": "nothing"}))
            if answered != name or not isinstance(result, dict):
                error_type = result.get("type") if isinstance(result, dict) else None
                raise _unavailable(f"Mail answered {error_type or 'an error'} to {name}.")
            results[name] = result
        return results

    async def mailboxes(self, user: User) -> list[Mailbox]:
        """The user's own mailboxes: without the shares capability, TMail leaves out those
        shared with them."""
        results = await self._call(
            user, ("Mailbox/get", {"ids": None, "properties": MAILBOX_PROPERTIES})
        )
        return _parsed(_Mailboxes, results["Mailbox/get"], "the mailboxes").found
