"""space.feed.read.v1: the feeds of the user's spaces in Twake Space, their Fil: a card per object
of their apps, such as a file, an event, a task or an email, the posts of their members, and the
reactions to both."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel, Field

from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.space import (
    EXAMPLE_ITEM,
    EXAMPLE_SPACE,
    LONGEST_NAME,
    MOST_READ,
    NO_POSTING,
    UNTRUSTED,
    ItemId,
    SpaceId,
)
from twake_space_agent_contracts.space.backend import (
    SPACE_ID,
    Actor,
    FeedItem,
    SpaceOwner,
    SpaceOwnerDependency,
    SpaceSummary,
    TwakeSpace,
    feed_item_not_found,
    for_each,
)
from twake_space_agent_contracts.space.spaces import FEED_URL
from twake_space_agent_contracts.text import line, paragraphs_within

LONGEST_TITLE = 500
LONGEST_PREVIEW = 1000
LONGEST_TEXT = 4000
"""The longest post Space takes."""
LONGEST_KEY = 16
"""The longest reaction Space takes, in characters."""
LONGEST_STATE_TEXT = 500
MOST_STATE_ENTRIES = 20
DEEPEST_STATE = 3
MOST_ITEMS = 50
"""The most items Space gives of a feed at once."""
RECENT = timedelta(days=7)
"""How far back the feeds of all the user's spaces go, unless told."""

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


class ItemSpaceText(BaseModel):
    name: str | None


class ItemSpace(BaseModel):
    """The space whose feed holds an item."""

    space_id: str
    url: str = Field(description=FEED_URL)
    untrusted: ItemSpaceText


class ListedFeedItem(SpaceFeedItem):
    space: ItemSpace


class SpaceFeed(BaseModel):
    items: list[ListedFeedItem]
    next: str | None = Field(
        description="With space_id, the cursor of the older items, to pass as before; null after "
        "the last, and without space_id."
    )
    truncated: bool = Field(
        description="Without space_id, whether items since since were left out: more than limit, "
        f"or in the feeds of the spaces after the first {MOST_READ}. False with space_id, where "
        "next tells of older items."
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
            text=paragraphs_within(item.body, LONGEST_TEXT)[0],
            title=line(item.title, LONGEST_TITLE)[0],
            preview=line(item.preview, LONGEST_PREVIEW)[0],
            object_id=line(item.object_id, LONGEST_TITLE)[0],
            state=_cleaned(item.state) if card else None,
        ),
    )


def _item_space(space: TwakeSpace, space_id: str, name: str) -> ItemSpace:
    """The space whose feed holds an item, as the contracts name it."""
    return ItemSpace(
        space_id=space_id,
        url=space.feed_url(space_id),
        untrusted=ItemSpaceText(name=line(name, LONGEST_NAME)[0]),
    )


def _listed(item: FeedItem, me: str | None, held_in: ItemSpace) -> ListedFeedItem:
    """An item of a list of the feeds, with the space whose feed holds it."""
    return ListedFeedItem(**dict(feed_item(item, me)), space=held_in)


async def _all_feeds(
    space: TwakeSpace,
    owner: SpaceOwner,
    *,
    category: Category | None,
    limit: int,
    since: datetime,
) -> SpaceFeed:
    """The items of the feeds of the user's first MOST_READ spaces since `since`, newest first,
    `limit` at most."""
    summaries = await space.spaces(owner)
    read = summaries[:MOST_READ]
    # The user has the same user id in every space: the first one that holds them tells it. A
    # token of the organization reaches the spaces the user is not a member of too
    me = None
    for summary in read:
        detail = await space.found_space(owner, summary.space_id)
        me = None if detail is None else detail.user_id_of(owner.user.email)
        if me is not None:
            break
    # One item more than limit tells whether a feed holds more since then, as far as Space gives
    asked = min(limit + 1, MOST_ITEMS)

    async def page_of(summary: SpaceSummary) -> tuple[list[FeedItem], str | None] | None:
        return await space.found_feed(
            owner, summary.space_id, category=category, limit=asked, before=None
        )

    pages = await for_each(read, page_of)
    found: list[tuple[FeedItem, ItemSpace]] = []
    more = len(summaries) > MOST_READ
    for summary, page in zip(read, pages, strict=True):
        # The user left the space once Space listed it, or it was deleted
        if page is None:
            continue
        items, following = page
        held_in = _item_space(space, summary.space_id, summary.name)
        found += [(item, held_in) for item in items if item.time >= since]
        # Older items follow the last one Space gave, which is since then too
        more = more or (following is not None and len(items) > 0 and items[-1].time >= since)
    # As each feed orders them
    found.sort(key=lambda pair: (pair[0].time, pair[0].item_id), reverse=True)
    return SpaceFeed(
        items=[_listed(item, me, held_in) for item, held_in in found[:limit]],
        next=None,
        truncated=more or len(found) > limit,
    )


