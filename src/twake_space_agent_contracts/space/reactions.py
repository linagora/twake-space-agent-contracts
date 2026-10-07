"""space.reaction.add.v1 and space.reaction.remove.v1: the user reacts to an item of the feed of one
of their spaces in Twake Space, with a reaction Space offers, and takes a reaction back."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import Previewing, digest_of
from twake_space_agent_contracts.space import EXAMPLE_ITEM, UNTRUSTED, ItemId, SpaceId
from twake_space_agent_contracts.space.backend import FeedItem, TwakeSpace, feed_item_not_found
from twake_space_agent_contracts.space.feed import SpaceFeedItem, feed_item
from twake_space_agent_contracts.space.summaries import reacting, unreacting

Key = Literal[
    "\N{THUMBS UP SIGN}",
    "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}",
    "\N{FACE WITH TEARS OF JOY}",
    "\N{PARTY POPPER}",
    "\N{EYES}",
    "\N{PERSON WITH FOLDED HANDS}",
]
"""The reactions Twake Space offers in its feed."""


class NewReaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Key = Field(description="The reaction, one of those Twake Space offers.")


def _acted_on(item: FeedItem) -> dict[str, str | None]:
    """What a reaction acts on, as a preview shows the item: a card by its title, a post by its
    author and its text."""
    return {
        "item_id": item.item_id,
        "kind": item.kind,
        "title": item.title,
        "author": item.by.user_id if item.by else None,
        "text": item.body,
    }


def _add(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.reaction.add.v1"])

    @routes.post(
        "/spaces/{space_id}/feed/items/{item_id}/reactions",
        operation_id="add_feed_reaction",
        summary="React to an item of the feed of one of the user's spaces in Twake Space",
        description=(
            "Reacts, as the user you act for, to a card or a post of the feed of a space they are "
            "a member of, by the item_id list_feed_items gives, with one of the reactions Twake "
            "Space offers: the members of the space see it. It answers the item, with its "
            f"reactions. {UNTRUSTED} Example, for a thumbs up: {EXAMPLE_ITEM}, "
            'body={"key": "\N{THUMBS UP SIGN}"}.'
        ),
        response_model=SpaceFeedItem,
        # The user's own reaction, which they take back at will: the owner's consent to write in
        # Space covers it, and they are not asked to confirm each one. It tells what it would do,
        # for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def add_feed_reaction(
        space_id: SpaceId,
        item_id: ItemId,
        reaction: NewReaction,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceFeedItem | JSONResponse:
        detail = await space.space(user, space_id)
        me = detail.user_id_of(user.email)
        item = await space.item(user, space_id, item_id)
        if item is None:
            raise feed_item_not_found(space_id, item_id)
        already = item.reacted(me, reaction.key)
        # What the owner allows: the item as they were shown it, and whether the user reacted
        # so already
        digest = digest_of(space_id, _acted_on(item), reaction.key, already)
        if preview.asked:
            summary = reacting(item, me, detail.name, reaction.key, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        # Space keeps a reaction once: one the user made already changes nothing
        if already:
            return feed_item(item, me)
        await space.react(user, space_id, item_id, reaction.key)
        now = await space.item(user, space_id, item_id)
        if now is None:
            raise feed_item_not_found(space_id, item_id)
        return feed_item(now, me)

    return routes


def _remove(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.reaction.remove.v1"])

    @routes.post(
        "/spaces/{space_id}/feed/items/{item_id}/reactions/remove",
        operation_id="remove_feed_reaction",
        summary="Take a reaction back from an item of the feed of a space in Twake Space",
        description=(
            "Takes back a reaction of the user you act for to a card or a post of the feed of a "
            "space they are a member of, by the item_id list_feed_items gives: only their own, "
            "which me tells. It answers the item, with its reactions. "
            f"{UNTRUSTED} Example, to take a thumbs up back: {EXAMPLE_ITEM}, "
            'body={"key": "\N{THUMBS UP SIGN}"}.'
        ),
        response_model=SpaceFeedItem,
        # The user's own reaction, which they make again at will: the owner's consent to write in
        # Space covers it, and they are not asked to confirm each one. It tells what it would do,
        # for when they are.
        openapi_extra={"x-twake-risk": "low", "x-twake-preview": True},
    )
    async def remove_feed_reaction(
        space_id: SpaceId,
        item_id: ItemId,
        reaction: NewReaction,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceFeedItem | JSONResponse:
        detail = await space.space(user, space_id)
        me = detail.user_id_of(user.email)
        item = await space.item(user, space_id, item_id)
        if item is None:
            raise feed_item_not_found(space_id, item_id)
        made = item.reacted(me, reaction.key)
        # What the owner allows: the item as they were shown it, and whether the user reacted so
        digest = digest_of(space_id, _acted_on(item), reaction.key, made)
        if preview.asked:
            summary = unreacting(item, me, detail.name, reaction.key, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        # Only the user's own reaction is taken back: there is nothing else to take
        if not made:
            return feed_item(item, me)
        await space.unreact(user, space_id, item_id, reaction.key)
        now = await space.item(user, space_id, item_id)
        if now is None:
            raise feed_item_not_found(space_id, item_id)
        return feed_item(now, me)

    return routes


def routers(space: TwakeSpace, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the two contracts, one each."""
    return [_add(space, caller), _remove(space, caller)]
