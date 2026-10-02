"""Which game systems PUT /actor accepts, and how each one is validated.

A synced actor may name its system in a top-level `game_system` field. Module
versions that predate the field send none and are treated as dnd5e, so every
client already in the wild keeps working unchanged.

The field is deliberately not called `system`: Foundry uses that name for an
actor's data, and the module spreads actor roll data into the payload, so a
`system` key could already be present and mean something else entirely.

Supporting another system means adding its model to ACTOR_MODELS; the
endpoint does not change. With one entry this is a plain lookup -- typing
collapses a one-member Union, so pydantic cannot discriminate it yet -- and it
can become an Annotated discriminated union once there are two.
"""

from models.actor import Actor
from models.base_model import OronderBaseModel

DEFAULT_GAME_SYSTEM = "dnd5e"

ACTOR_MODELS: dict[str, type[OronderBaseModel]] = {
    "dnd5e": Actor,
}


class UnsupportedGameSystem(ValueError):
    """The payload names a game system this server cannot validate."""


def actor_model_for(payload: dict) -> type[OronderBaseModel]:
    """The model a synced actor payload should be validated against."""
    game_system = payload.get("game_system", DEFAULT_GAME_SYSTEM)
    if not isinstance(game_system, str):
        raise UnsupportedGameSystem(
            f"game_system must be a string, got {type(game_system).__name__}"
        )
    try:
        return ACTOR_MODELS[game_system]
    except KeyError:
        raise UnsupportedGameSystem(
            f"unsupported game_system {game_system!r}; this server accepts "
            + ", ".join(sorted(ACTOR_MODELS))
        ) from None
