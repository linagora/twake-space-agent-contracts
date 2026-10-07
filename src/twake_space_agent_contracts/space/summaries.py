"""What the writes of Twake Space tell the owner they would do, in their language."""

from dataclasses import dataclass
from typing import Literal

from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    excerpt,
    one_line,
    quoted,
    shown_size,
)
from twake_space_agent_contracts.space.backend import FeedItem

Form = Literal["to", "on", "the"]
"""How a summary names an item: as what a reaction goes to, what it is taken back on, or as
itself."""


@dataclass(frozen=True)
class _Words:
    """What a preview of a write in Space tells the owner, in one language."""

    space: str
    react: str
    reacted: str
    unreact: str
    not_reacted: str
    items: dict[str, dict[Form, str]]
    """How an item is named in each form: the user's post, someone's post, a post of no one the
    space knows, and a card."""


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        space="l'espace {name}",
        react="Réagir avec {key} {item} dans {space}, que ses membres voient",
        reacted="Tu as déjà réagi avec {key} {item} dans {space} : rien ne change.",
        unreact="Retirer ton {key} {item} dans {space}.",
        not_reacted="Tu n'as pas réagi avec {key} {item} dans {space} : rien ne change.",
        items={
            "own": {"to": "à ton message", "on": "sur ton message", "the": "ton message"},
            "post": {
                "to": "au message de {name}",
                "on": "sur le message de {name}",
                "the": "le message de {name}",
            },
            "unknown": {"to": "à un message", "on": "sur un message", "the": "un message"},
            "card": {
                "to": "à la carte {title}",
                "on": "sur la carte {title}",
                "the": "la carte {title}",
            },
        },
    ),
    "en": _Words(
        space="the space {name}",
        react="React with {key} {item} in {space}, which its members see",
        reacted="You reacted with {key} {item} in {space} already: nothing changes.",
        unreact="Take back your {key} {item} in {space}.",
        not_reacted="You have not reacted with {key} {item} in {space}: nothing changes.",
        items={
            "own": {"to": "to your post", "on": "on your post", "the": "your post"},
            "post": {
                "to": "to the post of {name}",
                "on": "on the post of {name}",
                "the": "the post of {name}",
            },
            "unknown": {"to": "to a post", "on": "on a post", "the": "a post"},
            "card": {
                "to": "to the card {title}",
                "on": "on the card {title}",
                "the": "the card {title}",
            },
        },
    ),
}


def space_named(name: str | None, language: Language) -> str:
    """A space, as a summary names it: by its name, between quotation marks."""
    return _WORDS[language].space.format(name=quoted(one_line(name), language))


def item_named(item: FeedItem, me: str | None, form: Form, language: Language) -> str:
    """An item of the feed, as a summary names it: a card by its title, a post by its author."""
    items = _WORDS[language].items
    if item.kind == "card":
        return items["card"][form].format(title=quoted(one_line(item.title), language))
    author = item.by
    if author is not None and author.user_id is not None and author.user_id == me:
        return items["own"][form]
    if author is None or author.kind != "user" or not one_line(author.name):
        return items["unknown"][form]
    return items["post"][form].format(name=quoted(one_line(author.name), language))


def _with_text(head: str, text: str | None, language: Language) -> str:
    """A summary's first line, then the text of a post, whole when it fits, each of its lines
    after a tab."""
    if not text:
        return head + "."
    head += " :" if language == "fr" else ":"
    return head + "\n" + excerpt(text, BUDGET - shown_size(head + "\n"), language)


def reacting(
    item: FeedItem, me: str | None, space: str | None, key: str, language: Language
) -> str:
    """What reacting to an item does, as the owner reads it: which item gets which reaction, and
    the text of a post, or that the user reacted so already."""
    words = _WORDS[language]
    named = {
        "key": key,
        "item": item_named(item, me, "to", language),
        "space": space_named(space, language),
    }
    if item.reacted(me, key):
        return words.reacted.format(**named)
    return _with_text(words.react.format(**named), item.body, language)


def unreacting(
    item: FeedItem, me: str | None, space: str | None, key: str, language: Language
) -> str:
    """What taking a reaction back does, as the owner reads it: which reaction leaves which item,
    or that the user did not react so."""
    words = _WORDS[language]
    space_name = space_named(space, language)
    if not item.reacted(me, key):
        named = item_named(item, me, "to", language)
        return words.not_reacted.format(key=key, item=named, space=space_name)
    named = item_named(item, me, "on", language)
    return words.unreact.format(key=key, item=named, space=space_name)
