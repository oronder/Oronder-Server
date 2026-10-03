"""How the bot rolls and displays a Call of Cthulhu 7e investigator.

Rolls are made here, in Discord, never handed to Foundry: the module's
incoming roll handler speaks dnd5e's roll API, which a CoC7 actor lacks.

Every CoC7 roll is a d100 against a percentile target, graded by how far under
it lands. The shared Advantage/Disadvantage option maps onto one bonus or
penalty die.
"""

import random
import re
from dataclasses import dataclass

import d20
from discord import Color, Embed, EmbedFooter

from models.coc7_actor import CoC7Actor, CoC7Weapon
from utils import truncate

CHARACTERISTICS = {
    "str": "Strength",
    "con": "Constitution",
    "siz": "Size",
    "dex": "Dexterity",
    "app": "Appearance",
    "int": "Intelligence",
    "pow": "Power",
    "edu": "Education",
}

FUMBLE, FAILURE, REGULAR, HARD, EXTREME, CRITICAL = range(6)
LEVEL_NAMES = {
    FUMBLE: "Fumble",
    FAILURE: "Failure",
    REGULAR: "Regular Success",
    HARD: "Hard Success",
    EXTREME: "Extreme Success",
    CRITICAL: "Critical Success",
}

# Conditions that mean the investigator is down, for the sheet's red colour.
_DOWN = {"dying", "dead", "unconscious"}


def dice_label(advantage: str | None) -> str | None:
    match (advantage or "").lower()[:3]:
        case "adv":
            return "Bonus Die"
        case "dis":
            return "Penalty Die"
    return None


# --- the d100 ------------------------------------------------------------


@dataclass
class D100:
    result: int
    target: int
    level: int
    # The tens dice rolled, when a bonus or penalty die added more than one.
    tens: list[int]
    units: int

    def __str__(self) -> str:
        text = f"`{self.result}` vs {self.target}: **{LEVEL_NAMES[self.level]}**"
        if len(self.tens) > 1:
            text += (
                f"\n*tens {' · '.join(f'{t:02d}' for t in self.tens)},"
                f" units {self.units}*"
            )
        return text


def success_level(result: int, target: int) -> int:
    if result == 1:
        return CRITICAL
    # 100 always fumbles; with a target under 50, so does anything from 96.
    if result == 100 or (target < 50 and result >= 96):
        return FUMBLE
    if result <= target // 5:
        return EXTREME
    if result <= target // 2:
        return HARD
    if result <= target:
        return REGULAR
    return FAILURE


def roll_d100(target: int, advantage: str | None = None, rng=random) -> D100:
    """A percentile roll. A bonus die rolls a second tens die and keeps the
    better (lower) result, a penalty die keeps the worse."""
    units = rng.randint(0, 9)
    extra = dice_label(advantage) is not None
    tens = [rng.randint(0, 9) * 10 for _ in range(2 if extra else 1)]
    # 00 on the tens and 0 on the units reads as 100, not 0.
    results = [t + units or 100 for t in tens]
    match dice_label(advantage):
        case "Bonus Die":
            result = min(results)
        case "Penalty Die":
            result = max(results)
        case _:
            result = results[0]
    return D100(result, target, success_level(result, target), tens, units)


# --- /roll ---------------------------------------------------------------


def rollables(actor: CoC7Actor) -> dict[str, int]:
    """Every roll the investigator can make, keyed by the label offered to the
    player, with its percentile target."""
    b = actor.body
    stats = {
        name: getattr(b.characteristics, key) for key, name in CHARACTERISTICS.items()
    }
    if b.luck is not None:
        stats["Luck"] = b.luck
    stats["Sanity"] = b.san.value
    stats.update({skill.label: skill.mod for skill in actor.skills.values()})
    return stats


def roll(
    actor: CoC7Actor, stat: str, advantage: str | None, rng=random
) -> tuple[str, str] | None:
    """(heading, result) for a roll, or None if the investigator has no such stat."""
    target = rollables(actor).get(stat)
    if target is None:
        return None
    heading = f"{stat} Roll"
    label = dice_label(advantage)
    if label:
        heading += f" ({label})"
    return heading, str(roll_d100(target, advantage, rng))


# --- /attack -------------------------------------------------------------


def attack_names(actor: CoC7Actor) -> list[str]:
    return [w.name for w in actor.body.weapons]


def steps_for(actor: CoC7Actor, name: str) -> list[str]:
    """CoC7 has no attack modes for the attack_mode option to offer."""
    return []


def find_weapon(actor: CoC7Actor, name: str) -> CoC7Weapon | None:
    return next((w for w in actor.body.weapons if w.name == name), None)


# A Damage Bonus written into the damage itself, as older CoC7 data does:
# "1D3+DB", "1D6+½DB", "1D4+DB/2".
_DB_IN_DAMAGE = re.compile(
    r"\s*\+\s*(?P<half>½|1/2)?\s*DB(?P<half2>\s*/\s*2)?", re.IGNORECASE
)


def _dice(expr: str) -> str:
    """CoC7 writes dice as 1D10; d20 wants 1d10."""
    return expr.replace(" ", "").replace("D", "d")


def _maximum(expr: str) -> int:
    """The highest an expression can roll: every die at its top face."""
    return d20.roll(
        re.sub(r"(\d*)d(\d+)", lambda m: f"{int(m[1] or 1) * int(m[2])}", expr)
    ).total


