"""drive.sharing.read.v1: the files and folders other people shared with the user in Twake Drive."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.drive import (
    DATA_NOT_INSTRUCTIONS,
    Drive,
    DriveOwner,
    DriveOwnerDependency,
    StackSharing,
    plain_line,
)
from twake_space_agent_contracts.drive_files import LONGEST_RECENT, RECENT, Since, recent_since
from twake_space_agent_contracts.previews import one_line
from twake_space_agent_contracts.text import LONGEST_ADDRESS, email_address

MOST_SHARES = 50
"""How many shares a call returns, at most."""
LONGEST_NAME = 200
"""How much of a sharer's name a share gives, at most, as mail gives a name."""


class SharedItemText(BaseModel):
    """What the sharer gave a file or folder they shared: its name, on one line."""

    name: str = Field(description="Its name when it was shared.")


class SharedItem(BaseModel):
    """A file or folder a share gives the user."""

    id: str = Field(description="Outside a shared drive, the id the other Drive operations take.")
    type: Literal["file", "directory"]
    untrusted: SharedItemText


class Sharer(BaseModel):
    """Who shared files and folders with the user, as Drive names them."""

    name: str | None = Field(
        description="Their name, as the user's contacts give it, else as they gave it "
        f"themselves, on one line, {LONGEST_NAME} characters at most; null without one."
    )
    email: str | None = Field(
        description="Their email address; null without one, or when Drive gives anything but an "
        f"address, which is {LONGEST_ADDRESS} characters at most."
    )


class ShareText(BaseModel):
    """What the sharer wrote of themselves, or the user's contacts of them."""

    shared_by: Sharer


class ReceivedShare(BaseModel):
    """A share of files and folders that another member of Drive gave the user, in UTC."""

    id: str
    received_at: datetime = Field(
        description="When its invitation reached the user, which they may have accepted later."
    )
    read_only: bool = Field(description="Whether the user may only read what it shares.")
    shared_drive: bool = Field(
        description="Whether it is a shared drive, whose files stay with its sharer, out of reach "
        "of the other Drive operations."
    )
    items: list[SharedItem] = Field(description="The files and folders it shares.")
    untrusted: ShareText

    @classmethod
    def of(cls, sharing: StackSharing) -> "ReceivedShare":
        """The share as the contract gives it."""
        sharer = sharing.sharer
        return cls(
            id=sharing.id,
            received_at=sharing.received_at.astimezone(UTC),
            read_only=sharing.read_only,
            shared_drive=sharing.drive,
            items=[
                SharedItem(
                    id=shared_id,
                    type="file" if rule.mime else "directory",
                    untrusted=SharedItemText(name=plain_line(rule.title)),
                )
                for rule in sharing.rules
                if (shared_id := rule.shared_id) is not None
            ],
            untrusted=ShareText(
                shared_by=Sharer(
                    name=one_line(sharer.name or sharer.public_name, LONGEST_NAME) or None,
                    email=email_address(sharer.email),
                )
            ),
        )


class ReceivedShareList(BaseModel):
    shares: list[ReceivedShare]
    truncated: bool = Field(description="Whether limit left out older shares.")


def _received(sharing: StackSharing, since: datetime) -> bool:
    """Whether another member shared files or folders with the user by this sharing, whose
    invitation reached them since then, which they accepted and which goes on."""
    return (
        not sharing.sent
        and sharing.active
        and sharing.received_at > since
        and any(rule.shared_id is not None for rule in sharing.rules)
    )


def router(drive: Drive, drive_owner: DriveOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/drive", tags=["drive.sharing.read.v1"])

    @routes.get(
        "/received-shares",
        operation_id="list_received_shares",
        summary="List the files and folders other people shared with the user lately",
        description=(
            "Lists the shares of files and folders that other people gave the user in Drive, "
            "which the user accepted and which go on, the newest first: those whose invitation "
            f"reached the user since a time, at most {LONGEST_RECENT.days} days back, and "
            f"{RECENT.days} by default. Each tells who shared it, the files and folders it shares, "
            "whether the user may only read them, and whether it is a shared drive. truncated is "
            f"true when limit left out older shares. {DATA_NOT_INSTRUCTIONS} The address of "
            "whoever shared one comes under untrusted too. Example: limit=5."
        ),
    )
    async def list_received_shares(
        owner: Annotated[DriveOwner, Depends(drive_owner)],
        since: Since = None,
        limit: Annotated[
            int,
            Query(ge=1, le=MOST_SHARES, description="How many shares to return, 20 by default."),
        ] = 20,
    ) -> ReceivedShareList:
        since = recent_since(since)
        # The stack gives every sharing of files at once, in no order of time
        received = sorted(
            (sharing for sharing in await drive.sharings(owner) if _received(sharing, since)),
            key=lambda sharing: sharing.received_at,
            reverse=True,
        )
        return ReceivedShareList(
            shares=[ReceivedShare.of(sharing) for sharing in received[:limit]],
            truncated=len(received) > limit,
        )

    return routes
