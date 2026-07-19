"""System registry: `world.system` is the discriminator on every actor
row/payload; legacy rows without a world default to dnd5e."""

from typing import Dict

from systems.base import BaseSystemActor, GameSystem
from systems.coc7 import CoC7System
from systems.dnd5e import Dnd5eSystem
from systems.pf2e import Pf2eSystem

DEFAULT_SYSTEM_ID = "dnd5e"

_SYSTEMS: Dict[str, GameSystem] = {
    system.system_id: system
    for system in (Dnd5eSystem(), Pf2eSystem(), CoC7System())
}


def get_system(system_id: str | None = None) -> GameSystem:
    return _SYSTEMS.get(system_id or DEFAULT_SYSTEM_ID, _SYSTEMS[DEFAULT_SYSTEM_ID])


def system_id_of(world) -> str:
    """Extract the foundry system id from a world dict/model/None."""
    if world is None:
        return DEFAULT_SYSTEM_ID
    if isinstance(world, dict):
        return world.get("system") or DEFAULT_SYSTEM_ID
    return getattr(world, "system", None) or DEFAULT_SYSTEM_ID


def get_system_for_world(world) -> GameSystem:
    return get_system(system_id_of(world))


def get_system_for_actor(actor) -> GameSystem:
    """System for a validated actor model or an ActorTable row."""
    return get_system_for_world(getattr(actor, "world", None))


def validate_actor_row(row) -> BaseSystemActor:
    """Validate an ActorTable row with the model matching its system."""
    return get_system_for_world(row.world).actor_model.model_validate(row)
