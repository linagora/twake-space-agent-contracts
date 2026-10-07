"""space.post.create.v1, space.post.update.v1 and space.post.delete.v1: the user posts in the feed
of one of their spaces in Twake Space, which every member sees, and edits and deletes their own
posts."""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from twake_space_agent_contracts.caller import CallerDependency, User
from twake_space_agent_contracts.previews import Previewing, digest_of
from twake_space_agent_contracts.problems import invalid_request
from twake_space_agent_contracts.space import (
    EXAMPLE_ITEM,
    EXAMPLE_SPACE,
    UNTRUSTED,
    ItemId,
    SpaceId,
)
from twake_space_agent_contracts.space.backend import (
    FeedItem,
    SpaceDetail,
    TwakeSpace,
    feed_item_not_found,
    forbidden_role,
    not_a_post,
    not_author,
)
from twake_space_agent_contracts.space.feed import LONGEST_TEXT, SpaceFeedItem, feed_item
from twake_space_agent_contracts.space.summaries import deleting, editing, posting


class PostText(BaseModel):
    """The text of a post, which every member of the space reads."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1,
        max_length=LONGEST_TEXT,
        description=f"Plain text, on several lines if need be, {LONGEST_TEXT} characters at most.",
    )


def _text_of(post: PostText) -> str:
    """The text as Space keeps it, without the blanks around it."""
    text = post.text.strip()
    if not text:
        raise invalid_request("text: A post's text cannot be blank.")
    return text


def _post_as_it_is(post: FeedItem) -> dict[str, str | None]:
    """What a change or a deletion of a post acts on: the post, its text and its last edit."""
    edited = post.edited_at
    return {
        "item_id": post.item_id,
        "text": post.body,
        "edited_at": edited.isoformat() if edited else None,
    }


def audience(detail: SpaceDetail) -> list[str]:
    """Who reads what is written in the space, by user id: its members, sorted, so that the
    same members make the same digest."""
    return sorted(member.user_id for member in detail.members)


async def _own_post(
    space: TwakeSpace, user: User, space_id: str, item_id: str, me: str | None
) -> FeedItem:
    """The post, if the user wrote it: Space does not say who the user is, so the member who
    has their email tells, and Space refuses anyone else's post at any rate."""
    item = await space.item(user, space_id, item_id)
    if item is None:
        raise feed_item_not_found(space_id, item_id)
    if item.kind != "post":
        raise not_a_post(space_id, item_id)
    author = item.by.user_id if item.by else None
    if me is not None and author != me:
        raise not_author(space_id, item_id)
    return item


def _create(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.post.create.v1"])

    @routes.post(
        "/spaces/{space_id}/feed/posts",
        operation_id="create_feed_post",
        status_code=201,
        summary="Post in the feed of one of the user's spaces in Twake Space",
        description=(
            "Posts a text, as the user you act for, in the feed of a space where they are an "
            "editor or an admin: every member of the space sees it in its feed. Call it only once "
            "the user asked to post this very text; they confirm each call. Each call posts "
            "anew: after an error, read the feed before calling again. It answers the post, as "
            f"read_feed_item gives it. {UNTRUSTED} Example, to tell the members a document is "
            f'ready: {EXAMPLE_SPACE}, body={{"text": "The roadmap is ready for review."}}.'
        ),
        response_model=SpaceFeedItem,
        # Every member of the space reads it: the owner confirms each one, shown what it would
        # post and where
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def create_feed_post(
        space_id: SpaceId,
        new: PostText,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceFeedItem | JSONResponse:
        text = _text_of(new)
        detail = await space.space(user, space_id)
        if detail.role == "viewer":
            raise forbidden_role(space_id)
        # What the owner allows: the space and who reads the post there, its members
        digest = digest_of(space_id, detail.name, audience(detail))
        if preview.asked:
            summary = posting(detail.name, len(detail.members), text, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        me = detail.member_named(user.email)
        created = await space.post(user, space_id, text)
        return feed_item(created, me.user_id if me else None)

    return routes


def _update(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.post.update.v1"])

    @routes.patch(
        "/spaces/{space_id}/feed/posts/{item_id}",
        operation_id="update_feed_post",
        summary="Edit a post of the user in the feed of a space in Twake Space",
        description=(
            "Replaces the text of a post the user you act for wrote in the feed of one of their "
            "spaces, by the item_id list_feed_items gives, where by.you tells their own: every "
            "member of the space sees the new text, marked as edited. Call it only once the user "
            "asked to change this very post; they confirm each call. It answers the post, as "
            f"read_feed_item gives it. {UNTRUSTED} Example, to fix a date: {EXAMPLE_ITEM}, "
            'body={"text": "The roadmap is ready for review by Monday."}.'
        ),
        response_model=SpaceFeedItem,
        # Every member of the space reads the new text: the owner confirms each change, shown
        # what it would write over
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def update_feed_post(
        space_id: SpaceId,
        item_id: ItemId,
        changed: PostText,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceFeedItem | JSONResponse:
        text = _text_of(changed)
        detail = await space.space(user, space_id)
        me = detail.member_named(user.email)
        mine = me.user_id if me else None
        post = await _own_post(space, user, space_id, item_id, mine)
        # What the owner allows: the post as it is, and who reads it
        digest = digest_of(space_id, _post_as_it_is(post), audience(detail))
        if preview.asked:
            members = len(detail.members)
            summary = editing(post, mine, detail.name, members, text, preview.language)
            return preview.answer(summary, digest)
        preview.check(digest)
        # Space would mark the post as edited, with nothing changed
        if post.body == text:
            return feed_item(post, mine)
        edited = await space.edit(user, space_id, item_id, text)
        return feed_item(edited, mine)

    return routes


def _delete(space: TwakeSpace, caller: CallerDependency) -> APIRouter:
    routes = APIRouter(prefix="/contracts/v1/space", tags=["space.post.delete.v1"])

    @routes.delete(
        "/spaces/{space_id}/feed/posts/{item_id}",
        operation_id="delete_feed_post",
        summary="Delete a post of the user from the feed of a space in Twake Space",
        description=(
            "Deletes, for good, a post the user you act for wrote in the feed of one of their "
            "spaces, by the item_id list_feed_items gives, where by.you tells their own, with "
            "its reactions: the members of the space no longer see it, and it comes back only if "
            "posted again. Call it only once the user asked to delete this very post; they "
            f"confirm each call. It answers the post as it was. {UNTRUSTED} Example: "
            f"{EXAMPLE_ITEM}."
        ),
        response_model=SpaceFeedItem,
        # A post the user loses for good, with the reactions of others: the owner confirms each
        # one, shown what it would delete
        openapi_extra={"x-twake-risk": "high", "x-twake-preview": True},
    )
    async def delete_feed_post(
        space_id: SpaceId,
        item_id: ItemId,
        user: Annotated[User, Depends(caller)],
        preview: Previewing,
    ) -> SpaceFeedItem | JSONResponse:
        detail = await space.space(user, space_id)
        me = detail.member_named(user.email)
        mine = me.user_id if me else None
        post = await _own_post(space, user, space_id, item_id, mine)
        # What the owner allows: the post as it is
        digest = digest_of(space_id, _post_as_it_is(post))
        if preview.asked:
            return preview.answer(deleting(post, mine, detail.name, preview.language), digest)
        preview.check(digest)
        await space.delete(user, space_id, item_id)
        return feed_item(post, mine)

    return routes


def routers(space: TwakeSpace, caller: CallerDependency) -> list[APIRouter]:
    """The routers of the three contracts, one each."""
    return [_create(space, caller), _update(space, caller), _delete(space, caller)]
