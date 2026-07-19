"""Game-system abstraction (see SYSTEMS.md for the cross-repo contract).

A GameSystem encapsulates everything dnd5e-specific that used to be inlined
throughout the server: stat classification for /roll, autocomplete
vocabularies, the pydantic actor model, local dice fallbacks, and the
xp/level model. `world.system` on every actor row/payload is the
discriminator (see systems.registry).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, List, Optional

from models.actor import World
from models.base_model import OronderBaseModel

# Discord's hard cap for an embed description; rendered sheets must fit.
EMBED_DESCRIPTION_LIMIT = 4096


def fit_sheet(
    before: List[str],
    items: List[str],
    after: List[str],
    limit: int = EMBED_DESCRIPTION_LIMIT,
) -> str:
    """Join sheet lines, dropping trailing `items` (with an elision note)
    until the result fits inside a Discord embed description."""
    for keep in range(len(items), -1, -1):
        omitted = len(items) - keep
        chunk = items[:keep] + ([f"… and {omitted} more"] if omitted else [])
        text = "\n".join(before + chunk + after)
        if len(text) <= limit:
            return text
    return "\n".join(before + after)[:limit]


class BaseSystemActor(OronderBaseModel):
    """Minimal envelope shared by every system's actor model (contract 1)."""

    id: str
    name: str
    discord_ids: List[int] = []
    portrait_url: Optional[str] = None
    equipment: List[str] = []
    world: Optional[World] = None

    def first_name(self):
        return self.name.split(" ")[0]


@dataclass
class RollSpec:
    """A classified /roll request.

    payload: system-specific socket payload (contract 2) minus the common
        actor_id/discord_id fields, which the caller adds.
    descriptor: embed field title shown on Discord.
    local_roll: server-side dice fallback used when Foundry is unreachable;
        returns the result string.
    """

    payload: dict
    descriptor: str
    local_roll: Callable[[], str]


class GameSystem(ABC):
    system_id: str
    supports_xp: bool
    actor_model: type[BaseSystemActor]

    def parse_actor(self, payload) -> BaseSystemActor:
        """Validate an actor payload (dict) or DB row into this system's model."""
        return self.actor_model.model_validate(payload)

    @abstractmethod
    def stat_options(self, row) -> List[str]:
        """Autocomplete vocabulary for /roll, derived from an ActorTable row."""

    @abstractmethod
    def build_roll(
        self,
        actor,
        character: str,
        stat: str,
        advantage: Optional[str],
        save: bool,
    ) -> Optional[RollSpec]:
        """Classify a stat name into a RollSpec, or None if unrecognized."""

    @abstractmethod
    def attack_payload(
        self,
        actor,
        attack,
        advantage: Optional[str],
        spell_level: Optional[int],
        attack_mode: Optional[str],
    ) -> dict:
        """System-specific socket payload for an attack (minus common fields)."""

    @abstractmethod
    def attack_fallback(self, actor, attack, advantage: Optional[str]) -> str:
        """Local dice fallback for an attack roll."""

    def actor_level(self, actor) -> Optional[int]:
        """Character level, or None for systems without levels."""
        return None

    def get_lvl(self, xp: int) -> Optional[int]:
        """Level for a given XP total, or None for systems without XP."""
        return None

    def get_exp(self, actor, guild_settings) -> Optional[int]:
        """Total XP for an actor, or None for systems without XP."""
        return None

    def desc_string(self, actor) -> str:
        """Short character description for sheet display."""
        return actor.desc_string()

    def summary_text(self, actor) -> str:
        """Plain-text character summary for /lookup character."""
        return self.desc_string(actor)
