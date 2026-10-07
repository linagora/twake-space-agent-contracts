"""space.reaction.add.v1 and space.reaction.remove.v1: the user reacts to an item of the feed of one
of their spaces in Twake Space, with a reaction Space offers, and takes a reaction back."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import Preview, Previewing, digest_of
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.space import EXAMPLE_ITEM, UNTRUSTED, ItemId, SpaceId
from twake_space_agent_contracts.space.backend import FeedItem, TwakeSpace, feed_item_not_found
from twake_space_agent_contracts.space.feed import LONGEST_KEY, SpaceFeedItem, feed_item
from twake_space_agent_contracts.space.summaries import reacting, unreacting

OFFERED = (
    "\N{THUMBS UP SIGN}",
    "\N{HEAVY BLACK HEART}\N{VARIATION SELECTOR-16}",
    "\N{FACE WITH TEARS OF JOY}",
    "\N{PARTY POPPER}",
    "\N{EYES}",
    "\N{PERSON WITH FOLDED HANDS}",
)
"""The reactions the Space web app offers in its feed."""

Key = Annotated[str, Field(min_length=1, max_length=LONGEST_KEY)]


class NewReaction(BaseModel):
    """The reaction to add."""

    model_config = ConfigDict(extra="forbid")

    key: Key = Field(
        description=f"The reaction: one of those Twake Space offers, {' '.join(OFFERED)}, or one "
        "the item has already, as reactions gives it, to join it."
    )


class OwnReaction(BaseModel):
    """A reaction of the user's, to take back."""

    model_config = ConfigDict(extra="forbid")

    key: Key = Field(
        description="The reaction to take back, as reactions gives it where mine is true."
    )


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


async def _react(
    space: TwakeSpace,
    user: User,
    space_id: str,
    item_id: str,
    key: str,
    preview: Preview,
    *,
    adding: bool,
) -> SpaceFeedItem | JSONResponse:
    """Adds the user's reaction to the item, or takes it back, as they would see the item once
    read again."""
    detail = await space.space(user, space_id)
    me = detail.user_id_of(user.email)
    item = await space.item(user, space_id, item_id)
    if item is None:
        raise feed_item_not_found(space_id, item_id)
    # The words of a reaction are the web app's, or someone's whom the user joins: never the model's
    if adding and key not in OFFERED and key not in {found.key for found in item.reactions}:
        raise invalid_request(
            f"key: Give one of the reactions Twake Space offers, {' '.join(OFFERED)}, or one the"
            " item has already."
        )
    reacted = item.reacted(me, key)
    # What the owner allows: the item as they were shown it, and whether the user reacted so
    digest = digest_of(space_id, _acted_on(item), key, reacted)
    if preview.asked:
        told = reacting if adding else unreacting
        return preview.answer(told(item, me, detail.name, key, preview.language), digest)
    preview.check(digest)
    if adding:
        # Space keeps a reaction once: one the user made already changes nothing
        if reacted:
            return feed_item(item, me)
        await space.react(user, space_id, item_id, key)
    else:
        # Space takes back the user's own reaction alone, which it knows when the contract may
        # not, as when no member has the user's email: it is always asked
        await space.unreact(user, space_id, item_id, key)
    now = await space.item(user, space_id, item_id)
    if now is None:
        raise feed_item_not_found(space_id, item_id)
    return feed_item(now, me)


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
        return await _react(space, user, space_id, item_id, reaction.key, preview, adding=True)

    return routes


def _remove(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.reaction.remove.v1"])

    @routes.post(
        "/spaces/{space_id}/feed/items/{item_id}/reactions/remove",
        operation_id="remove_feed_reaction",
        summary="Take a reaction back from an item of the feed of a space in Twake Space",
        description=(
            "Takes back a reaction of the user you act for to a card or a post of the feed of a "
            "space they are a member of, by the item_id list_feed_items gives, and its key, as "
            "reactions gives it where mine is true: Space takes back the user's own alone, and a "
            "reaction they did not make stays. It answers the item, with its reactions. "
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
        reaction: OwnReaction,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceFeedItem | JSONResponse:
        return await _react(space, user, space_id, item_id, reaction.key, preview, adding=False)

    return routes


def routers(space: TwakeSpace, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the two contracts, one each."""
    return [_add(space, caller), _remove(space, caller)]