def damage_parts(weapon: CoC7Weapon, db: str | None) -> tuple[str, str | None]:
    """The weapon's own damage and the Damage Bonus it adds, as d20
    expressions: ("1d6+1", "1d4"), ("1d10", None), ("1d6", "(1d4)/2")."""
    damage = weapon.damage or ""
    mode = weapon.db
    m = _DB_IN_DAMAGE.search(damage)
    if m:
        mode = "half" if (m["half"] or m["half2"]) else "full"
        damage = damage[: m.start()] + damage[m.end() :]
    damage = _dice(damage)
    bonus = _dice(db or "").lstrip("+")
    if mode == "none" or bonus in ("", "0", "-0"):
        return damage, None
    return damage, (f"({bonus})/2" if mode == "half" else bonus)


def roll_damage(weapon: CoC7Weapon, db: str | None, extreme: bool) -> str:
    """Normal damage on a regular or hard success. On an extreme one the
    weapon and Damage Bonus deal their maximum, and an impaling weapon adds a
    normal damage roll on top."""
    damage, bonus = damage_parts(weapon, db)
    if not damage:
        return weapon.damage or "—"
    expr = damage if bonus is None else f"{damage}+{bonus}"
    try:
        if not extreme:
            return str(d20.roll(expr))
        maximum = _maximum(expr)
        if weapon.impale:
            extra = d20.roll(damage)
            return f"Impale: {maximum} (maximum) + {extra} = `{maximum + extra.total}`"
        return f"Maximum: `{maximum}`"
    except (ValueError, d20.RollError):
        return weapon.damage or "—"


def attack(
    actor: CoC7Actor,
    name: str,
    step: str | None,
    advantage: str | None,
    rng=random,
) -> Embed | None:
    """The embed for a weapon attack, or None if there is no such weapon.

    `step` is the attack_mode option, which CoC7 does not use."""
    weapon = find_weapon(actor, name)
    if weapon is None:
        return None
    title = weapon.name
    label = dice_label(advantage)
    if label:
        title += f" ({label})"
    embed = Embed(
        title=title,
        description=weapon.skill,
        footer=EmbedFooter(actor.name, actor.portrait_url),
    )
    if weapon.target is None:
        # Nothing to roll the attack against; the damage still stands.
        embed.add_field(name="Damage", value=roll_damage(weapon, actor.body.db, False))
        return embed

    rolled = roll_d100(weapon.target, advantage, rng)
    embed.add_field(name="Attack", value=str(rolled), inline=False)
    # A failed attack misses whatever the defender does.
    if rolled.level >= REGULAR:
        extreme = rolled.level >= EXTREME
        embed.add_field(
            name="Extreme Damage" if extreme else "Damage",
            value=roll_damage(weapon, actor.body.db, extreme),
            inline=False,
        )
    return embed


# --- /lookup character ---------------------------------------------------


def _chunks(entries: list[str], limit: int = 1024) -> list[str]:
    """Join entries with " · " into as few field values as fit Discord's limit."""
    out, cur = [], ""
    for entry in entries:
        candidate = f"{cur} · {entry}" if cur else entry
        if len(candidate) > limit and cur:
            out.append(cur)
            cur = entry
        else:
            cur = candidate
    if cur:
        out.append(truncate(cur, limit))
    return out


def character_embed(actor: CoC7Actor) -> Embed:
    b = actor.body
    description = ", ".join(
        x for x in (b.occupation, b.archetype, b.age and f"age {b.age}") if x
    )
    down = actor.hp.value <= 0 or any(c.lower() in _DOWN for c in b.conditions)
    embed = Embed(
        title=actor.name,
        description=description or None,
        color=Color.red() if down else None,
    )
    if actor.portrait_url and actor.portrait_url.startswith("http"):
        embed.set_thumbnail(url=actor.portrait_url)

    embed.add_field(name="HP", value=f"{actor.hp.value}/{actor.hp.max}")
    embed.add_field(name="Sanity", value=f"{b.san.value}/{b.san.max}")
    embed.add_field(name="Magic Points", value=f"{b.mp.value}/{b.mp.max}")
    if b.luck is not None:
        embed.add_field(name="Luck", value=str(b.luck))
    for name, value in (("MOV", b.mov), ("Build", b.build), ("Damage Bonus", b.db)):
        if value is not None:
            embed.add_field(name=name, value=str(value))
    if b.conditions:
        embed.add_field(name="Conditions", value=", ".join(b.conditions), inline=False)

    embed.add_field(
        name="Characteristics",
        value=" · ".join(
            f"{key.upper()} {getattr(b.characteristics, key)}"
            for key in CHARACTERISTICS
        ),
        inline=False,
    )

    if b.weapons:
        embed.add_field(
            name="Weapons",
            value=truncate(
                "\n".join(
                    f"**{w.name}**"
                    + (f" {w.skill} {w.target}%" if w.target is not None else "")
                    + (f" · {w.damage}" if w.damage else "")
                    for w in b.weapons
                ),
                1024,
            ),
            inline=False,
        )

    skills = sorted(actor.skills.values(), key=lambda s: s.label)
    for i, value in enumerate(_chunks([f"{s.label} {s.mod}%" for s in skills])):
        embed.add_field(name="Skills" if i == 0 else "", value=value, inline=False)

    credit = [
        f"{label}: {value}"
        for label, value in (
            ("Spending Level", b.credit.spending_level),
            ("Cash", b.credit.cash),
            ("Assets", b.credit.assets),
        )
        if value
    ]
    if credit:
        embed.add_field(name="Credit", value=" · ".join(credit), inline=False)
    return embed
