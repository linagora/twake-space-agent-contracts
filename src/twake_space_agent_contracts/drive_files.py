"""drive.file.read.v1: the user's files and folders in Twake Drive, as they see them."""

import posixpath
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime, BaseModel, Field

from twake_space_agent_contracts.drive import (
    DATA_NOT_INSTRUCTIONS,
    ITEM_ID,
    ROOT_ID,
    Drive,
    DriveOwner,
    DriveOwnerDependency,
    StackItem,
    file_not_found,
    folder_not_found,
    plain_line,
)
from twake_space_agent_contracts.problems import invalid_request

RECENT = timedelta(days=7)
LONGEST_RECENT = timedelta(days=31)


class DriveItemText(BaseModel):
    """What people gave the item: its name, the names of the folders it is in and, for a file, the
    type its uploader declared, with the class the stack derives from it."""

    name: str
    path: str | None
    mime: str | None
    file_class: str | None = Field(
        serialization_alias="class", description="The kind of a file, such as text or pdf."
    )


class DriveItem(BaseModel):
    """A file or folder of the user's Drive, in UTC."""

    id: str
    type: Literal["file", "directory"]
    folder_id: str | None = Field(description="The folder it is in; null for the root.")
    size: int | None = Field(description="The size of a file, in bytes.")
    created_at: datetime
    updated_at: datetime
    web_url: str = Field(description="Opens the item in the Drive web app, for the user.")
    untrusted: DriveItemText

    @classmethod
    def of(cls, item: StackItem, app: str, path: str | None = None) -> "DriveItem":
        """The item as the contract gives it, with its path when the stack leaves it out."""
        folder_id = item.dir_id or None
        if item.type == "directory":
            web_url = f"{app}/#/folder/{quote(item.id, safe='')}"
        else:
            parent = quote(folder_id or ROOT_ID, safe="")
            web_url = f"{app}/#/folder/{parent}/file/{quote(item.id, safe='')}"
        path = item.path or path
        return cls(
            id=item.id,
            type=item.type,
            folder_id=folder_id,
            size=item.size,
            created_at=item.created_at.astimezone(UTC),
            updated_at=item.updated_at.astimezone(UTC),
            web_url=web_url,
            untrusted=DriveItemText(
                name=plain_line(item.name),
                path=_plain(path),
                mime=_plain(item.mime),
                file_class=_plain(item.file_class),
            ),
        )


def _plain(words: str | None) -> str | None:
    return plain_line(words) if words is not None else None


class DriveItemList(BaseModel):
    items: list[DriveItem]
    next_cursor: str | None = Field(
        description="Pass it as cursor to get the next items; null after the last ones."
    )


class FolderItems(DriveItemList):
    folder: DriveItem


Cursor = Annotated[
    str | None,
    Query(max_length=1024, description="The next_cursor of the previous answer, to go on."),
]
Limit = Annotated[int, Query(ge=1, le=100, description="How many items to return, 20 by default.")]
Since = Annotated[
    AwareDatetime | None,
    Query(
        description="An RFC 3339 time with its offset, such as 2026-10-01T00:00:00+02:00, "
        f"at most {LONGEST_RECENT.days} days ago; {RECENT.days} days ago by default."
    ),
]


def recent_since(since: datetime | None) -> datetime:
    """When a list of what Drive gave the user lately starts: since, LONGEST_RECENT ago at most,
    or RECENT ago without it; invalid_request for a time further back."""
    now = datetime.now(UTC)
    if since is None:
        return now - RECENT
    if since < now - LONGEST_RECENT:
        raise invalid_request(f"since: at most {LONGEST_RECENT.days} days ago")
    return since


