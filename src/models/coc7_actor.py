"""The Call of Cthulhu 7e body for PUT /actor, shaped after CoC7 8.15.

Everything is a final number, as the system computes it once an actor's data
is prepared, so the bot never re-implements CoC7's rules for derived values:
skill targets from each skill item's system.value, MOV, Build and Damage
Bonus from system.attribs.{mov,build,db}.value.

The shared section carries HP and the skills; a skill's `mod` is its
percentile target, `rank` is unused. CoC7 money is a spending level, cash and
assets rather than coins, so it lives in `credit` here and the shared
currency stays empty.
"""

from typing import Literal

from pydantic import Field

from models.base_model import OronderBaseModel
from models.shared_actor import SharedActor


class CoC7Characteristics(OronderBaseModel):
    str: int
    con: int
    siz: int
    dex: int
    app: int
    int: int
    pow: int
    edu: int


class CoC7Pool(OronderBaseModel):
    value: int = 0
    max: int = 0


class CoC7Weapon(OronderBaseModel):
    name: str
    # The skill the weapon is rolled with, e.g. "Firearms (Handgun)", and its
    # percentile target. None when the weapon is linked to no skill, which
    # leaves nothing to roll.
    skill: str | None = None
    target: int | None = None
    # The normal-range damage, e.g. "1D10" or "1D6+1".
    damage: str | None = None
    # How the wielder's Damage Bonus applies: melee weapons add it, thrown
    # weapons add half, firearms none.
    db: Literal["full", "half", "none"] = "none"
    # Impaling weapons roll extra damage on an Extreme success.
    impale: bool = False
    ranged: bool = False


class CoC7Credit(OronderBaseModel):
    # Free text in CoC7, e.g. "$10", so kept as strings.
    spending_level: str | None = None
    cash: str | None = None
    assets: str | None = None


class CoC7Body(OronderBaseModel):
    # Display-only for the bot, so a missing one is no reason to refuse a sync.
    occupation: str | None = None
    archetype: str | None = None
    # Free text in CoC7.
    age: str | None = None
    characteristics: CoC7Characteristics
    mp: CoC7Pool = Field(default_factory=CoC7Pool)
    luck: int | None = None
    san: CoC7Pool = Field(default_factory=CoC7Pool)
    mov: int | None = None
    build: int | None = None
    # A dice expression or a flat number: "0", "+1D4", "-1", "+2D6".
    db: str | None = None
    weapons: list[CoC7Weapon] = Field(default_factory=list)
    credit: CoC7Credit = Field(default_factory=CoC7Credit)
    # The conditions currently on the investigator, as CoC7 names them, e.g.
    # "Major Wound", "Dying", "Temporary Insanity".
    conditions: list[str] = Field(default_factory=list)


class CoC7Actor(SharedActor):
    game_system: Literal["CoC7"]
    body: CoC7Body
