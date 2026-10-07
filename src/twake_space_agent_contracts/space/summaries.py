"""What the writes of Twake Space tell the owner they would do, in their language."""

from dataclasses import dataclass
from typing import Literal, get_args

from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    excerpt,
    fitted,
    one_line,
    person,
    quoted,
    shown_size,
)
from twake_space_agent_contracts.space.backend import FeedItem, Member, Person

FORMER = 1_000
"""What the preview of a change keeps at least of its summary for the former text of the post: its
beginning, and the line that says how much of it is left out."""

Form = Literal["to", "on", "the"]
"""How a summary names an item: as what a reaction goes to, what it is taken back on, or as
itself."""


@dataclass(frozen=True)
class _Words:
    """What a preview of a write in Space tells the owner, in one language."""

    space: str
    post: str
    edit: str
    instead: str
    unedited: str
    delete: str
    add: str
    left: str
    unchanged: str
    roles: dict[str, str]
    """Each role, for several people."""
    role: dict[str, str]
    """Each role, for one person."""
    change_role: str
    admin_powers: str
    same_role: str
    remove: str
    remove_yourself: str
    through_group: str
    through_group_yourself: str
    seen_by: tuple[str, str]
    """Who sees what the user posts: the one member, and more members."""
    react: str
    reacted: str
    unreact: str
    unreact_if_made: str
    not_reacted: str
    items: dict[str, dict[Form, str]]
    """How an item is named in each form: the user's post, someone's post, a post of no one the
    space knows, and a card."""


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        space="l'espace {name}",
        post="Publier dans le fil de {space}, {seen}",
        edit="Modifier {item} dans le fil de {space}, {seen}, en :",
        instead="Au lieu de :",
        unedited="{item} dans le fil de {space} dit déjà cela : rien ne change.",
        delete="Supprimer {item} du fil de {space}, définitivement, avec ses réactions",
        add="Ajouter à {space}, comme {roles}, ces personnes, qui en voient alors tout le"
        " contenu :",
        left="Déjà membres, laissés tels quels :",
        unchanged="Rien ne change dans {space} : ces personnes y sont déjà {roles} :",
        roles={"viewer": "lecteurs", "editor": "éditeurs", "admin": "administrateurs"},
        role={"viewer": "lecteur", "editor": "éditeur", "admin": "administrateur"},
        change_role="Faire de {person} un {role} de {space}, au lieu d'un {former}",
        admin_powers=" : cette personne ajoute alors, modifie et retire ses membres",
        same_role="{person} est déjà {role} de {space} : rien ne change.",
        remove="Retirer {person} de {space} : cette personne n'en voit plus le contenu.",
        remove_yourself="Te retirer de {space} : tu n'en vois plus le contenu.",
        through_group="Sauf si elle en est membre par un groupe lié, et le reste tant que le groupe"
        " est lié et qu'elle en fait partie.",
        through_group_yourself="Sauf si tu en es membre par un groupe lié, et le restes tant que"
        " le groupe est lié et que tu en fais partie.",
        seen_by=("que son seul membre voit", "que ses {count} membres voient"),
        react="Réagir avec {key} {item} dans {space}, que ses membres voient",
        reacted="Tu as déjà réagi avec {key} {item} dans {space} : rien ne change.",
        unreact="Retirer ton {key} {item} dans {space}.",
        unreact_if_made="Retirer ton {key} {item} dans {space}, si tu l'as mis.",
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
        post="Post in the feed of {space}, {seen}",
        edit="Change {item} in the feed of {space}, {seen}, to:",
        instead="Instead of:",
        unedited="{item} in the feed of {space} says so already: nothing changes.",
        delete="Delete {item} from the feed of {space}, for good, with its reactions",
        add="Add to {space}, as {roles}, these people, who then see all it holds:",
        left="Members already, left as they are:",
        unchanged="Nothing changes in {space}: these people are {roles} there already:",
        roles={"viewer": "viewers", "editor": "editors", "admin": "admins"},
        role={"viewer": "viewer", "editor": "editor", "admin": "admin"},
        change_role="Make {person} {article} {role} of {space}, instead of {former_article}"
        " {former}",
        admin_powers=": they then add, change and remove its members",
        same_role="{person} is {article} {role} of {space} already: nothing changes.",
        remove="Remove {person} from {space}: they no longer see what it holds.",
        remove_yourself="Remove yourself from {space}: you no longer see what it holds.",
        through_group="Unless they are a member through a linked group, who stays one while the"
        " group is linked and they are in it.",
        through_group_yourself="Unless you are a member through a linked group, and stay one"
        " while the group is linked and you are in it.",
        seen_by=("which its only member sees", "which its {count} members see"),
        react="React with {key} {item} in {space}, which its members see",
        reacted="You reacted with {key} {item} in {space} already: nothing changes.",
        unreact="Take back your {key} {item} in {space}.",
        unreact_if_made="Take back your {key} {item} in {space}, if you made it.",
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
    or that the user did not react so, when the contract can tell the user among the members."""
    words = _WORDS[language]
    space_name = space_named(space, language)
    if me is None:
        named = item_named(item, me, "on", language)
        return words.unreact_if_made.format(key=key, item=named, space=space_name)
    if not item.reacted(me, key):
        named = item_named(item, me, "to", language)
        return words.not_reacted.format(key=key, item=named, space=space_name)
    named = item_named(item, me, "on", language)
    return words.unreact.format(key=key, item=named, space=space_name)


def seen_by(count: int, language: Language) -> str:
    """Who sees what the user writes in a space: its members."""
    one, many = _WORDS[language].seen_by
    return one if count == 1 else many.format(count=count)


def _posting_head(space: str | None, members: int, language: Language) -> str:
    """The first line of the preview of a post, before its text."""
    words = _WORDS[language]
    head = words.post.format(space=space_named(space, language), seen=seen_by(members, language))
    return head + (" :" if language == "fr" else ":")


def posting_room(space: str | None, members: int) -> int:
    """What the preview of a new post leaves of its summary for the text, in whichever of its
    languages leaves less: the text a post takes, which the owner reads whole."""
    return min(
        BUDGET - shown_size(_posting_head(space, members, language) + "\n")
        for language in get_args(Language)
    )


def posting(space: str | None, members: int, text: str, language: Language) -> str:
    """What posting does, as the owner reads it: where the post goes, how many people see it,
    and its text, whole."""
    head = _posting_head(space, members, language)
    return head + "\n" + excerpt(text, posting_room(space, members), language)


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _editing_around(
    item: FeedItem, me: str | None, space: str | None, members: int, language: Language
) -> tuple[str, str]:
    """What the preview of a change tells before the new text, and between it and the former."""
    words = _WORDS[language]
    named = item_named(item, me, "the", language)
    seen = seen_by(members, language)
    head = words.edit.format(item=named, space=space_named(space, language), seen=seen)
    return head + "\n", "\n" + words.instead + "\n"


def editing_room(item: FeedItem, me: str | None, space: str | None, members: int) -> int:
    """What the preview of a change leaves of its summary for the new text, in whichever of its
    languages leaves less, FORMER kept for the former text: the new text a change takes, which
    the owner reads whole."""
    return min(
        BUDGET - shown_size(head + middle) - FORMER
        for head, middle in (
            _editing_around(item, me, space, members, language) for language in get_args(Language)
        )
    )


def editing(
    item: FeedItem, me: str | None, space: str | None, members: int, text: str, language: Language
) -> str:
    """What editing a post does, as the owner reads it: its new text, whole, and its former one,
    shortened to what the rest of the summary leaves, or that it says so already."""
    words = _WORDS[language]
    if item.body == text:
        named = _capitalized(item_named(item, me, "the", language))
        return words.unedited.format(item=named, space=space_named(space, language))
    head, middle = _editing_around(item, me, space, members, language)
    new = excerpt(text, editing_room(item, me, space, members), language)
    former = excerpt(item.body or "", BUDGET - shown_size(head + new + middle), language)
    return head + new + middle + former


def deleting(item: FeedItem, me: str | None, space: str | None, language: Language) -> str:
    """What deleting a post does, as the owner reads it: which post goes for good, and its
    text, whole when it fits."""
    words = _WORDS[language]
    named = item_named(item, me, "the", language)
    head = words.delete.format(item=named, space=space_named(space, language))
    return _with_text(head, item.body, language)


def _person_line(found: Person, room: int, language: Language) -> str:
    """A person on a line of its own after a tab, by their name and their email, as previews lay
    people out, within `room` of the summary: a name that would take more is cut, the email
    kept whole."""
    shown = person(found.display_name, found.email, language) or one_line(found.username)
    if shown_size("\t" + shown) <= room:
        return "\t" + shown
    # What the layout puts around a name: its quotation marks, and the email
    around = shown_size("\t" + (person("…", found.email, language) or "")) - shown_size("…")
    name = fitted(one_line(found.display_name), max(room - around, shown_size("…")))
    return "\t" + (person(name, found.email, language) or one_line(found.username))


def _people(people: list[Person], room: int, language: Language) -> list[str]:
    """People, each on a line of its own, each within `room` of the summary."""
    return [_person_line(found, room, language) for found in people]


def adding_members(
    space: str | None, role: str, added: list[Person], left: list[Person], language: Language
) -> str:
    """What adding people to a space does, as the owner reads it: whom it takes in and as what,
    and who are members already, or that nothing changes. Each person takes an equal share of
    what the summary leaves."""
    words = _WORDS[language]
    named = {"space": space_named(space, language), "roles": words.roles[role]}
    heads = [words.add.format(**named), words.left] if added else [words.unchanged.format(**named)]
    room = (BUDGET - shown_size("\n".join(heads))) // max(len(added) + len(left), 1) - 1
    if not added:
        return "\n".join([heads[0], *_people(left, room, language)])
    lines = [heads[0], *_people(added, room, language)]
    if left:
        lines += [words.left, *_people(left, room, language)]
    return "\n".join(lines)


def _article(role: str) -> str:
    """The English article of a role: an admin, an editor, a viewer."""
    return "an" if role[:1] in "aeiou" else "a"


def member_named(member: Member, language: Language) -> str:
    """A member, as a summary names them: by their name and their email."""
    return person(member.display_name, member.email, language) or one_line(member.username)


def changing_role(member: Member, role: str, space: str | None, language: Language) -> str:
    """What changing the role of a member does, as the owner reads it: their new role and their
    former one, and what an admin does, or that nothing changes."""
    words = _WORDS[language]
    named = {
        "person": member_named(member, language),
        "role": words.role[role],
        "article": _article(words.role[role]),
        "space": space_named(space, language),
    }
    if member.role == role:
        return words.same_role.format(**named)
    former = words.role.get(member.role, one_line(member.role))
    line = words.change_role.format(**named, former=former, former_article=_article(former))
    return line + (words.admin_powers if role == "admin" else "") + "."


def removing(member: Member, you: bool, space: str | None, groups: bool, language: Language) -> str:
    """What removing a member does, as the owner reads it: who leaves the space, maybe the user
    themselves, and, when the space links groups, that a member through one stays."""
    words = _WORDS[language]
    space_name = space_named(space, language)
    if you:
        line = words.remove_yourself.format(space=space_name)
    else:
        line = words.remove.format(person=member_named(member, language), space=space_name)
    if not groups:
        return line
    return line + "\n" + (words.through_group_yourself if you else words.through_group)
