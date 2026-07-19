"""dnd5e GameSystem: delegates to the existing dnd/ package and models.actor.

Behavior here must stay byte-for-byte identical to the pre-abstraction
server: the classification chain, payload vocabulary, and local dice
fallbacks are lifted verbatim from groups/top_level.py.
"""

from typing import List, Optional

import d20
from pydantic import TypeAdapter

from dnd import (
    ABILITIES,
    OTHER_ROLLABLES,
    OTHER_ROLLABLES_NAME_TO_ABRV,
    SKILLS,
    STAT_NAME_TO_ABRV,
    TOOLS,
    clense_damage_roll,
)
from dnd.items import attack_modes
from dnd.rules import get_lvl
from models.actor import Actor, Spell, Tools
from systems.base import GameSystem, RollSpec


class Dnd5eSystem(GameSystem):
    system_id = "dnd5e"
    supports_xp = True
    actor_model = Actor

    def stat_options(self, row) -> List[str]:
        tools = TypeAdapter(Tools).validate_python(row.tools or {})
        return [
            *ABILITIES.values(),
            *SKILLS.values(),
            *OTHER_ROLLABLES.values(),
            *tools.known_tool_strings(),
        ]

    def build_roll(
        self,
        actor: Actor,
        character: str,
        stat: str,
        advantage: Optional[str],
        save: bool,
    ) -> Optional[RollSpec]:
        stat_type, stat_descriptor = (
            ("save", f"{stat} Saving Throw")
            if save
            else ("ability", f"{stat} Ability Check")
            if stat in ABILITIES.values()
            else ("tool", f"{stat} Tool Check")
            if stat in TOOLS.values()
            else ("skill", f"{stat} Skill Check")
            if stat in SKILLS.values()
            else ("init", f"{character} rolls for Initiative!")
            if stat == "Initiative"
            else (OTHER_ROLLABLES_NAME_TO_ABRV.get(stat, None), stat)
        )

        if not stat_type:
            return None

        if advantage:
            stat_descriptor += f" ({advantage})"

        def local_roll() -> str:
            _, res = actor.roll_str(
                stat, advantage=advantage and advantage.lower()[:3], is_save=save
            )
            return str(res)

        return RollSpec(
            payload={
                "type": stat_type,
                "stat": STAT_NAME_TO_ABRV[stat],
                "advantage": advantage,
            },
            descriptor=stat_descriptor,
            local_roll=local_roll,
        )

    def attack_payload(
        self,
        actor: Actor,
        attack,
        advantage: Optional[str],
        spell_level: Optional[int],
        attack_mode: Optional[str],
    ) -> dict:
        payload = {"type": "attack", "item_id": attack.id}
        if isinstance(attack, Spell) and spell_level is not None:
            payload["spell_level"] = spell_level
        if advantage:
            payload["advantage"] = advantage
        if attack_mode and attack_mode in attack_modes:
            payload["attack_mode"] = attack_modes[attack_mode]
        return payload

    def attack_fallback(self, actor: Actor, attack, advantage: Optional[str]) -> str:
        match advantage:
            case "Disadvantage":
                attack.attack.replace("1d20", "2d20kl1", 1)
            case "Advantage":
                attack.attack.replace(
                    "1d20", "3d20kh1" if actor.elven_accuracy() else "2d20kh1", 1
                )
        return d20.roll(clense_damage_roll(attack.attack)).result

    def actor_level(self, actor: Actor) -> Optional[int]:
        return actor.details.level

    def get_lvl(self, xp: int) -> Optional[int]:
        return get_lvl(xp)

    def get_exp(self, actor: Actor, guild_settings) -> Optional[int]:
        return actor.get_exp(guild_settings)

    def desc_string(self, actor: Actor) -> str:
        return actor.desc_string()