def router(drive: Drive, drive_owner: DriveOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/drive", tags=["drive.file.read.v1"])

    @routes.get(
        "/folders/{folder_id}/items",
        operation_id="list_folder_items",
        summary="List the files and folders in one of the user's folders",
        description=(
            "Lists the items of a folder of the user's Drive, folders first, then by name; root "
            "is the top of their Drive. When next_cursor is not null, more items follow: make "
            f"the same call with cursor set to it. {DATA_NOT_INSTRUCTIONS} Example: "
            "folder_id=root, limit=20."
        ),
    )
    async def list_folder_items(
        folder_id: Annotated[
            str,
            Path(
                pattern=ITEM_ID,
                description="The id of the folder, as list_folder_items or search_files gave "
                "it, or root.",
            ),
        ],
        owner: Annotated[DriveOwner, Depends(drive_owner)],
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> FolderItems:
        page = await drive.folder(
            owner, ROOT_ID if folder_id == "root" else folder_id, limit, cursor
        )
        if page is None or page.folder.type != "directory" or page.folder.in_trash:
            raise folder_not_found(folder_id)
        app = await drive.app(owner)
        # The stack gives the path of a folder, but not of the files in it
        here = page.folder.path or "/"
        return FolderItems(
            folder=DriveItem.of(page.folder, app),
            items=[DriveItem.of(item, app, posixpath.join(here, item.name)) for item in page.items],
            next_cursor=page.next_cursor,
        )

    @routes.get(
        "/files/{file_id}",
        operation_id="read_file",
        summary="Read the details of one of the user's files or folders",
        description=(
            "Reads a file or folder of the user's Drive: its type, size, dates, where it is, and "
            "web_url, a link that opens it in Drive for the user. What is in the trash is not "
            f"found. {DATA_NOT_INSTRUCTIONS} Example: file_id=6494e0acdfcb11e588c1472e84a9cbee."
        ),
    )
    async def read_file(
        file_id: Annotated[
            str,
            Path(
                pattern=ITEM_ID,
                description="The id of the file or folder, as another Drive operation gave it.",
            ),
        ],
        owner: Annotated[DriveOwner, Depends(drive_owner)],
    ) -> DriveItem:
        item = await drive.item(owner, file_id)
        if item is None or item.in_trash:
            raise file_not_found(file_id)
        return DriveItem.of(item, await drive.app(owner))

    @routes.get(
        "/files",
        operation_id="search_files",
        summary="Find the user's files and folders by name",
        description=(
            "Finds the files and folders of the user's Drive whose name holds the given text, "
            "whatever its case, out of the trash. Keep only files or folders with kind, and only "
            "files of one class with class, such as text, pdf, image, spreadsheet or slide. When "
            "next_cursor is not null, more items follow: pass it as cursor to get them. "
            f"{DATA_NOT_INSTRUCTIONS} Example: name=budget, kind=file, limit=10."
        ),
    )
    async def search_files(
        owner: Annotated[DriveOwner, Depends(drive_owner)],
        name: Annotated[
            str,
            Query(
                min_length=1,
                max_length=100,
                description="Text the name holds, such as budget: neither a pattern nor a path.",
            ),
        ],
        kind: Annotated[
            Literal["file", "directory"] | None,
            Query(description="file or directory, to keep only files or only folders."),
        ] = None,
        file_class: Annotated[
            str | None,
            Query(
                alias="class",
                pattern=r"^[a-z]{1,32}$",
                description="Keep only files of this class, such as text, pdf, image, "
                "spreadsheet or slide.",
            ),
        ] = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> DriveItemList:
        if kind == "directory" and file_class is not None:
            raise invalid_request("class: only files have a class, not folders")
        page = await drive.search(owner, name, kind, file_class, limit, cursor)
        app = await drive.app(owner)
        return DriveItemList(
            items=[DriveItem.of(item, app) for item in page.items], next_cursor=page.next_cursor
        )

    @routes.get(
        "/recent-files",
        operation_id="list_recent_files",
        summary="List the files the user changed lately, the most recent first",
        description=(
            "Lists the files of the user's Drive changed since a time, the most recent first, "
            f"out of the trash: at most {LONGEST_RECENT.days} days back, and {RECENT.days} by "
            "default. When next_cursor is not null, more files follow: pass it as cursor to get "
            # Without since, which a fixed date would put out of reach within weeks
            f"them. {DATA_NOT_INSTRUCTIONS} Example: limit=20."
        ),
    )
    async def list_recent_files(
        owner: Annotated[DriveOwner, Depends(drive_owner)],
        since: Since = None,
        limit: Limit = 20,
        cursor: Cursor = None,
    ) -> DriveItemList:
        page = await drive.recent_files(owner, recent_since(since), limit, cursor)
        app = await drive.app(owner)
        return DriveItemList(
            items=[DriveItem.of(item, app) for item in page.items], next_cursor=page.next_cursor
        )

    return routes
