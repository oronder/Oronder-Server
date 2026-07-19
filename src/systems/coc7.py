"""Call of Cthulhu 7e GameSystem (see SYSTEMS.md).

Foundry data paths: system.characteristics.<abrv>.value (str, con, siz, dex,
app, int, pow, edu) and system.attribs.{hp,san,mp,lck,db,mov,build}.
DB mapping: characteristics are stored in the `abilities` JSONB column,
attribs in `attributes` (no schema changes).
"""

import random
from typing import Dict, List, Optional, Tuple

from pydantic import AliasChoices, Field

from models.base_model import OronderBaseModel
from systems.base import BaseSystemActor, GameSystem, RollSpec, fit_sheet

CHARACTERISTICS = ["str", "con", "siz", "dex", "app", "int", "pow", "edu"]

ATTRIBUTE_STATS = {"sanity": "san", "san": "san", "luck": "lck", "lck": "lck"}
ATTRIBUTE_LABELS = {"san": "Sanity", "lck": "Luck"}


class CoC7Characteristic(OronderBaseModel):
    value: int = 0


class CoC7SkillItem(OronderBaseModel):
    id: Optional[str] = None
    name: str
    value: int = 0


class CoC7Weapon(OronderBaseModel):
    id: str
    name: str
    type: str = "weapon"
    skill: Optional[str] = None
    value: int = 0
    damage: Optional[str] = None
    img: Optional[str] = None


class CoC7Actor(BaseSystemActor):
    abilities: Dict[str, CoC7Characteristic] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("characteristics", "abilities"),
    )
    attributes: dict = Field(
        default_factory=dict, validation_alias=AliasChoices("attribs", "attributes")
    )
    skills: List[CoC7SkillItem] = []
    details: dict = {}
    weapons: List[CoC7Weapon] = []

    def attrib_value(self, key: str) -> Optional[int]:
        attrib = (self.attributes or {}).get(key)
        if isinstance(attrib, dict):
            return attrib.get("value")
        return attrib if isinstance(attrib, int) else None

    def desc_string(self) -> str:
        occupation = (self.details or {}).get("occupation") or "Investigator"
        age = (self.details or {}).get("age")
        return f"{occupation}, age {age}" if age else occupation


def success_level(roll: int, value: int) -> str:
    """CoC7 d100 success grading for a roll-under check against `value`."""
    if roll == 1:
        return "Critical success"
    if roll <= value // 5:
        return "Extreme success"
    if roll <= value // 2:
        return "Hard success"
    if roll <= value:
        return "Regular success"
    if (value < 50 and roll >= 96) or roll == 100:
        return "Fumble"
    return "Failure"


def roll_d100(
    advantage: Optional[str] = None, rng: random.Random = random
) -> Tuple[int, List[int], int]:
    """Percentile roll with optional bonus/penalty die.

    Advantage rolls one extra tens die and keeps the lowest total (bonus
    die); Disadvantage keeps the highest (penalty die).
    Returns (total, tens dice, units die).
    """
    units = rng.randint(0, 9)
    n_tens = 2 if advantage in ("Advantage", "Disadvantage") else 1
    tens = [rng.randint(0, 9) for _ in range(n_tens)]
    totals = [(t * 10 + units) or 100 for t in tens]
    if advantage == "Advantage":
        total = min(totals)
    elif advantage == "Disadvantage":
        total = max(totals)
    else:
        total = totals[0]
    return total, tens, units


def check_result(value: int, advantage: Optional[str] = None) -> str:
    total, _, _ = roll_d100(advantage)
    grade = success_level(total, value)
    # expose the roll-under thresholds alongside the grade
    return (
        f"{value} / rolled {total}: {grade}"
        f" (Hard {value // 2}, Extreme {value // 5})"
    )


