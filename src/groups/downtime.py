import math

import d20
import discord
from typing import Tuple
from discord import Embed, Cog, InteractionContextType
from discord import SlashCommandGroup
from discord.commands import option
from discord.ext.commands import Bot
from sqlalchemy import select

import system
from database import DowntimeTable, Session
from database.guild_settings_table import GuildSettingsTable
from system import SKILLS, TOOLS, cleanse_damage_roll, ABILITIES, items
from system.items import get_item_price_string, get_item, format_number, get_item_rarity
from groups import character_description, get_actor, invite_link
from groups.autocomplete import actor_autocomplete, search
from models import DowntimeModel
from models.actor import Actor
from models.guild_settings import Subscription, GuildSettings
from utils import mention_safe, getLogger
from views.downtime import DowntimeView, DowntimeBuyView, DowntimeRoll, DowntimeGmView

logger = getLogger(__name__)


class Downtime(discord.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    downtime_group = SlashCommandGroup(
        "downtime", "Downtime Activities", contexts={InteractionContextType.guild}
    )

    @Cog.listener()
    async def on_ready(self):
        with Session() as session:
            unresolved_downtimes = [
                DowntimeModel.model_validate(i)
                for i in session.scalars(
                    select(DowntimeTable).where(DowntimeTable.gm_id.is_(None))
                ).all()
            ]

        for downtime_table in unresolved_downtimes:
            self.bot.add_view(DowntimeGmView(downtime_table))

    @staticmethod
    def common(
        character: str, ctx: discord.ApplicationContext
    ) -> Tuple[GuildSettings, Actor, dict]:
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if guild_settings.subscription == Subscription.none:
            return (
                None,
                None,
                logger.err_msg(
                    f"{ctx.command.name.capitalize()} is a supporter only feature.\n{invite_link(ctx)}",
                    ctx.guild_id,
                ),
            )

        downtime_channel = ctx.guild.get_channel(guild_settings.downtime_channel_id)
        if ctx.channel_id != downtime_channel.id:
            return (
                None,
                None,
                logger.err_msg(
                    f"{ctx.command.name.capitalize()} must be run in {mention_safe(downtime_channel)}.",
                    ctx.guild_id,
                ),
            )

        return guild_settings, *get_actor(character, ctx.user.id, ctx.guild_id)

    if system.ENABLED:

        @downtime_group.command(
            name="buy", description="Convenience command for Buying a Magic Item."
        )
        @option(
            "character",
            description=character_description,
            autocomplete=actor_autocomplete,
        )
        @option(
            "item",
            description="Item to Buy",
            autocomplete=lambda ctx: search(ctx.value, items.shoppable_items, sorted),
        )
        @option(
            "extra_weeks",
            default=0,
            description="+1 for every extra week spent",
            min_value=0,
            max_value=10,
        )
        @option(
            "extra_gold",
            default=0,
            description="+1 for every 100 gold spent spent",
            choices=list(range(0, 1100, 100)),
        )
        async def command_downtime_buy(
            self,
            ctx: discord.ApplicationContext,
            character: str,
            extra_weeks: int,
            extra_gold: int,
            item: str,
        ):
            guild_settings, actor, error = self.common(character, ctx)
            if error:
                await ctx.respond(**error)
                return
            if not item:
                await ctx.respond(f"`{item}` not found!", ephemeral=True)
                return

            situational_bonus = extra_weeks + int(extra_gold / 100)
            if situational_bonus > 10:
                ctx.respond(
                    **logger.err_msg(
                        "Bonuses from extra gold and time spent cannot exceed 10.",
                        ctx.guild_id,
                    )
                )

            roll = actor.roll("per", situational_bonus=situational_bonus)
            dc_lookup = {
                "common": 10,
                "uncommon": 15,
                "rare": 20,
                "very rare": 25,
                "legendary": 30,
            }

            dc = dc_lookup.get(get_item_rarity(item), 0)
            success = roll.total >= dc
            if success:
                roll_string = f"**{str(roll)}** >= DC {dc}!"
                item_actual, _ = get_item(item)
                consumable = (item_actual and item_actual["consumable"]) or any(
                    i in item.lower() for i in ["potion", "scroll"]
                )
                price_string = get_item_price_string(item, consumable)
            else:
                roll_string = f"{str(roll)} < DC **{dc}**"
                price_string = f"Failed DC for {item}!"

            embed = Embed(title=f"{actor.name} goes Shopping!")
            embed.set_thumbnail(url=actor.portrait_url)
            embed.add_field(name="Persuasion", value=roll_string)
            embed.add_field(
                name="Downtime Duration",
                value=f"{1 + extra_weeks} week{'s' if extra_weeks else ''}",
            )
            embed.add_field(name="Search Cost", value=f"`{str(100 + extra_gold)} gp`")

            embed.add_field(
                name=f"**{item}**" if success else "", value=price_string, inline=False
            )

            await ctx.respond(
                view=DowntimeBuyView(roll.total, bool(item) and success, ctx.user.id),
                embed=embed,
            )

    @downtime_group.command(
        name="pitfight", description="Convenience command for Pit Fighting."
    )
    @option(
        "character", description=character_description, autocomplete=actor_autocomplete
    )
    async def command_downtime_fight(
        self, ctx: discord.ApplicationContext, character: str
    ):
        guild_settings, actor, error = self.common(character, ctx)
        if error:
            await ctx.respond(**error)
            return

        max_hit_die = max(int(v["hitDice"][1:]) for v in actor.classes.values())

        stats_to_rolls = {
            ABILITIES["con"]: DowntimeRoll(
                roll=lambda: d20.roll(
                    f"1d20 + 1d{max_hit_die} + {actor.abilities.con.mod}"
                ),
                dc_fun=lambda: d20.roll("5+2d10"),
            ),
            SKILLS["ath"]: DowntimeRoll(
                roll=lambda: actor.roll("ath"), dc_fun=lambda: d20.roll("5+2d10")
            ),
            SKILLS["acr"]: DowntimeRoll(
                roll=lambda: actor.roll("acr"), dc_fun=lambda: d20.roll("5+2d10")
            ),
        }
        best_weapon = actor.best_weapon()
        if best_weapon:
            best_weapon_attack = cleanse_damage_roll(best_weapon.attack)
            stats_to_rolls["Attack"] = DowntimeRoll(
                roll=lambda: d20.roll(best_weapon_attack),
                dc_fun=lambda: d20.roll("5+2d10"),
            )

        embed = Embed(title=f"{actor.name} goes Pit Fighting!")
        embed.set_thumbnail(url=actor.portrait_url)

        def wins_to_outcome(win_count: int) -> str:
            if win_count:
                return f"Win {int(math.pow(2, win_count) * 25)} gp."
            else:
                return "You Get Nothing! You Lose! Good Day, Sir!"

        await ctx.respond(
            view=DowntimeView(
                stats_to_rolls=stats_to_rolls,
                wins_to_outcome=wins_to_outcome,
                initiator_id=ctx.user.id,
            ),
            embed=embed,
        )

    @downtime_group.command(name="crime", description="Convenience command for Crime.")
    @option(
        "character", description=character_description, autocomplete=actor_autocomplete
    )
    @option("dc", description="What's the mark?", choices=[10, 15, 20, 25])
    async def command_downtime_crime(
        self, ctx: discord.ApplicationContext, character: str, dc: int
    ):
        guild_settings, actor, error = self.common(character, ctx)
        if error:
            await ctx.respond(**error)
            return

        stats_to_rolls = {
            SKILLS["ste"]: DowntimeRoll(roll=lambda: actor.roll("ste"), dc_int=dc),
            TOOLS["thief"]: DowntimeRoll(
                roll=lambda: actor.roll(
                    "dex",
                    situational_bonus=actor.tools.thief.total
                    if actor.tools.thief
                    else 0,
                ),
                dc_int=dc,
            ),
            SKILLS["inv"]: DowntimeRoll(
                roll=lambda: actor.roll("inv"), dc_int=dc, group=1
            ),
            SKILLS["per"]: DowntimeRoll(
                roll=lambda: actor.roll("per"), dc_int=dc, group=1
            ),
            SKILLS["dec"]: DowntimeRoll(
                roll=lambda: actor.roll("dec"), dc_int=dc, group=1
            ),
        }

        embed = Embed(title=f"{actor.name} commits a Crime!")
        embed.set_thumbnail(url=actor.portrait_url)

        def wins_to_outcome(win_count: int) -> str:
            reward = {10: 50, 150: 100, 20: 200, 25: 1000}[dc]

            match win_count:
                case 0:
                    return f"{int(reward / 25)} weeks in jail and a {reward} gp fine. Oops."
                case 1:
                    return "Hey, at least you didn't get caught?"
                case 2:
                    return f"Made away with {int(reward / 2)} gp"
                case _:
                    return f"Made away with {reward} gp!"

        await ctx.respond(
            view=DowntimeView(stats_to_rolls, wins_to_outcome, ctx.user.id), embed=embed
        )

    @downtime_group.command(
        name="gamble", description="Convenience command for Gambling."
    )
    @option(
        "character", description=character_description, autocomplete=actor_autocomplete
    )
    @option("ante", description="in gp", min_value=10)
    async def command_downtime_gamble(
        self, ctx: discord.ApplicationContext, character: str, ante: int
    ):
        guild_settings, actor, error = self.common(character, ctx)
        if error:
            await ctx.respond(**error)
            return

        stats_to_rolls = {
            SKILLS[s]: DowntimeRoll(
                roll=lambda: actor.roll(s), dc_fun=lambda: d20.roll("5+2d10")
            )
            for s in ["ins", "dec", "itm"]
        }

        gaming_sets = [
            (TOOLS["chess"], actor.tools.chess),
            (TOOLS["dice"], actor.tools.dice),
            (TOOLS["card"], actor.tools.card),
        ]
        for gaming_set_name, gaming_set in gaming_sets:
            if gaming_set:
                stats_to_rolls[gaming_set_name] = DowntimeRoll(
                    roll=gaming_set.roll, dc_fun=lambda: d20.roll("5+2d10"), group=1
                )

        embed = Embed(title=f"{actor.name} goes Gambling!")
        embed.add_field(name="", value=f"{format_number(ante)} down.")
        embed.set_thumbnail(url=actor.portrait_url)

        def wins_to_outcome(win_count: int) -> str:
            match win_count:
                case 0:
                    return f"-{format_number(int(ante * 2))}\nLose all the money you bet, and accrue a debt equal to that amount."
                case 1:
                    return (
                        f"-{format_number(int(ante / 2))}\nLose half the money you bet."
                    )
                case 2:
                    return f"+{format_number(int(ante / 2))}\nGain the amount you bet plus half again more."
                case _:
                    return f"+{format_number(int(ante * 2))}\nGain double the amount you bet."

        await ctx.respond(
            view=DowntimeView(stats_to_rolls, wins_to_outcome, ctx.user.id),
            embed=embed
        )


def setup(bot: Bot):
    logger.critical("Loading")
    bot.add_cog(Downtime(bot))
