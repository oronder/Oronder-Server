"""Finding a player's character, whatever game system it belongs to.

dnd5e characters live in `actors` and the rest in `system_actors` (see
models.game_systems). Commands that support more than dnd5e look characters up
here; everything else keeps using groups.get_actor, which only knows dnd5e.
"""

from pydantic import ValidationError
from sqlalchemy import any_, select
from sqlalchemy.exc import MultipleResultsFound, NoResultFound, SQLAlchemyError

from database import Session, ambiguous_character, newest_first, newest_row
from database.actor_table import ActorTable
from database.system_actor_table import SystemActorTable
from models.actor import Actor
from models.game_systems import ACTOR_MODELS
from models.shared_actor import SharedActor
from utils import getLogger, truncate

logger = getLogger(__name__)

Character = Actor | SharedActor

# Discord rejects an autocomplete choice longer than this, and one bad choice
# loses the whole list, so long names are offered truncated (utils.truncate,
# ellipsis and all) and matched back by prefix.
CHOICE_LIMIT = 100
_ELLIPSIS = "..."


def _name_is(table, name: str):
    if len(name) == CHOICE_LIMIT and name.endswith(_ELLIPSIS):
        return table.name.startswith(name[: -len(_ELLIPSIS)], autoescape=True)
    return table.name == name


def find_character(
    name: str, discord_id: int, guild_id: int, gm: bool = False
) -> tuple[Character | None, dict | None]:
    """The named character, dnd5e or otherwise, with the same rules as
    groups.get_actor: an exact name, in this guild, owned by the caller unless
    a GM is asking."""
    for table in (ActorTable, SystemActorTable):
        stmt = select(table).where(_name_is(table, name), table.guild_id == guild_id)
        if not gm:
            stmt = stmt.where(discord_id == any_(table.discord_ids))
        try:
            with Session() as session:
                row = newest_row(session, stmt, table)
                return _validate(row), None
        except NoResultFound:
            continue
        except MultipleResultsFound:
            return None, logger.err_msg(ambiguous_character(name), guild_id)
        except (SQLAlchemyError, ValidationError) as e:
            return None, logger.err_msg(str(e), guild_id)
    return None, logger.err_msg(f"Character {name} not found!", guild_id)


def character_names(
    discord_id: int, guild_id: int, contains: str, gm: bool = False, limit: int = 25
) -> list[str]:
    """Names for an autocomplete, across every game system."""
    names: list[str] = []
    for table in (ActorTable, SystemActorTable):
        stmt = (
            select(table.name)
            .where(table.guild_id == guild_id, table.name.icontains(contains or ""))
            .limit(limit)
        )
        if not gm:
            stmt = stmt.where(discord_id == any_(table.discord_ids))
        with Session() as session:
            names.extend(session.scalars(stmt))
    return [truncate(n, CHOICE_LIMIT) for n in sorted(set(names))[:limit]]


def _validate(row) -> Character:
    if isinstance(row, ActorTable):
        return Actor.model_validate(row)
    data = {
        "game_system": row.game_system,
        "id": row.id,
        "name": row.name,
        "portrait_url": row.portrait_url,
        "discord_ids": row.discord_ids,
        "world": row.world,
        "hp": row.hp,
        "skills": row.skills,
        "currency": row.currency,
        "body": row.body,
    }
    return ACTOR_MODELS[row.game_system].model_validate(data)


def character_for_autocomplete(
    name: str, discord_id: int, guild_id: int, gm: bool = False
) -> Character | None:
    """The character an autocomplete's `character` option refers to.

    Usually that option holds an exact name picked from the character
    autocomplete, but it can still be a fragment the player is typing, so fall
    back to the first name containing it -- as the dnd5e stat autocomplete
    always has."""
    if not name:
        return None
    character, _ = find_character(name, discord_id, guild_id, gm)
    if character:
        return character
    for table in (ActorTable, SystemActorTable):
        stmt = (
            select(table)
            .where(table.guild_id == guild_id, table.name.icontains(name))
            .order_by(newest_first(table))
            .limit(1)
        )
        if not gm:
            stmt = stmt.where(discord_id == any_(table.discord_ids))
        with Session() as session:
            row = session.scalars(stmt).first()
            if row is not None:
                return _validate(row)
    return None