class CoC7System(GameSystem):
    system_id = "CoC7"
    supports_xp = False
    actor_model = CoC7Actor

    def stat_options(self, row) -> List[str]:
        options = [c.upper() for c in CHARACTERISTICS]
        options += ["Sanity", "Luck"]
        for skill in row.skills or []:
            if isinstance(skill, dict) and skill.get("name"):
                options.append(skill["name"])
        return options

    def _resolve_stat(self, actor: CoC7Actor, stat: str):
        """-> (type, stat key, target value, label) or None."""
        low = stat.strip().lower()
        if low in CHARACTERISTICS:
            characteristic = actor.abilities.get(low)
            return (
                "characteristic",
                low,
                characteristic.value if characteristic else 0,
                stat.strip().upper(),
            )
        if low in ATTRIBUTE_STATS:
            key = ATTRIBUTE_STATS[low]
            return "attribute", key, actor.attrib_value(key) or 0, ATTRIBUTE_LABELS[key]
        skill = next((s for s in actor.skills if s.name.lower() == low), None)
        if skill:
            return "skill", skill.name, skill.value, skill.name
        return None

    def build_roll(
        self,
        actor: CoC7Actor,
        character: str,
        stat: str,
        advantage: Optional[str],
        save: bool,
    ) -> Optional[RollSpec]:
        resolved = self._resolve_stat(actor, stat)
        if not resolved:
            return None
        stat_type, stat_key, value, label = resolved

        descriptor = f"{label} Check"
        if advantage:
            descriptor += f" ({advantage})"

        def local_roll() -> str:
            return check_result(value, advantage)

        return RollSpec(
            payload={"type": stat_type, "stat": stat_key, "advantage": advantage},
            descriptor=descriptor,
            local_roll=local_roll,
        )

    def attack_payload(
        self,
        actor: CoC7Actor,
        attack: CoC7Weapon,
        advantage: Optional[str],
        spell_level: Optional[int],
        attack_mode: Optional[str],
    ) -> dict:
        payload = {"type": "attack", "item_id": attack.id, "stat": attack.skill}
        if advantage:
            payload["advantage"] = advantage
        return payload

    def attack_fallback(
        self, actor: CoC7Actor, attack: CoC7Weapon, advantage: Optional[str]
    ) -> str:
        return check_result(attack.value or 0, advantage)

    def desc_string(self, actor: CoC7Actor) -> str:
        return actor.desc_string()

    def summary_text(self, actor: CoC7Actor) -> str:
        return self.markdown_sheet(actor)

    def markdown_sheet(self, actor: CoC7Actor) -> str:
        """Discord-markdown investigator sheet for /lookup character.

        Characteristics and skills show value (half/fifth) since CoC7's
        Hard/Extreme thresholds are value/2 and value/5. Resilient to
        sparse payloads and kept inside the embed description limit.
        """
        attribs = actor.attributes or {}
        lines = [f"**{actor.desc_string()}**"]

        chars = [
            (c, actor.abilities[c].value)
            for c in CHARACTERISTICS
            if c in actor.abilities
        ]
        if chars:
            rows = [
                "   ".join(
                    f"{c.upper()} {v:>3} ({v // 2:>2}/{v // 5:>2})"
                    for c, v in chars[i : i + 4]
                )
                for i in range(0, len(chars), 4)
            ]
            lines += [
                "",
                "**Characteristics** — value (half/fifth)",
                "```\n" + "\n".join(rows) + "\n```",
            ]

        pools = []
        for key, label in [("hp", "HP"), ("san", "SAN"), ("mp", "MP")]:
            attrib = attribs.get(key)
            value = actor.attrib_value(key)
            if value is None:
                continue
            attrib_max = attrib.get("max") if isinstance(attrib, dict) else None
            pools.append(
                f"{label} **{value}/{attrib_max}**"
                if attrib_max is not None
                else f"{label} **{value}**"
            )
        luck = actor.attrib_value("lck")
        if luck is not None:
            pools.append(f"Luck **{luck}**")
        combat = []
        db = attribs.get("db")
        if db not in (None, ""):
            combat.append(f"Damage Bonus **{db}**")
        if attribs.get("build") is not None:
            combat.append(f"Build **{attribs['build']}**")
        if attribs.get("mov") is not None:
            combat.append(f"Move **{attribs['mov']}**")
        if pools or combat:
            lines += ["", "**Status**"]
            if pools:
                lines.append(" • ".join(pools))
            if combat:
                lines.append(" • ".join(combat))

        skill_lines = [
            f"{s.name} {s.value} ({s.value // 2}/{s.value // 5})"
            for s in sorted(actor.skills, key=lambda s: (-s.value, s.name.lower()))
        ]
        before = lines + (
            ["", "**Skills** — value (half/fifth)"] if skill_lines else []
        )

        after = []
        if actor.weapons:
            after += ["", "**Weapons**"]
            for w in actor.weapons:
                detail = []
                if w.skill:
                    detail.append(f"{w.skill} {w.value}")
                elif w.value:
                    detail.append(str(w.value))
                if w.damage:
                    detail.append(f"damage {w.damage}")
                after.append(f"{w.name} — {', '.join(detail)}" if detail else w.name)

        return fit_sheet(before, skill_lines, after)