def router(
    space: TwakeSpace, owner_of: SpaceOwnerDependency, now: Callable[[], datetime]
) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.feed.read.v1"])

    @routes.get(
        "/feed",
        operation_id="list_feed_items",
        summary="Read the feeds of the user's spaces in Twake Space",
        description=(
            "Reads the feeds of the spaces the user you act for is a member of, their Fil, newest "
            "first: a card per object of a space's apps, such as a file, an event, a task or an "
            "email, at the time of the object's first activity and showing its latest, and the "
            "posts the members wrote, each with its reactions and the space it is in. Without "
            f"space_id, the feeds of the user's spaces, the first {MOST_READ} by name, merged: "
            "the items since since, 7 days ago by default, limit at most, truncated telling that "
            "more were left out. With space_id, the feed of that space alone: when next is not "
            "null, older items follow: make the same call with before set to it. category keeps "
            "messages, the posts and the mail, files, activities, such as tasks, or events. "
            f"{NO_POSTING} {UNTRUSTED} Example, for what is new in the user's spaces: (no "
            f"parameters); for the latest files of a space: {EXAMPLE_SPACE}, category=files, "
            "limit=20."
        ),
    )
    async def list_feed_items(
        owner: Annotated[SpaceOwner, Depends(owner_of)],
        space_id: Annotated[
            str | None,
            Query(
                pattern=SPACE_ID,
                description="The space_id of a space, as list_spaces gives it, for its feed "
                "alone; the feeds of all the user's spaces without it.",
            ),
        ] = None,
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
                description="With space_id, the next of the previous answer, for the items "
                "older than it.",
            ),
        ] = None,
        since: Annotated[
            AwareDatetime | None,
            Query(
                description="An RFC 3339 time with its offset, such as "
                "2026-10-01T00:00:00+02:00: the items of a time since then. Without space_id, 7 "
                "days ago by default; with it, the whole feed by default."
            ),
        ] = None,
    ) -> SpaceFeed:
        if space_id is None:
            # A cursor of Space names an item, not the feed it is in
            if before is not None:
                raise invalid_request(
                    "before: A cursor pages the feed of one space: give the space_id of the call "
                    "whose next it is."
                )
            return await _all_feeds(
                space, owner, category=category, limit=limit, since=since or now() - RECENT
            )
        detail = await space.space(owner, space_id)
        items, following = await space.feed(
            owner, space_id, category=category, limit=limit, before=before
        )
        if since is not None:
            recent = [item for item in items if item.time >= since]
            # The items Space gives next are older still
            following = following if len(recent) == len(items) else None
            items = recent
        me = detail.user_id_of(owner.user.email)
        held_in = _item_space(space, detail.space_id, detail.name)
        return SpaceFeed(
            items=[_listed(item, me, held_in) for item in items], next=following, truncated=False
        )

    @routes.get(
        "/spaces/{space_id}/feed/items/{item_id}",
        operation_id="read_feed_item",
        summary="Read an item of the feed of one of the user's spaces in Twake Space",
        description=(
            "Reads a card or a post of the feed of a space the user you act for is a member of, "
            f"by the item_id list_feed_items gives, with its reactions. {NO_POSTING} {UNTRUSTED} "
            f"Example: {EXAMPLE_ITEM}."
        ),
    )
    async def read_feed_item(
        space_id: SpaceId, item_id: ItemId, owner: Annotated[SpaceOwner, Depends(owner_of)]
    ) -> SpaceFeedItem:
        me = (await space.space(owner, space_id)).user_id_of(owner.user.email)
        item = await space.item(owner, space_id, item_id)
        if item is None:
            raise feed_item_not_found(space_id, item_id)
        return feed_item(item, me)

    return routes
