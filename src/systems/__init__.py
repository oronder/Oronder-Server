"""Per-game-system abstraction layer. See SYSTEMS.md for the cross-repo
contract shared with the Foundry module."""

from systems.base import BaseSystemActor, GameSystem, RollSpec
from systems.registry import (
    DEFAULT_SYSTEM_ID,
    get_system,
    get_system_for_actor,
    get_system_for_world,
    system_id_of,
    validate_actor_row,
)

__all__ = [
    "BaseSystemActor",
    "GameSystem",
    "RollSpec",
    "DEFAULT_SYSTEM_ID",
    "get_system",
    "get_system_for_actor",
    "get_system_for_world",
    "system_id_of",
    "validate_actor_row",
]
