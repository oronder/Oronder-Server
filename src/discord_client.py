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
from groups.top_level import r, roll_attack, roll, action
from models.socket_aware_bot import SocketAwareBot
from routers.socket_io import sio
from routers.socket_namespace import SocketNamespace
from utils import oronder_bot_prod, getLogger, run_uptime_monitor

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

bot = SocketAwareBot(intents=intents)


async def start():
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
    sio.register_namespace(SocketNamespace(bot, "/"))

    if bot.application_id == oronder_bot_prod:
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
