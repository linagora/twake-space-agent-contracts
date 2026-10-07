"""space.feed.read.v1: the feed of a space of the user in Twake Space, its Fil: a card per object of
its apps, such as a file, an event, a task or an email, the posts of its members, and the reactions
to both."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.space import (
    EXAMPLE_ITEM,
    EXAMPLE_SPACE,
    UNTRUSTED,
    ItemId,
    SpaceId,
)
from twake_space_agent_contracts.space.backend import (
    Actor,
    FeedItem,
    TwakeSpace,
    feed_item_not_found,
)
from twake_space_agent_contracts.text import line, paragraphs

LONGEST_NAME = 255
LONGEST_TITLE = 500
LONGEST_PREVIEW = 1000
LONGEST_TEXT = 4000
"""The longest post Space takes."""
LONGEST_KEY = 16
"""The longest reaction Space takes, in characters."""
LONGEST_STATE_TEXT = 500
MOST_STATE_ENTRIES = 20
DEEPEST_STATE = 3

Category = Literal["messages", "files", "activities", "events"]
CATEGORIES = (
    "messages, the posts and the mail; files; activities, such as tasks; or events, of the "
    "calendar."
)


class ByText(BaseModel):
    name: str | None = Field(
        description="The name the space knows the person by, or the name of the token."
    )


class By(BaseModel):
    """Who made the latest activity of a card, or wrote a post."""

    kind: Literal["user", "token", "deleted_user"] = Field(
        description="user, a person; token, an application acting with a token of Space; "
        "deleted_user, someone whose account is deleted."
    )
    user_id: str | None = Field(
        description="The user_id of the member, as read_space gives it; null for someone outside "
        "the space, and for any other kind."
    )
    you: bool = Field(description="Whether it is the user you act for.")
    untrusted: ByText


class FeedObject(BaseModel):
    """The object of an app a card is about."""

    type: str = Field(description="What it is, as its app names it, such as file, event or task.")
    container_kind: str | None = Field(
        description="What of the space's apps it lies in: project, the Tasks project; "
        "matrix_space, the Chat room; mailbox; calendar; drive; null for none."
    )
    container_id: str | None = Field(description="Its id, as read_space gives the space's.")


class ReactionText(BaseModel):
    key: str = Field(description="The reaction, such as an emoji.")


class FeedReaction(BaseModel):
    count: int = Field(description="How many people reacted so.")
    mine: bool = Field(description="Whether the user you act for is one of them.")
    untrusted: ReactionText


class ItemText(BaseModel):
    """What people wrote: a post's text; a card's title, preview and object, and what its app
    tells of the object."""

    text: str | None = Field(description="A post's text, in plain text; null for a card.")
    title: str | None = Field(
        description="The title of a card's object, such as a file's name; null for a post."
    )
    preview: str | None
    object_id: str | None = Field(description="The id of a card's object, as its app gives it.")
    state: Any = Field(
        default=None,
        description="What the app of a card tells of its object, such as an event's times or "
        "where it takes place; null for a post.",
    )


class SpaceFeedItem(BaseModel):
    """An item of the feed: a card or a post."""

    item_id: str
    kind: Literal["card", "post"] = Field(
        description="card, the latest activity of an app on one object, which keeps the place of "
        "the object's first; post, what a member wrote."
    )
    category: str = Field(description=CATEGORIES)
    time: datetime = Field(description="When the object's first activity was, or the post written.")
    updated_at: datetime
    edited_at: datetime | None = Field(description="When a post was last edited; null until then.")
    event_type: str | None = Field(
        description="The type of a card's latest activity, such as "
        "com.twake.drive.file.updated.v1; null for a post."
    )
    object: FeedObject | None = Field(description="What a card is about; null for a post.")
    by: By | None = Field(description="null for an activity no one in particular made.")
    reactions: list[FeedReaction] = Field(description="In the order they were first added.")
    untrusted: ItemText


class SpaceFeed(BaseModel):
    items: list[SpaceFeedItem]
    next: str | None = Field(
        description="The cursor of the older items, to pass as before; null after the last."
    )


def _cleaned(value: Any, depth: int = 0) -> Any:
    """What an app tells of an object, as the contracts give it back: each text on one line,
    without what a reader does not see, cut after LONGEST_STATE_TEXT characters; the first
    MOST_STATE_ENTRIES entries of each list or object, DEEPEST_STATE levels deep, deeper ones
    null."""
    if isinstance(value, str):
        return line(value, LONGEST_STATE_TEXT)[0]
    if value is None or isinstance(value, bool | int | float):
        return value
    if depth >= DEEPEST_STATE:
        return None
    if isinstance(value, list):
        return [_cleaned(item, depth + 1) for item in value[:MOST_STATE_ENTRIES]]
    if isinstance(value, dict):
        entries = list(value.items())[:MOST_STATE_ENTRIES]
        return {
            name: _cleaned(item, depth + 1)
            for key, item in entries
            if (name := line(str(key), LONGEST_NAME)[0])
        }
    return None


def _by(actor: Actor | None, me: str | None) -> By | None:
    if actor is None:
        return None
    return By(
        kind=actor.kind,
        user_id=actor.user_id,
        you=actor.user_id is not None and actor.user_id == me,
        untrusted=ByText(name=line(actor.name, LONGEST_NAME)[0]),
    )


def feed_item(item: FeedItem, me: str | None) -> SpaceFeedItem:
    """An item as the contracts give it to the user, whose user id in the space is `me`, None if
    the contract cannot tell it."""
    card = item.kind == "card"
    return SpaceFeedItem(
        item_id=item.item_id,
        kind=item.kind,
        category=item.category,
        time=item.time,
        updated_at=item.updated_at,
        edited_at=item.edited_at,
        event_type=item.event_type,
        object=FeedObject(
            type=item.object_type or "",
            container_kind=item.container_kind,
            container_id=item.container_id,
        )
        if card
        else None,
        by=_by(item.by, me),
        reactions=[
            FeedReaction(
                count=len(reaction.user_ids),
                mine=me is not None and me in reaction.user_ids,
                untrusted=ReactionText(key=line(reaction.key, LONGEST_KEY)[0] or ""),
            )
            for reaction in item.reactions
        ],
        untrusted=ItemText(
            text=paragraphs(item.body, LONGEST_TEXT)[0],
            title=line(item.title, LONGEST_TITLE)[0],
            preview=line(item.preview, LONGEST_PREVIEW)[0],
            object_id=line(item.object_id, LONGEST_TITLE)[0],
            state=_cleaned(item.state) if card else None,
        ),
    )


def router(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.feed.read.v1"])

    @routes.get(
        "/spaces/{space_id}/feed",
        operation_id="list_feed_items",
        summary="Read the feed of one of the user's spaces in Twake Space",
        description=(
            "Reads the feed of a space the user you act for is a member of, its Fil, newest first: "
            "a card per object of the space's apps, such as a file, an event, a task or an email, "
            "showing its latest activity, and the posts its members wrote, each with its "
            "reactions. category keeps messages, the posts and the mail, files, activities, such "
            f"as tasks, or events. {UNTRUSTED} Example, for the latest files of a space: "
            f"{EXAMPLE_SPACE}, category=files, limit=20."
        ),
    )
    async def list_feed_items(
        space_id: SpaceId,
        user: Annotated[User, Depends(caller)],
        category: Annotated[
            Category | None,
            Query(description="messages, files, activities or events; all of them by default."),
        ] = None,
        limit: Annotated[
            int, Query(ge=1, le=50, description="How many items to give, 20 by default.")
        ] = 20,
        before: Annotated[
            str | None,
            Query(
                pattern=r"^[A-Za-z0-9_-]{1,200}$",
                description="The next of the previous answer, for the items older than it.",
            ),
        ] = None,
    ) -> SpaceFeed:
        me = (await space.space(user, space_id)).user_id_of(user.email)
        items, following = await space.feed(
            user, space_id, category=category, limit=limit, before=before
        )
        return SpaceFeed(items=[feed_item(item, me) for item in items], next=following)

    @routes.get(
        "/spaces/{space_id}/feed/items/{item_id}",
        operation_id="read_feed_item",
        summary="Read an item of the feed of one of the user's spaces in Twake Space",
        description=(
            "Reads a card or a post of the feed of a space the user you act for is a member of, "
            f"by the item_id list_feed_items gives, with its reactions. {UNTRUSTED} Example: "
            f"{EXAMPLE_ITEM}."
        ),
    )
    async def read_feed_item(
        space_id: SpaceId, item_id: ItemId, user: Annotated[User, Depends(caller)]
    ) -> SpaceFeedItem:
        me = (await space.space(user, space_id)).user_id_of(user.email)
        item = await space.item(user, space_id, item_id)
        if item is None:
            raise feed_item_not_found(space_id, item_id)
        return feed_item(item, me)

    return routes
