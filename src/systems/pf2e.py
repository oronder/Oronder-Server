"""Pathfinder Second Edition GameSystem (see SYSTEMS.md)."""

from typing import Dict, List, Optional

import d20
from pydantic import AliasChoices, Field

from models.actor import earned_xp_and_starting_level
from models.base_model import OronderBaseModel
from systems.base import BaseSystemActor, GameSystem, RollSpec, fit_sheet

SKILL_SLUGS = [
    "acrobatics",
    "arcana",
    "athletics",
    "crafting",
    "deception",
    "diplomacy",
    "intimidation",
    "medicine",
    "nature",
    "occultism",
    "performance",
    "religion",
    "society",
    "stealth",
    "survival",
    "thievery",
]

SAVE_SLUGS = ["fortitude", "reflex", "will"]

ABILITY_ORDER = ["str", "dex", "con", "int", "wis", "cha"]

# pf2e proficiency ranks 0-4
RANK_LABELS = ["Untrained", "Trained", "Expert", "Master", "Legendary"]

XP_PER_LEVEL = 1000
MAX_LEVEL = 20


def rank_label(rank) -> str:
    try:
        return RANK_LABELS[max(0, min(int(rank), 4))]
    except (TypeError, ValueError):
        return RANK_LABELS[0]


class Pf2eAbility(OronderBaseModel):
    mod: int = 0


class Pf2eSkill(OronderBaseModel):
    mod: int = 0
    rank: int = 0
    label: Optional[str] = None


class Pf2eSave(OronderBaseModel):
    mod: int = 0


class Pf2eAttributes(OronderBaseModel):
    hp: dict = {}
    ac: dict = {}
    speed: Optional[float] = None
    class_dc: Optional[int] = None
    saves: Dict[str, Pf2eSave] = {}
    perception: Pf2eSave = Pf2eSave()


class Pf2eDetails(OronderBaseModel):
    level: int = 1
    class_name: str = Field("", validation_alias=AliasChoices("class", "class_name"))
    ancestry: str = ""
    heritage: str = ""
    background: str = ""
    xp: dict = {}


class Pf2eWeapon(OronderBaseModel):
    id: str
    name: str
    type: str = "weapon"
    attack: Optional[str] = None
    img: Optional[str] = None


class Pf2eActor(BaseSystemActor):
    abilities: Dict[str, Pf2eAbility] = {}
    skills: Dict[str, Pf2eSkill] = {}
    attributes: Pf2eAttributes = Pf2eAttributes()
    details: Pf2eDetails = Pf2eDetails()
    currency: dict = {}
    weapons: List[Pf2eWeapon] = []

    def skill_label(self, slug: str) -> str:
        skill = self.skills.get(slug)
        if skill and skill.label:
            return skill.label
        if slug.startswith("lore-"):
            return f"{slug[len('lore-'):].replace('-', ' ').title()} Lore"
        return slug.replace("-", " ").title()

    def desc_string(self) -> str:
        parts = [
            p
            for p in [self.details.ancestry, self.details.class_name]
            if p
        ]
        return f"{' '.join(parts) or 'Adventurer'} {self.details.level}"

    def xp_display(self) -> Optional[str]:
        """'400/1000 (Level 5)', or None when the module sent no XP."""
        xp = self.details.xp or {}
        value = xp.get("value")
        if value is None:
            return None
        xp_max = xp.get("max")
        amount = f"{value}/{xp_max}" if xp_max else str(value)
        return f"{amount} (Level {self.details.level})"


def _skill_slugs(skills: dict) -> List[str]:
    """All skill slugs for an actor: the fixed list plus dynamic lore-*."""
    lore = [s for s in (skills or {}) if s.startswith("lore-")]
    return [*SKILL_SLUGS, *lore]


