"""The pf2e body for PUT /actor, validated against a live pf2e 8.5.1 character.

Field sources on the module side, for reference when either end changes:
level from actor.level, ac from actor.armorClass, perception and saves from
pf2e's Statistic objects, attributes from system.abilities[*].mod (pf2e has
modifiers, not scores), strikes from system.actions, spellcasting from the
actor's spellcasting entries.
"""

from typing import Literal

from pydantic import ConfigDict, Field

from models.base_model import OronderBaseModel
from models.shared_actor import SharedActor


class Pf2eStat(OronderBaseModel):
    mod: int
    # 0 untrained, 1 trained, 2 expert, 3 master, 4 legendary
    rank: int


class Pf2eXp(OronderBaseModel):
    value: int = 0
    max: int = 1000


class Pf2eAttributes(OronderBaseModel):
    str: int
    dex: int
    con: int
    int: int
    wis: int
    cha: int


class Pf2eSaves(OronderBaseModel):
    fortitude: Pf2eStat
    reflex: Pf2eStat
    will: Pf2eStat


class Pf2eStrike(OronderBaseModel):
    name: str
    # pf2e lists a strike for every weapon carried, wielded or not, so this is
    # what stops the bot offering an attack with a bow still in the pack.
    # Absent means unknown, which is treated as not ready.
    ready: bool = False
    # The multiple attack penalty ladder: always three, full bonus first. Agile
    # changes the penalties (-4/-8 instead of -5/-10), not the count.
    attack: list[int] = Field(min_length=3, max_length=3)
    damage: str | None = None
    traits: list[str] = Field(default_factory=list)


class Pf2eSpellcasting(OronderBaseModel):
    name: str
    tradition: str | None = None
    attack: int | None = None
    dc: int | None = None
    spells: list[str] = Field(default_factory=list)


class Pf2eBody(OronderBaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    level: int
    xp: Pf2eXp = Field(default_factory=Pf2eXp)
    # Display-only for the bot, so a missing one is no reason to refuse a sync.
    ancestry: str | None = None
    heritage: str | None = None
    background: str | None = None
    # `class` on the wire; a keyword in Python.
    class_: str | None = Field(default=None, alias="class")
    ac: int
    perception: Pf2eStat
    attributes: Pf2eAttributes
    saves: Pf2eSaves
    strikes: list[Pf2eStrike] = Field(default_factory=list)
    # Optional in v1. Slots, focus points and heightening can be added later
    # without breaking this shape.
    spellcasting: list[Pf2eSpellcasting] = Field(default_factory=list)


class Pf2eActor(SharedActor):
    game_system: Literal["pf2e"]
    body: Pf2eBody
