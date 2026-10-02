"""How the bot rolls and displays a pf2e character.

Rolls are made here, in Discord, never handed to Foundry: the module's
incoming roll handler speaks dnd5e's roll API, which a pf2e actor lacks.
"""

import re

import d20
from discord import Color, Embed, EmbedField, EmbedFooter

from models.pf2e_actor import Pf2eActor, Pf2eStrike
from utils import truncate

RANKS = ["Untrained", "Trained", "Expert", "Master", "Legendary"]

# The three saves under the names a player would type, and pf2e's own
# abbreviations for the character sheet.
SAVES = {"fortitude": "Fortitude Save", "reflex": "Reflex Save", "will": "Will Save"}
SAVE_ABBREVIATIONS = {"fortitude": "Fort", "reflex": "Ref", "will": "Will"}

ATTRIBUTES = ["str", "dex", "con", "int", "wis", "cha"]

ORDINALS = ["1st", "2nd", "3rd"]


def signed(n: int) -> str:
    return f"{n:+d}"


def rank_letter(rank: int | None) -> str:
    return RANKS[rank][0] if rank is not None and 0 <= rank < len(RANKS) else "?"


def d20_expression(advantage: str | None) -> str:
    """pf2e has no advantage, but its fortune and misfortune effects are the
    same mechanic -- roll twice, keep the higher or the lower -- so the shared
    Advantage/Disadvantage option maps onto them."""
    match (advantage or "").lower()[:3]:
        case "adv":
            return "2d20kh1"
        case "dis":
            return "2d20kl1"
    return "1d20"


def fortune_label(advantage: str | None) -> str | None:
    match (advantage or "").lower()[:3]:
        case "adv":
            return "Fortune"
        case "dis":
            return "Misfortune"
    return None


# --- /roll ---------------------------------------------------------------


def rollables(actor: Pf2eActor) -> dict[str, int]:
    """Every check the character can roll, keyed by the label offered to the
    player, with its total modifier."""
    stats = {skill.label: skill.mod for skill in actor.skills.values()}
    stats["Perception"] = actor.body.perception.mod
    # Initiative is a Perception check unless something says otherwise.
    stats["Initiative"] = actor.body.perception.mod
    for key, label in SAVES.items():
        stats[label] = getattr(actor.body.saves, key).mod
    return stats


def roll(actor: Pf2eActor, stat: str, advantage: str | None) -> tuple[str, str] | None:
    """(heading, result) for a check, or None if the character has no such stat."""
    mod = rollables(actor).get(stat)
    if mod is None:
        return None
    result = d20.roll(f"{d20_expression(advantage)}{signed(mod)}")
    heading = (
        f"{actor.name} rolls for Initiative!"
        if stat == "Initiative"
        else stat
        if stat.endswith("Save")
        else f"{stat} Check"
    )
    label = fortune_label(advantage)
    if label:
        heading += f" ({label})"
    return heading, str(result)


# --- /attack -------------------------------------------------------------


def attack_names(actor: Pf2eActor) -> list[str]:
    """Strikes the character can make right now, then spell attacks.

    pf2e lists a strike for every weapon carried, so only `ready` ones are
    offered; otherwise the bot would happily attack with a bow in the pack."""
    names = [s.name for s in actor.body.strikes if s.ready]
    names += [
        f"Spell Attack: {e.name}"
        for e in actor.body.spellcasting
        if e.attack is not None
    ]
    return names


def attack_steps(strike: Pf2eStrike) -> list[str]:
    """The multiple attack penalty choices, e.g. '2nd attack (+3)'."""
    return [
        f"{ORDINALS[i]} attack ({signed(bonus)})"
        for i, bonus in enumerate(strike.attack)
    ]


def steps_for(actor: Pf2eActor, name: str) -> list[str]:
    """Attack-penalty choices for any attack option, strike or spell."""
    if name.startswith("Spell Attack: "):
        entry = next(
            (
                e
                for e in actor.body.spellcasting
                if e.name == name.removeprefix("Spell Attack: ")
            ),
            None,
        )
        if entry is None or entry.attack is None:
            return []
        # Spells are never agile, so the standard -5/-10.
        return [
            f"{ORDINALS[i]} attack ({signed(entry.attack - 5 * i)})" for i in range(3)
        ]
    strike = find_strike(actor, name)
    return attack_steps(strike) if strike else []


def step_index(step: str | None) -> int:
    for i, ordinal in enumerate(ORDINALS):
        if step and step.startswith(ordinal):
            return i
    return 0


def find_strike(actor: Pf2eActor, name: str) -> Pf2eStrike | None:
    return next((s for s in actor.body.strikes if s.name == name), None)


def attack(
    actor: Pf2eActor, name: str, step: str | None, advantage: str | None
) -> Embed | None:
    """The embed for a strike or spell attack, or None if there is no such attack."""
    footer = EmbedFooter(actor.name, actor.portrait_url)

    if name.startswith("Spell Attack: "):
        entry_name = name.removeprefix("Spell Attack: ")
        entry = next((e for e in actor.body.spellcasting if e.name == entry_name), None)
        if entry is None or entry.attack is None:
            return None
        i = step_index(step)
        bonus = entry.attack - 5 * i
        result = d20.roll(f"{d20_expression(advantage)}{signed(bonus)}")
        return Embed(
            title=f"Spell Attack ({entry.name})",
            description=fortune_label(advantage),
            fields=[EmbedField(f"{ORDINALS[i]} attack", str(result))],
            footer=footer,
        )

    strike = find_strike(actor, name)
    if strike is None:
        return None
    i = step_index(step)
    result = d20.roll(f"{d20_expression(advantage)}{signed(strike.attack[i])}")
    embed = Embed(
        title=strike.name,
        description=", ".join(strike.traits) or None,
        fields=[EmbedField(f"{ORDINALS[i]} attack", str(result))],
        footer=footer,
    )
    label = fortune_label(advantage)
    if label:
        embed.title += f" ({label})"
    if strike.damage:
        embed.add_field(name="Damage", value=roll_damage(strike.damage), inline=False)
    return embed


