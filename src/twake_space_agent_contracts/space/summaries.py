"""What the writes of Twake Space on the members of a space tell the owner they would do, in their
language."""

from dataclasses import dataclass

from twake_space_agent_contracts.previews import (
    BUDGET,
    Language,
    fitted,
    one_line,
    person,
    quoted,
    shown_size,
)
from twake_space_agent_contracts.space.backend import Member


@dataclass(frozen=True)
class _Words:
    """What a preview of a write on the members of a space tells the owner, in one language."""

    space: str
    add: str
    unknown: str
    """What follows a username none of the user's spaces has."""
    left: str
    through_groups: str
    unchanged: str
    roles: dict[str, str]
    """Each role, for several people."""
    role: dict[str, str]
    """Each role, for one person."""
    change_role: str
    admin_powers: str
    group_role: str
    same_role: str
    remove: str
    remove_yourself: str
    through_group: str
    through_group_yourself: str


_WORDS: dict[Language, _Words] = {
    "fr": _Words(
        space="l'espace {name}",
        add="Ajouter à {space}, comme {roles}, ces personnes, qui en voient alors tout le"
        " contenu :",
        unknown=" (dans aucun de tes espaces : vérifie cet identifiant)",
        left="Déjà membres, laissés tels quels :",
        through_groups="Déjà membres, peut-être par un groupe lié : ceci en fait des membres"
        " directs, comme {roles}, et n'ajoute personne si l'un d'eux est membre direct d'un autre"
        " rôle :",
        unchanged="Rien ne change dans {space} : ces personnes y sont déjà {roles} :",
        roles={"viewer": "lecteurs", "editor": "éditeurs", "admin": "administrateurs"},
        role={"viewer": "lecteur", "editor": "éditeur", "admin": "administrateur"},
        change_role="Faire de {person} un {role} de {space}, au lieu d'un {former}",
        admin_powers=" : cette personne ajoute alors, modifie et retire ses membres",
        group_role="Si cette personne en est aussi membre par un groupe lié, elle garde le plus"
        " fort de ce rôle et de celui du groupe.",
        same_role="{person} est déjà {role} de {space} : rien ne change.",
        remove="Retirer {person} de {space} : cette personne n'en voit plus le contenu.",
        remove_yourself="Te retirer de {space} : tu n'en vois plus le contenu.",
        through_group="Sauf si elle en est membre par un groupe lié, et le reste tant que le groupe"
        " est lié et qu'elle en fait partie.",
        through_group_yourself="Sauf si tu en es membre par un groupe lié, et le restes tant que"
        " le groupe est lié et que tu en fais partie.",
    ),
    "en": _Words(
        space="the space {name}",
        add="Add to {space}, as {roles}, these people, who then see all it holds:",
        unknown=" (in none of your spaces: check this username)",
        left="Members already, left as they are:",
        through_groups="Members already, maybe through a linked group: this makes them direct"
        " members, as {roles}, and adds nobody if one of them is a direct member of another role:",
        unchanged="Nothing changes in {space}: these people are {roles} there already:",
        roles={"viewer": "viewers", "editor": "editors", "admin": "admins"},
        role={"viewer": "viewer", "editor": "editor", "admin": "admin"},
        change_role="Make {person} {article} {role} of {space}, instead of {former_article}"
        " {former}",
        admin_powers=": they then add, change and remove its members",
        group_role="If they are a member through a linked group too, they keep the stronger of"
        " this role and the group's.",
        same_role="{person} is {article} {role} of {space} already: nothing changes.",
        remove="Remove {person} from {space}: they no longer see what it holds.",
        remove_yourself="Remove yourself from {space}: you no longer see what it holds.",
        through_group="Unless they are a member through a linked group, who stays one while the"
        " group is linked and they are in it.",
        through_group_yourself="Unless you are a member through a linked group, and stay one"
        " while the group is linked and you are in it.",
    ),
}


def space_named(name: str | None, language: Language) -> str:
    """A space, as a summary names it: by its name, between quotation marks."""
    return _WORDS[language].space.format(name=quoted(one_line(name), language))


def member_named(member: Member, language: Language) -> str:
    """A member, as a summary names them: by their name and their email."""
    return person(member.display_name, member.email, language) or one_line(member.username)


def _person_line(found: Member | str, after: str, room: int, language: Language) -> str:
    """A person on a line of its own after a tab, as previews lay people out, then `after`, within
    `room` of the summary: a member of the user's spaces by their name and their email, a name
    that would take more cut, the email kept whole; anyone else by the username the call gives,
    cut if need be, said to be checked."""
    if isinstance(found, str):
        after = _WORDS[language].unknown + after
        around = shown_size("\t" + quoted("", language) + after)
        return "\t" + quoted(fitted(one_line(found), max(room - around, 1)), language) + after
    shown = member_named(found, language)
    if shown_size("\t" + shown + after) <= room:
        return "\t" + shown + after
    # What the layout puts around a name: its quotation marks, and the email
    around = shown_size("\t" + (person("…", found.email, language) or "") + after)
    name = fitted(one_line(found.display_name), max(room - around + shown_size("…"), 1))
    return "\t" + (person(name, found.email, language) or one_line(found.username)) + after


def adding_members(
    space: str | None,
    role: str,
    added: list[Member | str],
    listed: list[Member],
    groups: bool,
    language: Language,
) -> str:
    """What adding people to a space does, as the owner reads it: whom it takes in and as what, by
    their name and their email when the user's spaces have them, else by the username, said to be
    checked; and who the space lists already: left as they are, or, when it links groups, made
    direct members; or that nothing changes. Each person takes an equal share of what the summary
    leaves."""
    words = _WORDS[language]
    named = {"space": space_named(space, language), "roles": words.roles[role]}
    heads = [words.add.format(**named)] if added else []
    if listed and groups:
        heads.append(words.through_groups.format(**named))
    elif listed:
        heads.append(words.left if added else words.unchanged.format(**named))
    room = (BUDGET - shown_size("\n".join(heads))) // max(len(added) + len(listed), 1) - 1
    lines: list[str] = []
    if added:
        lines += [heads[0], *(_person_line(found, "", room, language) for found in added)]
    if listed:
        lines.append(heads[-1])
        for member in listed:
            held = words.role.get(member.role, one_line(member.role))
            lines.append(_person_line(member, f" ({held})" if groups else "", room, language))
    return "\n".join(lines)


def _article(role: str) -> str:
    """The English article of a role: an admin, an editor, a viewer."""
    return "an" if role[:1] in "aeiou" else "a"


def changing_role(
    member: Member, role: str, space: str | None, groups: bool, language: Language
) -> str:
    """What changing the role of a member does, as the owner reads it: their new role and their
    former one, and what an admin does, or that nothing changes; and, when the space links groups,
    that a member through one of them too keeps the stronger role."""
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
    line += (words.admin_powers if role == "admin" else "") + "."
    return line + ("\n" + words.group_role if groups else "")


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
