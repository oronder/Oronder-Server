from datetime import timedelta
from enum import Enum

from discord import SlashCommandGroup, ApplicationContext, Cog, InteractionContextType
from discord.commands import option
from discord.ext.commands import Bot
from sqlalchemy import select, func, any_
from sqlalchemy.exc import NoResultFound

from database import Session
from database.actor_table import ActorTable
from database.missions import MissionTable, edit_mission
from groups import (
    character_description,
    display_ephemeral,
    display_choices,
    DISPLAY_PRIVATE,
)
from groups.autocomplete import (
    mission_join_autocomplete,
    join_actor_autocomplete,
    standby_actor_autocomplete,
    mission_remove_autocomplete,
    mission_info_autocomplete,
)
from models.missions import Mission
from utils import getLogger

logger = getLogger(__name__)


class Game(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot

    game_group = SlashCommandGroup(
        "game", "Session scheduling.", contexts={InteractionContextType.guild}
    )

    class placement(Enum):
        Join = 0
        Standby = 1
        Remove = 2

    show_past_games = "Shows past games. This is not usually what you want."

    @game_group.command(name="remove", description="Remove your character from a game.")
    @option(
        "game",
        description="Game to cancel sign up",
        autocomplete=mission_remove_autocomplete,
    )
    @option("past", bool, description=show_past_games, default=False)
    async def remove(self, ctx: ApplicationContext, game: str, past: bool):
        try:
            with Session() as session:
                mission = Mission.model_validate(
                    session.query(MissionTable)
                    .filter_by(guild_id=ctx.guild_id, title=game)
                    .one()
                )
                actors = (
                    session.query(ActorTable)
                    .filter_by(guild_id=ctx.guild_id)
                    .filter(ctx.interaction.user.id == any_(ActorTable.discord_ids))
                    .filter(ActorTable.id.in_([*mission.pcs, *mission.pcs_standby]))
                    .filter(ctx.interaction.user.id == any_(ActorTable.discord_ids))
                    .all()
                )
        except NoResultFound:
            await ctx.respond(f"**{game}** not found!", ephemeral=True)
            return

        if not actors:
            await ctx.respond(
                f"You have no characters signed up for **{game}**.", ephemeral=True
            )
            return
        msgs = []
        for actor in actors:
            if actor.id in mission.pcs_standby:
                mission.pcs_standby.remove(actor.id)
                msgs.append(
                    f"**{actor.name}** canceled tentative for **{mission.title}**."
                )
            else:
                mission.pcs.remove(actor.id)
                msgs.append(
                    f"**{actor.name}** canceled sign up for **{mission.title}**."
                )

        await ctx.respond("\n".join(msgs), ephemeral=True)
        await edit_mission(ctx.guild, mission)

    @game_group.command(name="join", description="Sign up to a game.")
    @option(
        "game",
        description="Game to sign up for",
        autocomplete=mission_join_autocomplete,
    )
    @option(
        "character",
        description=character_description,
        autocomplete=join_actor_autocomplete,
    )
    @option("past", bool, description=show_past_games, default=False)
    async def join(
        self, ctx: ApplicationContext, game: str, character: str, past: bool
    ):
        await self.signup(ctx, character, game, self.placement.Join.name)

    @game_group.command(name="tentative", description="Tentatively Sign up to a game.")
    @option(
        "game",
        description="Game to sign up for",
        autocomplete=mission_join_autocomplete,
    )
    @option(
        "character",
        description=character_description,
        autocomplete=standby_actor_autocomplete,
    )
    @option("past", bool, description=show_past_games, default=False)
    async def standby(
        self, ctx: ApplicationContext, game: str, character: str, past: bool
    ):
        await self.signup(ctx, character, game, self.placement.Standby.name)

    @staticmethod
    async def signup(
        ctx: ApplicationContext, character: str, game: str, placement: str
    ):
        with Session() as session:
            actor_id = (
                session.query(ActorTable.id)
                .filter_by(guild_id=ctx.guild_id, name=character)
                .filter(ctx.interaction.user.id == any_(ActorTable.discord_ids))
                .scalar()
            )
        if not actor_id:
            await ctx.respond(**logger.err_msg("Character not found", ctx.guild_id))
            return

        try:
            with Session() as session:
                mission = Mission.model_validate(
                    session.query(MissionTable)
                    .filter_by(guild_id=ctx.guild_id, title=game)
                    .one()
                )
        except NoResultFound:
            await ctx.respond(**logger.err_msg(f"{game} not found!", ctx.guild_id))
            return

        existing_others = [
            a
            for a in [*mission.get_actors(), *mission.get_actors(standby=True)]
            if a.id != actor_id and ctx.user.id in a.discord_ids
        ]
        if len(existing_others) > 1:
            logger.warning("how did this happen???")
        for existing_other in existing_others:
            if existing_other.id in mission.pcs:
                mission.pcs.remove(existing_other.id)
            else:
                mission.pcs_standby.remove(existing_other.id)

        msg = f"**{character}** unchanged for **{mission.title}**."
        previously_joined = (
            actor_id in [*mission.pcs, *mission.pcs_standby] or existing_others
        )

        match placement:
            case "Join":
                if len(mission.pcs) >= mission.max_pc_count:
                    msg = f"**{mission.title}** is full."
                else:
                    if actor_id in mission.pcs_standby:
                        mission.pcs_standby.remove(actor_id)
                    if actor_id not in mission.pcs:
                        mission.pcs.append(actor_id)
                        msg = f"**{character}** signed up for **{mission.title}**."
            case "Standby":
                if actor_id in mission.pcs:
                    mission.pcs.remove(actor_id)
                if actor_id not in mission.pcs_standby:
                    mission.pcs_standby.append(actor_id)
                    msg = f"**{character}** tentatively signed up for **{mission.title}**."

        await ctx.respond(content=msg, ephemeral=True)
        await edit_mission(ctx.guild, mission)

        if not previously_joined:
            channel_or_thread = ctx.guild.get_channel_or_thread(
                mission.channel_or_thread_id
            )
            if not channel_or_thread:
                logger.warning(f"Channel or Thread not found for {mission.title}!")
                return
            standby_str = " tentatively" if actor_id in mission.pcs_standby else ""
            await channel_or_thread.send(
                f"{ctx.user.mention} has{standby_str} signed up with {character}!"
            )

        logger.info(f"{ctx.guild.name} | {msg}")

    @game_group.command(name="list", description="List Upcoming games.")
    @option(
        "display",
        description=f"{display_ephemeral}. Defaults to Private",
        default=DISPLAY_PRIVATE,
        choices=display_choices,
    )
    async def list(self, ctx: ApplicationContext, display: str = DISPLAY_PRIVATE):
        stmt = (
            select(MissionTable)
            .where(MissionTable.guild_id == ctx.interaction.guild_id)
            .where(MissionTable.date_time > func.now() - timedelta(hours=6))
        )

        with Session() as session:
            missions = [
                Mission.model_validate(mission)
                for mission in session.scalars(stmt).all()
            ]

        embeds = []
        for mission in missions:
            embeds.append(
                mission.msg_embed(guild=ctx.guild, title=mission.title_link(ctx.bot))
            )

        if len(embeds):
            await ctx.respond(
                content="# Upcoming Games",
                embeds=embeds,
                ephemeral=display == DISPLAY_PRIVATE,
            )
        else:
            await ctx.respond("No upcoming games scheduled. 😢", ephemeral=True)

    @game_group.command(name="info", description="Lookup a session.")
    @option(
        "game", description="Session to lookup.", autocomplete=mission_info_autocomplete
    )
    async def game_info(self, ctx: ApplicationContext, game: str):
        with Session() as session:
            mission = (
                session.query(MissionTable)
                .filter_by(guild_id=ctx.guild_id, title=game)
                .one_or_none()
            )

        if mission:
            await ctx.respond(
                embed=Mission.model_validate(mission).msg_embed(ctx.guild),
                ephemeral=True,
            )
        else:
            await ctx.respond(f'**{game}** not found!', ephemeral=True)


def setup(bot: Bot):
    logger.critical("Loading")
    game = Game(bot)
    bot.add_cog(game)
