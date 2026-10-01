from typing import Optional, List, Tuple

from discord import ApplicationContext
from sqlalchemy import select, any_
from sqlalchemy.exc import NoResultFound, MultipleResultsFound

from database import Session
from database.actor_table import ActorTable
from database.guild_settings_table import GuildSettingsTable
from database.missions import MissionTable
from models.actor import Actor
from models.missions import Mission
from utils import HOME_GUILD_ID, SUPER_ADMIN_USER_IDS, getLogger

logger = getLogger(__name__)

DISABLE = "❌ DISABLE"
DISABLE_STR = "Disabled"
DEFAULT = "⭕ DEFAULT"
DEFAULT_STR = "Default"

no_init_err_msg = "Could not find existing configuration. Have you run `/admin init`?"

foundry_module_link = "https://foundryvtt.com/packages/oronder"
character_description = "A Foundry VTT character."
display_ephemeral = "Display result publicly or privately."
detail_description = "Only display a single character detail."
DISPLAY_PUBLIC = "public"
DISPLAY_PRIVATE = "private"
display_choices = [DISPLAY_PUBLIC, DISPLAY_PRIVATE]


def invite_link(ctx: ApplicationContext):
    home = ctx.bot.get_guild(HOME_GUILD_ID) if HOME_GUILD_ID else None
    if home and home.get_member(ctx.user.id):
        return f"https://discord.com/channels/{HOME_GUILD_ID}/role-subscriptions"
    else:
        return "https://discord.gg/Adg48Xrs6K"


def get_actors(discord_id: int, guild_id: int) -> Tuple[List[Actor], Optional[dict]]:
    stmt = (
        select(ActorTable)
        .where(discord_id == any_(ActorTable.discord_ids))
        .where(ActorTable.guild_id == guild_id)
    )

    try:
        with Session() as session:
            return [
                Actor.model_validate(actor) for actor in session.scalars(stmt).all()
            ], None

    except NoResultFound:
        return None, logger.err_msg(
            f"No Characters found for discord user {discord_id}!", guild_id
        )
    except Exception as e:
        return None, logger.err_msg(str(e), guild_id)


def get_actor(
    character: str, discord_id: int, guild_id: int, gm: bool = False
) -> Tuple[Actor, dict]:
    stmt = (
        select(ActorTable)
        .where(ActorTable.name == character)
        .where(ActorTable.guild_id == guild_id)
    )

    if not gm:
        stmt = stmt.where(discord_id == any_(ActorTable.discord_ids))

    try:
        with Session() as session:
            return Actor.model_validate(session.scalars(stmt).one()), None

    except NoResultFound:
        return None, logger.err_msg(f"Character {character} not found!", guild_id)
    except MultipleResultsFound:
        return None, logger.err_msg(
            "Duplicate character names. Results ambiguous!", guild_id
        )

    except Exception as e:
        return None, logger.err_msg(str(e), guild_id)


def get_mission_for_edit(title: str, ctx: ApplicationContext) -> Tuple[Mission, dict]:
    try:
        stmt = select(MissionTable).filter_by(guild_id=ctx.guild_id, title=title)
        if ctx.user.id not in SUPER_ADMIN_USER_IDS:
            stmt = stmt.filter_by(gm_id=ctx.user.id)
        with Session() as session:
            mission = Mission.model_validate(session.scalar(stmt))
        return mission, None
    except NoResultFound:
        return None, logger.err_msg(f"{title} not found!", ctx.guild_id)
    except Exception as e:
        return None, logger.err_msg(str(e), ctx.guild_id)


async def is_gm(ctx: ApplicationContext):
    guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
    if not guild_settings:
        await ctx.respond(no_init_err_msg, ephemeral=True)
        return False

    gm_role = ctx.guild.get_role(guild_settings.gm_role_id)
    if gm_role not in ctx.user.roles:
        await ctx.respond('GM only command.', ephemeral=True)
        return False

    return True


def drop_subcommands(bot, group_name: str, names) -> None:
    """Unregister subcommands of an already-added cog group.

    py-cord copies a Cog's commands when the class is defined, so filtering
    the class attribute has no effect on what gets registered -- the copy the
    bot holds has to be edited instead, after add_cog.
    """
    names = set(names)
    for command in bot.pending_application_commands:
        if command.name == group_name and hasattr(command, "subcommands"):
            command.subcommands = [
                c for c in command.subcommands if c.name not in names
            ]
