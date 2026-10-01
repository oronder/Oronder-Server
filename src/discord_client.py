import asyncio
import os

from discord import option, Intents, ApplicationContext

from groups import (
    character_description,
    display_ephemeral,
    DISPLAY_PUBLIC,
    display_choices,
    downtime,
    events,
    game,
    lookups,
    tasks,
    admin,
    gm,
    campaign,
)
from groups.autocomplete import (
    actor_autocomplete,
    stat_autocomplete,
    attack_autocomplete,
    spell_level_autocomplete,
    action_autocomplete,
    attack_mode_autocomplete,
)
import dnd
import dnd.rules
from groups.top_level import r, roll_attack, roll, action
from models.socket_aware_bot import SocketAwareBot
from routers.socket_io import sio
from routers.socket_namespace import SocketNamespace
from utils import (
    HOME_GUILD_ID,
    getLogger,
    run_uptime_monitor,
    ensure_members,
)

logger = getLogger(__name__)
token = os.environ["DISCORD_TOKEN"]

intents = Intents.default()
# noinspection PyDunderSlots,PyUnresolvedReferences
intents.members = True
# noinspection PyDunderSlots,PyUnresolvedReferences
intents.guild_reactions = True
# noinspection PyDunderSlots,PyUnresolvedReferences
intents.guild_polls = True
# noinspection PyDunderSlots,PyUnresolvedReferences
intents.guild_messages = True
# intents.message_content = True

# Members are loaded per guild on first use (utils.ensure_members), not all at
# startup -- which held on_ready for ~6 minutes per deploy.
bot = SocketAwareBot(intents=intents, chunk_guilds_at_startup=False)


async def start():
    # py-cord binds its event loop when the Bot is constructed -- here, at
    # import -- and only rebinds in `async with bot:`, which bot.start() skips.
    # Since uvicorn 0.4x the app is imported before uvicorn's loop exists, so
    # the bot kept a loop nothing runs: every py-cord future (wait_for, member
    # chunking, heartbeats) raised "attached to a different loop". Bind it to
    # the loop we are actually running on, as __aenter__ would.
    loop = asyncio.get_running_loop()
    bot.loop = bot.http.loop = bot._connection.loop = loop

    for cog in [gm, events, downtime, game, tasks, lookups, admin, campaign]:
        cog.setup(bot)
        await asyncio.sleep(1)
    logger.critical(f"Cogs Loaded: {', '.join([c.title() for c in bot.cogs])}")

    await bot.start(token)


async def stop():
    await bot.close()
    bot.socket_namespace.stop()


@bot.event
async def on_ready():
    logger.critical("Bot Ready")
    # The home server's member list backs invite_link and the subscription
    # role checks, so load it up front. It is one guild, not hundreds.
    if HOME_GUILD_ID:
        await ensure_members(bot.get_guild(HOME_GUILD_ID))
    sio.register_namespace(SocketNamespace(bot, "/"))

    if os.getenv("GITHUB_UPTIME_PAT") and os.getenv("GITHUB_UPTIME_URL"):
        await run_uptime_monitor()


@bot.slash_command(name="roll", description="Roll from a character sheet.")
@option("character", description=character_description, autocomplete=actor_autocomplete)
@option("stat", description="Ability, Skill or Tool", autocomplete=stat_autocomplete)
@option(
    "advantage",
    description="Dice so nice I rolled them twice.",
    default=None,
    choices=["Advantage", "Disadvantage"],
)
@option(
    "save",
    description="Saving throws only apply to Abilities, not Skills.",
    default=False,
)
async def command_roll(
    ctx: ApplicationContext, character: str, stat: str, advantage: str, save: bool
):
    await roll(ctx, character, stat, advantage, save, bot.socket_namespace)


@bot.slash_command(name="attack", description="Weapon or Spell Attack.")
@option(
    "character",
    parameter_name="actor_name",
    description=character_description,
    autocomplete=actor_autocomplete,
)
@option("weapon", description="Weapon to attack with", autocomplete=attack_autocomplete)
@option(
    "advantage",
    description="Dice so nice I rolled them twice.",
    default=None,
    choices=["Advantage", "Disadvantage"],
)
@option(
    "spell_level",
    description="Spell slot level to use.",
    default=None,
    autocomplete=spell_level_autocomplete,
)
@option(
    "attack_mode",
    description="How to attack with the weapon",
    default=None,
    autocomplete=attack_mode_autocomplete,
)
async def command_attack(
    ctx: ApplicationContext,
    actor_name: str,
    weapon: str,
    advantage: str,
    spell_level: int,
    attack_mode: str,
):
    await roll_attack(
        ctx,
        actor_name,
        weapon,
        bot.socket_namespace,
        advantage,
        spell_level,
        attack_mode,
    )


@bot.slash_command(name="r", description="Roll some dice!")
@option("die")
@option(
    "display",
    description=display_ephemeral,
    default=DISPLAY_PUBLIC,
    choices=display_choices,
)
async def command_r(ctx: ApplicationContext, die: str, display: str):
    await r(ctx, die, display)


@bot.slash_command(
    name="action", description="Declare Intent to a DM for Play by Post games."
)
@option(
    "character",
    parameter_name="actor_name",
    description=character_description,
    autocomplete=actor_autocomplete,
)
@option(
    name="type",
    description="Action Type",
    parameter_name="action_type",
    autocomplete=action_autocomplete,
)
@option(name="comment", description="Additional Info to display", default="")
@option(
    name="description",
    description="Display Description",
    parameter_name="display_description",
    default=False,
)
async def command_action(
    ctx: ApplicationContext,
    actor_name: str,
    action_type: str,
    comment: str,
    display_description: bool,
):
    await action(ctx, actor_name, action_type, comment, display_description)


# /action describes the action it declares, which comes from the 5e data. With
# no data source configured it is withdrawn rather than left to fail on use.
if not dnd.available(dnd.rules):
    bot.remove_application_command(command_action)
    logger.warning(
        "5e data unavailable, not registering /action"
        " -- set DND5E_DATA_SOURCE or populate ./data to enable it."
    )
