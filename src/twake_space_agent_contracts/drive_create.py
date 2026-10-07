"""drive.file.create.v1: a text file the user's assistant adds to their own Drive, in a folder that
nobody else sees."""

import posixpath
import unicodedata
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.drive import (
    ITEM_ID,
    ROOT_ID,
    Drive,
    DriveOwner,
    DriveOwnerDependency,
    folder_not_found,
)
from twake_space_agent_contracts.drive_files import DriveItem
from twake_space_agent_contracts.problems import Problem, invalid_request

LARGEST = 1_048_576
"""The bytes a new file holds at most, its content encoded in UTF-8: 1 MiB."""
NAME = r"^[^./][^/]*\.(md|markdown|txt)$"
"""A name with the extension of a type of text, neither a path nor a hidden file: a pattern in
ASCII, which the gateway checks as the service does."""
# The extensions a name ends with, for each type of text a file is created in
EXTENSIONS = {"text/markdown": (".md", ".markdown"), "text/plain": (".txt",)}
# What no name holds, as no text others wrote keeps it: what a reader does not see (Unicode's Cc,
# Cf and Cs), such as U+202E, which reverses what follows it, or U+200B, which shows nothing; and
# the line and paragraph separators (Zl and Zp), which break a name over lines
UNSEEN = {"Cc", "Cf", "Cs", "Zl", "Zp"}


class NewFile(BaseModel):
    """A text file to create: in which folder, its name, its content and its type."""

    # A field the contract does not take, such as one asking to replace a file, is refused
    model_config = ConfigDict(extra="forbid")

    folder_id: str = Field(
        pattern=ITEM_ID,
        description="The id of the folder, as list_folder_items or search_files gave it, or root "
        "for the top of the user's Drive.",
    )
    name: str = Field(
        max_length=255,
        pattern=NAME,
        description="The name of the file, at most 255 characters, ending in .md or .markdown "
        "for text/markdown and in .txt for text/plain: neither a path nor a hidden file, and "
        "without control, invisible or direction-changing characters, such as U+202E or U+200B.",
    )
    content: str = Field(
        max_length=LARGEST, description="The text of the file, at most 1 MiB in UTF-8."
    )
    mime: Literal["text/markdown", "text/plain"] = Field(
        description="text/markdown for Markdown, text/plain for plain text."
    )


def _encoded(new: NewFile) -> bytes:
    """The content to write, once the name, whose form its pattern already holds, and the content
    are found right."""
    if any(unicodedata.category(character) in UNSEEN for character in new.name):
        raise invalid_request("name: no control, invisible or direction-changing character")
    extensions = EXTENSIONS[new.mime]
    if not new.name.endswith(extensions):
        raise invalid_request(f"name: a {new.mime} file ends with {' or '.join(extensions)}")
    content = new.content.encode()
    if len(content) > LARGEST:
        raise invalid_request(f"content: at most {LARGEST} bytes in UTF-8")
    return content


def router(drive: Drive, drive_owner: DriveOwnerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/drive", tags=["drive.file.create.v1"])

    @routes.post(
        "/files",
        operation_id="create_file",
        status_code=201,
        summary="Create a text file in one of the user's folders",
        description=(
            "Creates a text file in the user's own Drive: Markdown, with mime text/markdown and a "
            "name ending in .md, or plain text, with text/plain and .txt, at most 1 MiB. "
            "folder_id is one of the user's folders, or root for the top of their Drive. The file "
            "appears in the user's Drive, where only they see it, and nobody else is notified: a "
            "folder shared with other people, by a sharing, a shared drive or a link, or inside "
            "such a folder, is refused with folder_shared. A name already in the folder is "
            "refused with name_taken: nothing is ever replaced, nor renamed. web_url opens the "
            "new file in Drive for the user. Example: body="
            '{"folder_id": "root", "name": "Meeting notes.md", "content": "# Meeting notes\\n\\n'
            '- Budget approved", "mime": "text/markdown"}.'
        ),
        # A new file in the user's own folders, where nobody else sees it, never over another:
        # the owner's consent to write in Drive covers it, and they are not asked to confirm each
        openapi_extra={"x-twake-risk": "low"},
    )
    async def create_file(
        new: NewFile, owner: Annotated[DriveOwner, Depends(drive_owner)]
    ) -> DriveItem:
        content = _encoded(new)
        folder = await drive.item(owner, ROOT_ID if new.folder_id == "root" else new.folder_id)
        if folder is None or folder.type != "directory" or folder.in_trash:
            raise folder_not_found(new.folder_id)
        # A folder in a shared folder is shared with it, by a sharing as by a link
        folders = [folder, *await drive.parents(owner, folder)]
        if any(item.shared for item in folders) or {
            item.id for item in folders
        } & await drive.linked_ids(owner):
            raise Problem(
                status=409,
                code="folder_shared",
                title="Folder shared",
                detail="The folder is shared with other people, or is in a shared folder: files "
                "are created only where nobody else sees them. Choose another folder, or let the "
                "user add the file in Drive.",
            )
        # Before the write: failing after it would leave a file that the call made again would
        # find in its way, as name_taken
        app = await drive.app(owner)
        created = await drive.create_file(owner, folder.id, new.name, content, new.mime)
        if created is None:
            raise folder_not_found(new.folder_id)
        return DriveItem.of(created, app, posixpath.join(folder.path or "/", new.name))

    return routes
