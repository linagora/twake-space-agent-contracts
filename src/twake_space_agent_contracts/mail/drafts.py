"""mail.draft.create.v1: replies prepared as drafts in the user's mailbox, which the user reviews
and sends themselves. The contract never sends anything."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.mail import EXAMPLE_ID, UNTRUSTED, people
from twake_space_agent_contracts.mail.tmail import JMAP_ID, PreparedReply, ReplyDraft, TMail
from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    Previewing,
    digest_of,
    excerpt,
    one_line,
    quoted,
    shown_size,
)

TEXT_BYTES = 20 * 1024
"""How large the text of a reply may be, in bytes of UTF-8."""


class Reply(BaseModel):
    """The reply the agent writes. Whom it goes to, the contract finds: never the agent."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        # What the gateway can check too: no text of TEXT_BYTES bytes has more characters
        max_length=TEXT_BYTES,
        description=f"The text of the reply, in plain text, at most {TEXT_BYTES} bytes in UTF-8: "
        f"{TEXT_BYTES} characters of ASCII, fewer of accented letters or emoji.",
    )
    reply_all: bool = Field(
        default=False,
        description="true to answer everyone else the email went to as well, but the user; false "
        "by default.",
    )

    @field_validator("text")
    @classmethod
    def _fits(cls, text: str) -> str:
        size = len(text.encode())
        if size > TEXT_BYTES:
            raise PydanticCustomError(
                "text_too_large",
                "the text takes {size} bytes in UTF-8, over the {most} it may take",
                {"size": size, "most": TEXT_BYTES},
            )
        return text


@dataclass(frozen=True)
class _Words:
    """What a preview of a reply draft tells the owner, in one language."""

    drafted: str
    to: str
    cc: str
    subject: str
    text: str
    reply_to: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        drafted="Préparer dans tes brouillons une réponse, jamais envoyée : tu la relis et"
        " l'envoies toi-même.",
        to="À : {people}",
        cc="Cc : {people}",
        subject="Objet : {subject}",
        text="Texte :",
        reply_to="Elle va à l'adresse de réponse que donne le mail, pas à son expéditeur.",
    ),
    "en": _Words(
        drafted="Prepare a reply in your drafts, never sent: you review it and send it yourself.",
        to="To: {people}",
        cc="Cc: {people}",
        subject="Subject: {subject}",
        text="Text:",
        reply_to="It goes to the reply address the email gives, not to its sender.",
    ),
}


def _summary(reply: PreparedReply, text: str, language: Language) -> str:
    """What drafting the reply does, as the owner reads it: whom the draft answers, under which
    subject, which the email's senders wrote, and its text, whole when it fits."""
    words = _WORDS[language]
    lines = [words.drafted]
    for header, shown, line in (("to", reply.to, words.to), ("cc", reply.cc, words.cc)):
        total = len(reply.draft.get(header, []))
        if total:
            lines.append(line.format(people=people(shown, total, language)))
    lines.append(words.subject.format(subject=quoted(one_line(reply.subject), language)))
    if reply.reply_to_differs:
        lines.append(words.reply_to)
    lines.append(words.text)
    head = "\n".join(lines) + "\n"
    # The text takes what the rest leaves of the summary
    return head + excerpt(text, BUDGET - shown_size(head), language)


def router(tmail: TMail, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/mail", tags=["mail.draft.create.v1"])

    @routes.post(
        "/emails/{email_id}/reply-draft",
        operation_id="create_reply_draft",
        summary="Prepare a draft reply to an email of the user, never sent",
        description=(
            "Prepares a reply to one of the emails of the user you act for, from their own "
            "mailboxes, as a draft in their Drafts mailbox. It never sends anything: the user "
            "reviews the draft and sends it themselves from Twake Mail. The draft answers the "
            "Reply-To address of the email, else its sender, and with reply_all true also everyone "
            "else the email went to, but never the user; to an email the user wrote, one of their "
            "Sent mailbox from one of their addresses, it answers those they sent it to. It stays "
            "in the conversation, under the email's subject with Re: before it. It holds text as "
            "you write it, in plain text: neither the email it answers, nor a signature, nor an "
            "attachment. reply_to_differs is true when the draft answers another address than the "
            "one the email is from: tell the user. to and cc give 100 addresses at most, "
            f"recipients_truncated telling that the draft has more. {UNTRUSTED} "
            "Example, for a reply to the email that list_emails gave with the id "
            f"{EXAMPLE_ID}: email_id={EXAMPLE_ID}, "
            'body={"text": "Hello Paul, the budget suits me. Best regards, Michel-Marie", '
            '"reply_all": false}.'
        ),
        status_code=201,
        response_model=ReplyDraft,
        # Nothing leaves the mailbox until the user sends it: the owner's consent to write in Mail
        # covers it, and they are not asked to confirm each draft. It tells what it would do, for
        # when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def create_reply_draft(
        email_id: Annotated[
            str,
            Path(
                pattern=JMAP_ID,
                description="The id of the email to answer, as list_emails, search_emails or "
                "read_thread gives it.",
            ),
        ],
        reply: Reply,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> ReplyDraft | JSONResponse:
        prepared = await tmail.prepare_reply(user, email_id, reply.reply_all)
        # What the owner allows: the draft as it would be created, but for its text, the call's own
        digest = digest_of(prepared.email_id, prepared.draft)
        if preview.asked:
            return preview.answer(_summary(prepared, reply.text, preview.language), digest)
        preview.check(digest)
        return await tmail.save_reply(user, prepared, reply.text)

    return routes
