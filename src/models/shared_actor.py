"""The section every non-dnd5e game system shares on PUT /actor.

dnd5e predates this: its payload is dnd5e's own roll data, validated by
models.actor.Actor, and stays that way so module versions already in use keep
working. Every system added since sends an Oronder-defined shape instead:
these shared fields at the top level, and its own model under `body`.

The shared section is deliberately small -- identity, portrait, owners, HP,
skills and money are the only concepts dnd5e, pf2e and CoC7 all have. Anything
richer belongs in the per-system body.
"""

from pydantic import Field

from models.base_model import OronderBaseModel


class SharedHp(OronderBaseModel):
    value: int = 0
    max: int = 0
    temp: int = 0


class SharedSkill(OronderBaseModel):
    # A label rather than relying on the key: systems such as pf2e have
    # player-defined skills (lores), so there is no fixed table to look up.
    label: str
    # The total the bot would roll. Its meaning is per system -- a d20
    # modifier for pf2e, a percentile target for CoC7.
    mod: int
    # Proficiency rank where the system has one (pf2e: 0 untrained .. 4
    # legendary), else None.
    rank: int | None = None
    # Player-defined knowledge skills (pf2e lores), so the bot can group them
    # apart from the system's fixed skill list.
    lore: bool = False


class SharedCurrency(OronderBaseModel):
    pp: int = 0
    gp: int = 0
    sp: int = 0
    cp: int = 0


class World(OronderBaseModel):
    id: str | None = None
    coreVersion: str | None = None
    system: str | None = None
    systemVersion: str | None = None


class SharedActor(OronderBaseModel):
    """Fields common to every system; subclassed once per system.

    Each subclass narrows `game_system` to its own literal and replaces
    `body` with that system's model.
    """

    game_system: str
    id: str
    name: str
    portrait_url: str | None = None
    discord_ids: list[int] = Field(default_factory=list)
    world: World | None = None
    hp: SharedHp = Field(default_factory=SharedHp)
    skills: dict[str, SharedSkill] = Field(default_factory=dict)
    currency: SharedCurrency = Field(default_factory=SharedCurrency)
    body: dict = Field(default_factory=dict)
