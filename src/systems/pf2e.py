"""Pathfinder Second Edition GameSystem (see SYSTEMS.md)."""

from typing import Dict, List, Optional

import d20
from pydantic import AliasChoices, Field

from models.actor import earned_xp_and_starting_level
from models.base_model import OronderBaseModel
from systems.base import BaseSystemActor, GameSystem, RollSpec

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

XP_PER_LEVEL = 1000
MAX_LEVEL = 20


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
            descriptor = f"{character} rolls for Initiative!"
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
        lines = [actor.desc_string()]
        hp = actor.attributes.hp.get("max")
        ac = actor.attributes.ac.get("value")
        if hp is not None:
            lines.append(f"HP: {hp}")
        if ac is not None:
            lines.append(f"AC: {ac}")
        saves = ", ".join(
            f"{slug.title()} {actor.attributes.saves[slug].mod:+d}"
            for slug in SAVE_SLUGS
            if slug in actor.attributes.saves
        )
        if saves:
            lines.append(f"Saves: {saves}")
        lines.append(f"Perception: {actor.attributes.perception.mod:+d}")
        trained = [
            f"{actor.skill_label(slug)} {skill.mod:+d}"
            for slug, skill in actor.skills.items()
            if skill.rank > 0
        ]
        if trained:
            lines.append(f"Skills: {', '.join(trained)}")
        weapons = [
            f"{w.name} ({w.attack})" if w.attack else w.name for w in actor.weapons
        ]
        if weapons:
            lines.append(f"Weapons: {', '.join(weapons)}")
        return "\n".join(lines)