# A damage component: a dice expression and the damage type it deals, e.g.
# "(1d10 + 1) slashing" or "1d6 persistent acid". pf2e joins components with
# "+" -- "(1d10 + 1) slashing + 6 spirit", "2d8 slashing + 1d6 fire" -- so the
# string is read as a run of these rather than split on separators. A type is
# two or more letters, which is what stops the "d" in "1d6" reading as one.
_COMPONENT = re.compile(
    r"\s*\+?\s*(?P<expr>[\d\s+\-*()d]+?)\s*"
    r"(?P<kind>(?:persistent\s+)?[a-z]{2,})\b",
    re.IGNORECASE,
)


def roll_damage(damage: str) -> str:
    """Roll a pf2e damage string, one line per damage type.

    Anything that does not parse cleanly is shown as the original text rather
    than raising: a strike with unusual damage should still be rollable.
    Persistent damage keeps its label, since pf2e applies it at the end of the
    turn rather than on the hit."""
    text = damage.strip()
    parts, pos, immediate = [], 0, 0
    for m in _COMPONENT.finditer(text):
        if text[pos : m.start()].strip():
            return text  # something between components we did not understand
        expr = m["expr"].replace(" ", "").lstrip("+")
        kind = m["kind"].lower()
        try:
            rolled = d20.roll(expr)
        except (ValueError, d20.RollError):
            return text
        parts.append(f"{rolled} {' '.join(kind.split())}")
        if not kind.startswith("persistent"):
            immediate += rolled.total
        pos = m.end()
    if not parts or text[pos:].strip():
        return text
    # A hit always deals at least 1 damage, so "1d4 - 1" rolling 0 is still 1.
    if immediate < 1:
        parts.append("*Minimum 1 damage on a hit.*")
    return "\n".join(parts)


# --- /lookup character ---------------------------------------------------


def character_embed(actor: Pf2eActor) -> Embed:
    b = actor.body
    identity = " ".join(x for x in (b.heritage or b.ancestry, b.class_) if x)
    description = f"Level {b.level} {identity}".strip()
    if b.background:
        description += f" ({b.background})"
    # Red at 0 HP, as dnd5e's sheet is when dead. pf2e conditions (dying,
    # unconscious) aren't in the v1 payload, so HP is the only signal.
    embed = Embed(
        title=actor.name,
        description=description,
        color=Color.red() if actor.hp.value <= 0 else None,
    )
    if actor.portrait_url and actor.portrait_url.startswith("http"):
        embed.set_thumbnail(url=actor.portrait_url)

    hp = f"{actor.hp.value}/{actor.hp.max}"
    if actor.hp.temp:
        hp += f" (+{actor.hp.temp} temp)"
    embed.add_field(name="HP", value=hp)
    embed.add_field(name="AC", value=str(b.ac))
    embed.add_field(
        name="Perception",
        value=f"{signed(b.perception.mod)} ({rank_letter(b.perception.rank)})",
    )
    embed.add_field(
        name="Saves",
        value=" · ".join(
            f"{SAVE_ABBREVIATIONS[k]} {signed(getattr(b.saves, k).mod)} "
            f"({rank_letter(getattr(b.saves, k).rank)})"
            for k in SAVES
        ),
        inline=False,
    )
    embed.add_field(
        name="Attributes",
        value=" · ".join(
            f"{a.upper()} {signed(getattr(b.attributes, a))}" for a in ATTRIBUTES
        ),
        inline=False,
    )

    ready = [s for s in b.strikes if s.ready]
    if ready:
        embed.add_field(
            name="Strikes",
            value=truncate(
                "\n".join(
                    f"**{s.name}** {'/'.join(signed(x) for x in s.attack)}"
                    + (f" · {s.damage}" if s.damage else "")
                    for s in ready
                ),
                1024,
            ),
            inline=False,
        )

    skills = sorted(
        (s for s in actor.skills.values() if not s.lore and (s.rank or 0) > 0),
        key=lambda s: s.label,
    )
    if skills:
        embed.add_field(
            name="Skills",
            value=truncate(
                " · ".join(
                    f"{s.label} {signed(s.mod)} ({rank_letter(s.rank)})" for s in skills
                ),
                1024,
            ),
            inline=False,
        )
    lores = sorted((s for s in actor.skills.values() if s.lore), key=lambda s: s.label)
    if lores:
        embed.add_field(
            name="Lore",
            value=truncate(
                " · ".join(f"{s.label} {signed(s.mod)}" for s in lores), 1024
            ),
            inline=False,
        )

    for entry in b.spellcasting:
        bits = [
            x
            for x in (
                entry.tradition,
                entry.dc and f"DC {entry.dc}",
                entry.attack is not None and f"attack {signed(entry.attack)}",
            )
            if x
        ]
        embed.add_field(
            name=truncate(
                f"{entry.name} ({', '.join(bits)})" if bits else entry.name, 256
            ),
            value=truncate(", ".join(entry.spells) or "—", 1024),
            inline=False,
        )

    coins = [
        f"{getattr(actor.currency, c)} {c}"
        for c in ("pp", "gp", "sp", "cp")
        if getattr(actor.currency, c)
    ]
    if coins:
        embed.add_field(name="Money", value=", ".join(coins))
    embed.add_field(name="XP", value=f"{b.xp.value}/{b.xp.max}")
    return embed