class Pf2eSystem(GameSystem):
    system_id = "pf2e"
    supports_xp = True
    actor_model = Pf2eActor

    def stat_options(self, row) -> List[str]:
        skills = row.skills or {}
        options = [slug.title() for slug in SKILL_SLUGS]
        for slug in skills:
            if slug.startswith("lore-"):
                label = (skills[slug] or {}).get("label") if isinstance(
                    skills[slug], dict
                ) else None
                options.append(
                    label or f"{slug[len('lore-'):].replace('-', ' ').title()} Lore"
                )
        options += [s.title() for s in SAVE_SLUGS]
        options += ["Perception", "Initiative"]
        return options

    def _resolve_stat(self, actor: Pf2eActor, stat: str):
        """-> (type, slug) or None. pf2e has no advantage; it is ignored."""
        low = stat.strip().lower()
        if low in SAVE_SLUGS:
            return "save", low
        if low == "perception":
            return "perception", "perception"
        if low == "initiative":
            return "init", "perception"
        if low in SKILL_SLUGS:
            return "skill", low
        # dynamic lore skills, matched by slug or label
        for slug in _skill_slugs(actor.skills):
            if low == slug or low == actor.skill_label(slug).lower():
                return "skill", slug
        return None

    def build_roll(
        self,
        actor: Pf2eActor,
        character: str,
        stat: str,
        advantage: Optional[str],
        save: bool,
    ) -> Optional[RollSpec]:
        resolved = self._resolve_stat(actor, stat)
        if not resolved:
            return None
        stat_type, slug = resolved

        if stat_type == "save":
            descriptor = f"{slug.title()} Saving Throw"
        elif stat_type == "perception":
            descriptor = "Perception Check"
        elif stat_type == "init":
            descriptor = f"{character} rolls Perception for Initiative!"
        else:
            descriptor = f"{actor.skill_label(slug)} Skill Check"

        def local_roll() -> str:
            if stat_type == "save":
                mod = actor.attributes.saves.get(slug, Pf2eSave()).mod
            elif stat_type in ("perception", "init"):
                mod = actor.attributes.perception.mod
            else:
                mod = actor.skills.get(slug, Pf2eSkill()).mod
            return str(d20.roll(f"1d20{mod:+d}"))

        return RollSpec(
            payload={"type": stat_type, "stat": slug, "advantage": None},
            descriptor=descriptor,
            local_roll=local_roll,
        )

    def attack_payload(
        self,
        actor: Pf2eActor,
        attack: Pf2eWeapon,
        advantage: Optional[str],
        spell_level: Optional[int],
        attack_mode: Optional[str],
    ) -> dict:
        return {"type": "attack", "item_id": attack.id}

    def attack_fallback(
        self, actor: Pf2eActor, attack: Pf2eWeapon, advantage: Optional[str]
    ) -> str:
        return d20.roll(attack.attack or "1d20").result

    def actor_level(self, actor: Pf2eActor) -> Optional[int]:
        return actor.details.level

    def get_lvl(self, xp: int) -> Optional[int]:
        return min(MAX_LEVEL, xp // XP_PER_LEVEL + 1)

    def get_exp(self, actor: Pf2eActor, guild_settings) -> Optional[int]:
        earned, starting_lvl = earned_xp_and_starting_level(actor.id, guild_settings)
        return (starting_lvl - 1) * XP_PER_LEVEL + earned

    def desc_string(self, actor: Pf2eActor) -> str:
        return actor.desc_string()

    def summary_text(self, actor: Pf2eActor) -> str:
        return self.markdown_sheet(actor)

    def markdown_sheet(self, actor: Pf2eActor) -> str:
        """Discord-markdown character sheet for /lookup character.

        Mirrors the 5e sheet's structure (header, defenses, abilities,
        skills, attacks, wealth) scaled to the pf2e payload. Resilient to
        sparse payloads and kept inside the embed description limit.
        """
        details = actor.details
        attrs = actor.attributes

        ancestry = details.ancestry or ""
        if ancestry and details.heritage:
            ancestry = f"{details.ancestry} ({details.heritage})"
        elif details.heritage:
            ancestry = details.heritage
        who = " ".join(
            p for p in [ancestry, details.class_name or "Adventurer"] if p
        )
        header = f"**{who} {details.level}**"
        if details.background:
            header += f" — {details.background}"
        lines = [header]

        xp = actor.xp_display()
        if xp:
            lines.append(f"**XP:** {xp}")

        defense_bits = []
        ac = (attrs.ac or {}).get("value")
        if ac is not None:
            defense_bits.append(f"AC **{ac}**")
        hp = (attrs.hp or {}).get("max")
        if hp is not None:
            defense_bits.append(f"HP **{hp}**")
        if attrs.class_dc is not None:
            defense_bits.append(f"Class DC **{attrs.class_dc}**")
        if attrs.speed is not None:
            speed = (
                int(attrs.speed)
                if float(attrs.speed).is_integer()
                else attrs.speed
            )
            defense_bits.append(f"Speed **{speed} ft**")
        save_bits = [
            f"{label} **{attrs.saves[slug].mod:+d}**"
            for slug, label in [
                ("fortitude", "Fort"),
                ("reflex", "Ref"),
                ("will", "Will"),
            ]
            if slug in attrs.saves
        ]
        save_bits.append(f"Perception **{attrs.perception.mod:+d}**")
        lines += ["", "**Defenses**"]
        if defense_bits:
            lines.append(" • ".join(defense_bits))
        lines.append(" • ".join(save_bits))

        mods = [
            (abrv, actor.abilities[abrv].mod)
            for abrv in ABILITY_ORDER
            if abrv in actor.abilities
        ]
        if mods:
            head = " ".join(f"{abrv.upper():>4}" for abrv, _ in mods)
            row = " ".join(f"{mod:+d}".rjust(4) for _, mod in mods)
            lines += ["", "**Ability Modifiers**", f"```\n{head}\n{row}\n```"]

        slugs = [s for s in SKILL_SLUGS if s in actor.skills]
        slugs += sorted(s for s in actor.skills if s.startswith("lore-"))
        ranked = [(s, actor.skills[s]) for s in slugs if actor.skills[s].rank > 0]
        untrained = [(s, actor.skills[s]) for s in slugs if actor.skills[s].rank <= 0]
        skill_lines = [
            f"{actor.skill_label(slug)} {skill.mod:+d} ({rank_label(skill.rank)})"
            for slug, skill in ranked
        ]
        if untrained:
            skill_lines.append(
                "Untrained: "
                + ", ".join(
                    f"{actor.skill_label(slug)} {skill.mod:+d}"
                    for slug, skill in untrained
                )
            )
        before = lines + (["", "**Skills**"] if skill_lines else [])

        after = []
        if actor.weapons:
            after += ["", "**Weapons**"]
            after += [
                f"{w.name} — `{w.attack}`" if w.attack else w.name
                for w in actor.weapons
            ]
        coins = [
            f"{actor.currency.get(coin)} {coin}"
            for coin in ["pp", "gp", "sp", "cp"]
            if actor.currency.get(coin)
        ]
        if coins:
            after += ["", "**Wealth:** " + ", ".join(coins)]

        return fit_sheet(before, skill_lines, after)
