import textwrap
from datetime import datetime, timedelta, date
from textwrap import dedent

import pytz
from discord import (
    SlashCommandGroup,
    ApplicationContext,
    ScheduledEventStatus,
    TextChannel,
    Thread,
    NotFound,
    Cog,
    Embed,
    User,
    InteractionContextType,
)
from discord.commands import option
from discord.ext.commands import bot_has_guild_permissions
from discord.utils import format_dt, snowflake_time
from sqlalchemy import select, func, any_, text, and_
from sqlalchemy.exc import NoResultFound
from tabulate import tabulate

import dnd
from database import Session, CampaignTable, XpAdjustmentsTable
from database.actor_table import ActorTable
from database.game_master_table import GameMasterTable
from database.guild_settings_table import GuildSettingsTable
from database.missions import MissionTable, edit_mission, upsert_mission
from dnd.items import format_number
from dnd.rules import get_lvl, lvl_to_xp
from groups import (
    character_description,
    get_mission_for_edit,
    invite_link,
    is_gm,
    no_init_err_msg,
    DISPLAY_PRIVATE,
    DISABLE,
    DEFAULT_STR,
    DISABLE_STR,
    DEFAULT,
    detail_description,
)
from groups.autocomplete import (
    mission_cancel_autocomplete,
    mission_edit_autocomplete,
    missions_without_xp_or_gold_autocomplete,
    gm_xp_actor_autocomplete,
    campaign_autocomplete,
    skill_autocomplete,
    actor_gm_autocomplete,
    timezone_autocomplete,
    detail_gm_autocomplete,
    xp_adjustment_comment_autocomplete,
)
from groups.lookups import lookup_character

# from integrations.llm import (
#     summarize, anthropic_models, openai_models, openai_embeddings, voyage_embeddings, default_model
# )
from models import CampaignModel
from models.actor import Actor
from models.guild_settings import Subscription
from models.mission_event_manager import MissionEventManager
from models.missions import Mission
from models.socket_aware_bot import SocketAwareBot
from utils import (
    get_image_bytes,
    respond_with_long_embed,
    parse_time,
    NOT_FOUND,
    getLogger,
    join_list,
)
from views.schedule_modal import ScheduleModal

logger = getLogger(__name__)


class GM(Cog):
    def __init__(self, bot: SocketAwareBot):
        self.bot = bot
        self.mission_event_manager = MissionEventManager(bot)

    gm_group = SlashCommandGroup(
        "gm",
        "Session scheduling.",
        checks=[is_gm],
        contexts={InteractionContextType.guild},
    )
    gm_session_group = gm_group.create_subgroup(
        "session", "Session Creation and Management Options.", checks=[is_gm]
    )
    gm_xp_group = gm_group.create_subgroup("xp", "GM XP Options.", checks=[is_gm])
    gm_settings_grounp = gm_group.create_subgroup(
        "settings", "GM Settings.", checks=[is_gm]
    )
    gm_lookup_group = gm_group.create_subgroup("lookup", "GM Lookups.", checks=[is_gm])

    @gm_session_group.command(name="cancel", description="Cancel a session.")
    @bot_has_guild_permissions(manage_events=True, manage_threads=True)
    @option(
        "game",
        description="Session to cancel.",
        autocomplete=mission_cancel_autocomplete,
    )
    @option(
        "thread",
        choices=["delete", "close", "lock"],
        description="What to do with thread. Has no effect if game was created in a text channel or a preexisting thread.",
    )
    async def cancel(
        self, ctx: ApplicationContext, game: str, thread: str | None = None
    ):
        mission, error = get_mission_for_edit(game, ctx)
        errors = []

        channel_or_thread = ctx.guild.get_channel_or_thread(
            mission.channel_or_thread_id
        )
        if channel_or_thread:
            if not thread:
                thread = (
                    "delete"
                    if channel_or_thread.last_message_id == channel_or_thread.id
                    else "close"
                )

            if mission.created_thread():
                match thread:
                    case "delete":
                        await channel_or_thread.delete()
                    case "close":
                        await channel_or_thread.archive(locked=False)
                    case "lock":
                        await channel_or_thread.archive(locked=True)
            else:
                message = channel_or_thread.get_partial_message(mission.message_id)
                try:
                    await message.delete()
                except NotFound:
                    errors.append("Message not found.")
        elif mission.created_thread():
            errors.append("Thread not found.")

        scheduled_event = ctx.guild.get_scheduled_event(mission.event_id)
        if not scheduled_event:
            errors.append("Scheduled Event not found.")
        elif scheduled_event.status == ScheduledEventStatus.active:
            await scheduled_event.complete()
        elif scheduled_event.status == ScheduledEventStatus.scheduled:
            await scheduled_event.cancel()

        stmt = (
            select(MissionTable)
            .where(MissionTable.guild_id == ctx.guild_id)
            .where(MissionTable.title == mission.title)
        )
        thread_deleted = (
            mission.created_thread() and ctx.channel_id == channel_or_thread.id
        )
        try:
            with Session() as session:
                mission = session.scalar(stmt)
                session.delete(mission)
                session.commit()
        except NoResultFound as e:
            logger.error(str(e))
            errors.append("Records not found.")

        message = f"Session **{game}** canceled."
        if len(errors):
            message = "\n".join([*errors, message])
            logger.warning(message)

        self.mission_event_manager.remove(mission.id)
        if thread_deleted:
            await ctx.user.send(message)
        else:
            await ctx.respond(message, ephemeral=True)

        logger.debug(f"{mission.title} canceled.")

    @gm_session_group.command(
        name="continue", description="Schedule a continuation of a past session."
    )
    @option(
        "game",
        description="Session to continue.",
        autocomplete=mission_edit_autocomplete,
    )
    @option(
        "date_time",
        description='Date and time or "Tuesday at 8pm". If timezone is omitted assumes server default.',
    )
    @option(
        "hook", description="Session hook.", default=None, min_length=1, max_length=1024
    )
    async def game_continue(
        self, ctx: ApplicationContext, game: str, date_time: str, hook: str
    ):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        mission, mission_error = get_mission_for_edit(game, ctx)
        if mission_error:
            await ctx.respond(**mission_error)
            return

        title_arr = mission.title.split(" ")
        if len(title_arr) > 1 and title_arr[-1].isdigit():
            mission.title = " ".join(title_arr[0:-1] + [str(int(title_arr[-1]) + 1)])
        else:
            mission.title = mission.title + " 2"

        mission.id = None
        mission.xp = None
        mission.gold = None
        mission.channel_override = True

        if hook:
            mission.hook = hook
        mission.date_time, time_error = parse_time(date_time, guild_settings.timezone)

        if time_error:
            await ctx.respond(**logger.err_msg(time_error, ctx.guild_id))
            return

        mission.date_time = mission.date_time.astimezone(pytz.UTC)

        embed = mission.msg_embed(ctx.guild)
        channel_or_thread = ctx.guild.get_channel_or_thread(
            mission.channel_or_thread_id
        )
        message = await channel_or_thread.send(embed=embed)
        mission.message_id = message.id

        if mission.date_time > datetime.now(pytz.UTC):
            scheduled_event, scheduled_event_error = await mission.create_event(
                ctx.guild
            )
            if scheduled_event_error:
                await ctx.respond(scheduled_event_error, ephemeral=True)
                return

        err = upsert_mission(
            ctx.guild, mission, add_event_fun=self.mission_event_manager.upsert
        )

        await ctx.respond(
            content="\n".join(err)
            if err
            else f"**{mission.title}** successfully created.\n{message.jump_url}",
            ephemeral=True,
        )

    @gm_session_group.command(name="edit", description="Edit an existing session.")
    @option(
        "game", description="Session to edit.", autocomplete=mission_edit_autocomplete
    )
    @option(
        "title",
        description="New session title.",
        default=None,
        min_length=1,
        max_length=50,
    )
    @option(
        "hook", description="Session hook.", default=None, min_length=1, max_length=1024
    )
    @option(
        "max_players",
        description="Maximum number of players.",
        default=None,
        min_value=1,
        max_value=12,
    )
    @option(
        "gm_pc",
        description="Character you want to reward GM XP to.",
        default=None,
        autocomplete=gm_xp_actor_autocomplete,
    )
    @option(
        "gm",
        User,
        description="Transfer ownership to another GM and clears GM PC.",
        default=None,
    )
    @option(
        "date_time",
        description='Date and time or "Tuesday at 8pm". If timezone is omitted assumes server default.',
        default=None,
    )
    @option("xp", description="XP per player to assign.", default=None, min_value=0)
    @option("gold", description="Gold per player to assign.", default=None, min_value=0)
    @option(
        "image_url",
        description="Image to display on Session event and embed.",
        default=None,
    )
    async def edit(
        self,
        ctx: ApplicationContext,
        game: str,
        title: str,
        hook: str,
        max_players: int,
        gm_pc: str,
        gm: User,
        date_time: str,
        xp: int,
        gold: int,
        image_url: str,
    ):
        if not any(
            i is not None
            for i in [
                title,
                hook,
                max_players,
                gm_pc,
                gm,
                date_time,
                xp,
                gold,
                image_url,
            ]
        ):
            await ctx.respond("No changes selected.", ephemeral=True)
            return

        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        mission, mission_error = get_mission_for_edit(game, ctx)
        if mission_error:
            await ctx.respond(**mission_error)
            return

        errors = []
        embed = Embed()
        if title:
            with Session() as session:
                if (
                    session.query(func.count(MissionTable.title))
                    .filter_by(guild_id=ctx.guild_id, title=title)
                    .scalar()
                ):
                    errors.append(f"Session **{title}** already exists.")
            embed.add_field(name="Title", value=f"**{mission.title}** -> **{title}**")
            mission.title = title

        embed.title = f"Edit Session **{mission.title}**"

        if hook:
            hook = hook.replace("\\n", "\n")
            hook_str = f"**{mission.hook}** -> **{hook}**"
            if len(hook_str) > 1024:
                t = len(hook_str) - 1024
                old_t = max(len(mission.hook) - int(t / 2) - 3, 0)
                new_t = max(len(hook) - t + int(t / 2) - 3, 0)
                hook_str = f"**{mission.hook[:old_t].rstrip()}...** -> **{hook[:new_t].rstrip()}...**"

            embed.add_field(name="Hook", value=hook_str)
            mission.hook = hook

        if max_players:
            embed.add_field(
                name="Max Players",
                value=f"**{mission.max_pc_count}** -> **{max_players}**",
            )
            mission.max_pc_count = max_players

        if gm_pc and guild_settings.gm_xp and not gm:
            with Session() as session:
                if mission.gm_pc:
                    old_pc_name: str | None = (
                        session.query(ActorTable)
                        .where(
                            and_(
                                ActorTable.guild_id == ctx.guild_id,
                                ActorTable.id == mission.gm_pc,
                                ctx.interaction.user.id == any_(ActorTable.discord_ids),
                            )
                        )
                        .one()
                        .name
                    )
                else:
                    old_pc_name = None
                try:
                    new_pc: ActorTable = (
                        session.query(ActorTable)
                        .where(
                            and_(
                                ActorTable.guild_id == ctx.guild_id,
                                ActorTable.name == gm_pc,
                                ctx.interaction.user.id == any_(ActorTable.discord_ids),
                            )
                        )
                        .one()
                    )

                    embed.add_field(
                        name="GM PC", value=f"**{old_pc_name}** -> **{new_pc.name}**"
                    )
                    mission.gm_pc = new_pc.id
                except NoResultFound:
                    errors.append(f"**{gm_pc}** not found.")

        if gm:
            gm_role = ctx.guild.get_role(guild_settings.gm_role_id)
            if gm.bot:
                errors.append("GM must be human.")
            if gm not in gm_role.members:
                errors.append(f"{gm.mention} is not a member of {gm_role.mention}.")
            if not gm.bot and gm in gm_role.members:
                cur_gm = (
                    ctx.user
                    if ctx.user.id == mission.gm_id
                    else ctx.guild.get_member(mission.gm_id)
                )
                mission.gm_id = gm.id
                mission.gm_pc = None
                embed.add_field(name="GM", value=f"{cur_gm.mention} -> {gm.mention}")

        if date_time:
            date_time, time_error = parse_time(date_time, guild_settings.timezone)
            if time_error:
                errors.append(time_error)
            else:
                if (
                    datetime.now(pytz.UTC) - timedelta(seconds=1)
                    < date_time
                    < datetime.now(pytz.UTC) + timedelta(seconds=5)
                ):
                    date_time = date_time.astimezone(pytz.UTC) + timedelta(seconds=5)
                embed.add_field(
                    name="Date Time",
                    value=f"{format_dt(mission.date_time)} -> {format_dt(date_time)}",
                )
                mission.date_time = date_time.astimezone(pytz.UTC)

        if xp:
            if not str(xp).isnumeric() or xp < 0:
                errors.append("XP must be a positive number.")
            else:
                embed.add_field(name="XP", value=f"**{mission.xp}** -> **{xp}**")
                mission.xp = xp

        if gold:
            if not str(gold).isnumeric() or gold < 0:
                errors.append("Gold must be a positive number.")
            else:
                embed.add_field(name="Gold", value=f"**{mission.gold}** -> **{gold}**")
                mission.gold = gold

        image_bytes = get_image_bytes(image_url)
        if image_url:
            if not image_bytes:
                errors.append(f"{image_url} is not a valid image url.")
            else:
                embed.add_field(
                    name="Image URL",
                    value=f"**{mission.image_url if mission.image_url else 'NONE'}** -> **{image_url}**",
                )
                mission.image_url = image_url

        if errors:
            msg = "\n".join([f"- {e}" for e in errors])
            try:
                await ctx.respond(**logger.err_msg(msg, ctx.guild_id))
            except NotFound as nf:
                logger.error(str(nf), stack_info=True)
                await ctx.user.send("- Message Context not found!\n" + msg)
        else:
            await ctx.respond(embed=embed, ephemeral=True)
            await edit_mission(
                ctx.guild, mission, self.mission_event_manager.upsert, image_bytes
            )
        logger.debug(mission)

    @gm_session_group.command(name="xp", description="Post session XP and Gold.")
    @option(
        "game",
        description="Game to set rewards for.",
        autocomplete=missions_without_xp_or_gold_autocomplete,
    )
    @option("xp", description="XP per player to assign.", min_value=0)
    @option("gold", description="Gold per player to assign.", min_value=0, default=0)
    async def reward_exp(self, ctx: ApplicationContext, game: str, xp: int, gold: int):
        await self.reward(ctx, game, xp, gold)

    @gm_session_group.command(name="reward", description="Post game XP and Gold.")
    @option(
        "game",
        description="Game to set rewards for.",
        autocomplete=missions_without_xp_or_gold_autocomplete,
    )
    @option("xp", description="XP per player to assign.", min_value=0)
    @option("gold", description="Gold per player to assign.", min_value=0, default=0)
    async def reward(self, ctx: ApplicationContext, game: str, xp: int, gold: int):
        mission, error = get_mission_for_edit(game, ctx)
        if not mission:
            await ctx.respond(**error)
            return
        if not str(gold).isnumeric() or gold < 0:
            await ctx.respond(
                **logger.err_msg("Gold must be a positive number.", ctx.guild_id)
            )
            return
        if not str(xp).isnumeric() or xp < 0:
            await ctx.respond(
                **logger.err_msg("XP must be a positive number.", ctx.guild_id)
            )
            return

        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)

        mission.xp = xp
        mission.gold = gold

        out_str = join_list(
            [
                f"**{format_number(xp, 'XP')}**" if xp else None,
                f"**{format_number(gold)}**" if gold else None,
            ],
            " and ",
        )

        await ctx.respond(f"{out_str or 'Nothing'} rewarded for **{game}**!")

        actors_names_to_levels_before = {
            a.name: get_lvl(a.get_exp(guild_settings)) for a in mission.get_actors()
        }
        await edit_mission(ctx.guild, mission)

        if xp:
            name_id_xp = [
                [a.name, a.id, a.get_exp(guild_settings)] for a in mission.get_actors()
            ]
            actor_names_to_levels = {name: get_lvl(xp) for [name, _, xp] in name_id_xp}

            out_str = "\n".join(
                [
                    f"**{name}**: {actors_names_to_levels_before[name]} -> {lvl}"
                    for (name, lvl) in actor_names_to_levels.items()
                    if lvl > actors_names_to_levels_before[name]
                ]
            )

            if out_str:
                await ctx.respond(out_str)

            await self.bot.socket_namespace.xp_sync(
                guild_settings, {_id: xp for [_, _id, xp] in name_id_xp}
            )

            logger.debug(f"reward for:\n{mission}")

    @gm_xp_group.command(name="adjust", description="One off Character XP adjustments.")
    @option(
        "character",
        parameter_name="actor_name",
        description="Character to adjust xp for.",
        autocomplete=actor_gm_autocomplete,
    )
    @option("xp", description="XP per player to assign.")
    @option("comment", description="Reason for XP reward.")
    async def reward_exp_actor(
        self, ctx: ApplicationContext, actor_name: str, xp: int, comment: str
    ):
        if xp == 0:
            await ctx.respond(
                **logger.err_msg(
                    "XP adjustment must be a non-zero number!", ctx.guild_id
                )
            )
            return

        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        if guild_settings.subscription == Subscription.none:
            await ctx.respond(
                **logger.err_msg(
                    f"{ctx.command.name.capitalize()} is a supporter only feature.\n{invite_link(ctx)}",
                    ctx.guild_id,
                )
            )
            return

        with Session() as session:
            pc_id = session.scalar(
                select(ActorTable.id).filter_by(guild_id=ctx.guild_id, name=actor_name)
            )
            session.add(
                XpAdjustmentsTable(
                    guild_id=ctx.guild_id,
                    actor_id=pc_id,
                    xp=xp,
                    comment=comment,
                    date=date.today(),
                )
            )
            session.commit()

            await ctx.respond(
                f"`{abs(xp)}` XP {'added to' if xp > 0 else 'subtracted from'} **{actor_name}** for *{comment}.*",
                ephemeral=True,
            )

    @gm_xp_group.command(
        name="delete_adjustment", description="Remove one off Character XP adjustment."
    )
    @option(
        "character",
        parameter_name="actor_name",
        description="Character to adjust xp for.",
        autocomplete=actor_gm_autocomplete,
    )
    @option(
        "comment",
        description="Reason for XP reward.",
        autocomplete=xp_adjustment_comment_autocomplete,
    )
    async def undo_reward_exp_actor(
        self, ctx: ApplicationContext, actor_name: str, comment: str
    ):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        if guild_settings.subscription == Subscription.none:
            await ctx.respond(
                **logger.err_msg(
                    f"{ctx.command.name.capitalize()} is a supporter only feature.\n{invite_link(ctx)}",
                    ctx.guild_id,
                )
            )
            return

        stmt = (
            select(XpAdjustmentsTable)
            .join(
                ActorTable,
                and_(
                    ActorTable.id == XpAdjustmentsTable.actor_id,
                    ActorTable.guild_id == ctx.guild_id,
                    ActorTable.name == actor_name,
                ),
            )
            .filter(
                XpAdjustmentsTable.guild_id == ctx.interaction.guild_id,
                XpAdjustmentsTable.comment == comment,
            )
        )

        with Session() as session:
            adjustment = session.scalar(stmt)
            session.delete(adjustment)
            session.commit()

        await ctx.respond(
            f"XP Adjustment **{comment}** removed from **{actor_name}**", ephemeral=True
        )

    @gm_xp_group.command(name="info", description="List all sources of Character XP.")
    @option(
        "character",
        parameter_name="actor_name",
        description="Character to explain XP for.",
        autocomplete=actor_gm_autocomplete,
    )
    async def explain_exp_actor(self, ctx: ApplicationContext, actor_name: str):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        if guild_settings.subscription == Subscription.none:
            await ctx.respond(
                **logger.err_msg(
                    f"{ctx.command.name.capitalize()} is a supporter only feature.\n{invite_link(ctx)}",
                    ctx.guild_id,
                ),
                ephemeral=True,
            )
            return

        with Session() as session:
            actor = Actor.model_validate(
                session.scalar(
                    select(ActorTable).filter_by(guild_id=ctx.guild_id, name=actor_name)
                )
            )
            adjustments = session.scalars(
                select(XpAdjustmentsTable).filter_by(
                    guild_id=ctx.guild_id, actor_id=actor.id
                )
            ).all()
            missions = session.scalars(
                select(MissionTable).filter(
                    MissionTable.guild_id == ctx.guild_id,
                    MissionTable.pcs.any(actor.id),
                )
            ).all()

            gm_missions = session.scalars(
                select(MissionTable).filter(
                    MissionTable.guild_id == ctx.guild_id,
                    MissionTable.gm_pc == actor.id,
                )
            ).all()

            campaigns = session.scalars(
                select(CampaignTable).filter(
                    (CampaignTable.guild_id == guild_settings.id)
                    & (any_(CampaignTable.actor_ids) == actor.id)
                )
            ).all()

        if campaigns:
            first_campaign_group: CampaignTable = min(
                campaigns, key=lambda campaign: snowflake_time(campaign.id)
            )
            starting_lvl = first_campaign_group.starting_level
            starting_xp_label = (
                f"{first_campaign_group.name} Starting Level {starting_lvl}"
            )
        else:
            starting_lvl = guild_settings.starting_level
            starting_xp_label = f"Starting Level {starting_lvl}"

        xp_sources = sorted(
            [
                *[{"name": a.comment, "xp": a.xp, "date": a.date} for a in adjustments],
                *[
                    {"name": m.title, "xp": m.xp, "date": m.date_time.date()}
                    for m in missions
                ],
                *[
                    {"name": f"{m.title} GM", "xp": m.gp_xp, "date": m.date_time.date()}
                    for m in gm_missions
                ],
            ],
            key=lambda x: x["date"],
        )

        xp_total = actor.get_exp(guild_settings)
        tabular_data = [
            [starting_xp_label, lvl_to_xp[starting_lvl]],
            *[[x["name"], x["xp"]] for x in xp_sources],
            ["TOTAL", xp_total],
        ]
        assert sum(row[1] or 0 for row in tabular_data[:-1]) == xp_total

        xp_table = tabulate(tabular_data, ["Source", "XP"], "rounded_outline")
        lines = str(xp_table).split("\n")
        await ctx.respond(
            "\n".join(
                [f"**{actor_name}**", "```", *lines[:-2], lines[2], *lines[-2:], "```"]
            )
        )

    @gm_session_group.command(name="schedule", description="Schedule a session.")
    @bot_has_guild_permissions(manage_events=True, manage_threads=True)
    @option(
        "max_players",
        int,
        description="Maximum number of players.",
        min_value=1,
        max_value=12,
    )
    @option(
        "campaign",
        autocomplete=campaign_autocomplete,
        description="Overrides campaign. Setting a campaign will invite it's characters to the session.",
    )
    @option(
        "channel",
        description="Overrides default channel or thread. If thread, you must be following it.",
    )
    async def schedule(
        self,
        ctx: ApplicationContext,
        max_players: int = 5,
        campaign: str | None = None,
        channel: Thread | TextChannel | None = None,
    ):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        if guild_settings.subscription == Subscription.none:
            await ctx.respond(
                **logger.err_msg(
                    f"{ctx.command.name.capitalize()} is a supporter only feature.\n{invite_link(ctx)}",
                    ctx.guild_id,
                )
            )
            return

        if guild_settings.gm_xp:
            with Session() as session:
                gm_pc_name = (
                    session.query(ActorTable.name)
                    .filter_by(guild_id=ctx.guild_id)
                    .filter(ctx.interaction.user.id == any_(ActorTable.discord_ids))
                    .limit(1)
                    .scalar()
                )
        else:
            gm_pc_name = None

        if campaign:
            with Session() as session:
                campaign = CampaignModel.model_validate(
                    session.query(CampaignTable)
                    .filter_by(name=campaign, guild_id=ctx.guild_id)
                    .one()
                )
                max_players = max(max_players, len(campaign.actor_ids))

        mission = Mission(
            guild_id=ctx.guild_id,
            gm_id=ctx.user.id,
            gm_xp=guild_settings.gm_xp,
            max_pc_count=max_players,
            channel_override=bool(channel),
            campaign_id=campaign.id if campaign else None,
        )

        await ctx.send_modal(
            ScheduleModal(
                title="Session Schedule",
                mission=mission,
                gm_pc_name=gm_pc_name,
                guild_settings=guild_settings,
                mission_event_manager=self.mission_event_manager,
                channel_override=channel,
                initiating_user=ctx.user,
                campaign=campaign,
            )
        )

    # @gm_session_group.command(name="summarize", description="Summarize a session.")
    # @option("query", description="Summarization query.", default=default_summary_query, max_length=1024)
    # @option("model", description="LLM Model", default=default_model, choices=anthropic_models[:25])
    # async def summary(self, ctx: ApplicationContext, query: str, model: str, embedding: str = 'voyage-large-2'):
    #     if ctx.guild_id not in [oronder_dnd_server_id]:
    #         await ctx.respond(
    #             **logger.err_msg(
    #                 f"`/game summarize` disabled for this server.",
    #                 ctx.guild_id))
    #         return
    #
    #     with Session() as session:
    #         missions = [
    #             Mission.model_validate(m) for m in
    #             session.query(MissionTable).filter_by(
    #                 guild_id=ctx.guild_id,
    #                 channel_or_thread_id=ctx.interaction.channel_id
    #             ).all()
    #         ]
    #
    #     if not missions:
    #         await ctx.respond(
    #             **logger.err_msg(
    #                 f"`/game summarize` must be run from a channel with a game. (see `/gm session schedule`)",
    #                 ctx.guild_id))
    #         return
    #
    #     if not isinstance(ctx.interaction.channel, TextChannel) and not isinstance(ctx.interaction.channel, Thread):
    #         await ctx.respond(**logger.err_msg(
    #             f"`/gm session` summarize cannot be run from a {type(ctx.interaction.channel).__name__}.",
    #             ctx.guild_id
    #         ))
    #         return
    #
    #     if model in openai_models and embedding not in openai_embeddings:
    #         embedding = 'text-embedding-ada-002'
    #     elif model in anthropic_models and embedding not in voyage_embeddings:
    #         embedding = 'voyage-2'
    #
    #     response = await ctx.respond(content=f'generating session summary with `{model}`/`{embedding}`.',
    #                                  ephemeral=True)
    #
    #     try:
    #         summary = await summarize(
    #             query=query,
    #             missions=missions,
    #             channel=ctx.interaction.channel,
    #             model=model,
    #             embedding=embedding
    #         )
    #         embed = Embed(title="Session Summary")
    #         embed.add_field(name=('' if query == default_summary_query else truncate(query, 256)), value=summary)
    #         await ctx.bot.get_channel(ctx.interaction.channel_id).send(embed=embed)
    #         return
    #     except Exception as e:
    #         if 'AuthenticationError' in type(e).__name__:
    #             err_msg = "`Incorrect API key provided`"
    #             logger.error(err_msg, stack_info=True)
    #         else:
    #             err_msg = logger.err_msg(f"Failed to generate summary!\n```\n{repr(e)}\n```", ctx.guild_id)['content']
    #
    #         await response.edit(content=err_msg)

    @gm_settings_grounp.command(name="edit", description="Edit GM settings.")
    @option(
        "timezone",
        str,
        description="Override default timezone for scheduling.",
        autocomplete=lambda ctx: [DEFAULT, *timezone_autocomplete(ctx)],
    )
    @option(
        "campaign",
        str,
        description="Set this if you run non-westmarch style games. See `/campaign ...`",
        autocomplete=lambda ctx: [DISABLE, *campaign_autocomplete(ctx)],
    )
    async def gm_settings(
        self,
        ctx: ApplicationContext,
        timezone: str | None = None,
        campaign: str | None = None,
    ):
        if not timezone and not campaign:
            await ctx.respond("No changes selected.", ephemeral=True)
            return
        with Session() as session:
            gm_settings = session.query(GameMasterTable).filter_by(
                id=ctx.user.id, guild_id=ctx.guild_id
            ).one_or_none() or GameMasterTable(id=ctx.user.id, guild_id=ctx.guild_id)

            embed = Embed(title="GM Settings Updated")
            if timezone:
                old_tz_name = (
                    gm_settings.timezone if gm_settings.timezone else DEFAULT_STR
                )
                new_tz_name = timezone if timezone != DEFAULT else DEFAULT_STR
                embed.add_field(
                    name="Timezone", value=f"**{old_tz_name}** -> **{new_tz_name}**"
                )
                gm_settings.timezone = timezone if timezone != DEFAULT else None

            if campaign:
                old_campaign_name = (
                    session.query(CampaignTable.name)
                    .filter_by(
                        id=gm_settings.default_campaign_id, guild_id=ctx.guild_id
                    )
                    .scalar()
                    or DISABLE_STR
                )

                campaign_id = (
                    None
                    if campaign == DISABLE
                    else session.query(CampaignTable.id)
                    .filter_by(name=campaign, guild_id=ctx.guild_id)
                    .scalar()
                )

                embed.add_field(
                    name="Default Campaign",
                    value=f"**{old_campaign_name}** -> **{DISABLE_STR if campaign == DISABLE else campaign}**",
                )
                gm_settings.default_campaign_id = campaign_id

            session.merge(gm_settings)
            session.commit()

        await ctx.respond(embed=embed, ephemeral=True)

    @gm_settings_grounp.command(name="info", description="Show GM Settings.")
    async def gm_info(self, ctx: ApplicationContext):
        with Session() as session:
            gm_settings = session.query(GameMasterTable).filter_by(
                id=ctx.user.id, guild_id=ctx.guild_id
            ).one_or_none() or GameMasterTable(id=ctx.user.id, guild_id=ctx.guild_id)
        embed = Embed(title="GM Settings")

        if gm_settings.default_campaign_id:
            with Session() as session:
                campaign = (
                    session.query(CampaignTable.name)
                    .filter_by(
                        guild_id=ctx.guild_id, id=gm_settings.default_campaign_id
                    )
                    .scalar()
                )

            campaign_name = campaign or NOT_FOUND
        else:
            campaign_name = DISABLE_STR

        if gm_settings.timezone:
            tz_str = gm_settings.timezone
        else:
            with Session() as session:
                guild_tz = (
                    session.query(GuildSettingsTable.timezone)
                    .filter_by(id=ctx.guild_id)
                    .scalar()
                )
                tz_str = f"{DEFAULT_STR} ({guild_tz})"

        embed.add_field(name="Default Campaign", value=campaign_name)
        embed.add_field(name="Timezone", value=tz_str)

        await ctx.respond(embed=embed, ephemeral=True)

    @gm_lookup_group.command(
        name="character", description="Look up your player's character details."
    )
    @option(
        "character",
        description=character_description,
        autocomplete=actor_gm_autocomplete,
    )
    @option(
        "detail",
        description=detail_description,
        default=None,
        autocomplete=detail_gm_autocomplete,
    )
    async def pc_lookup(self, ctx: ApplicationContext, character: str, detail: str):
        return await lookup_character(
            ctx, character, detail, DISPLAY_PRIVATE, self.bot.socket_namespace, gm=True
        )

    @gm_lookup_group.command(
        name="passive",
        description="Retrieve passive skill scores for all PCs active in current channel.",
    )
    @option("skill", autocomplete=skill_autocomplete)
    async def passive_check(self, ctx: ApplicationContext, skill):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        with Session() as session:
            stmt = text(
                textwrap.dedent(f"""
                SELECT distinct on (actors.id) actors.name, actors.skills -> '{dnd.abreviate_stat_name(skill)}' ->> 'passive' as passive
                FROM missions
                JOIN LATERAL unnest(missions.pcs::text[]) AS actor_id ON true
                JOIN actors ON actor_id = actors.id
                WHERE missions.channel_or_thread_id = {ctx.channel_id};
            """)
            )

            name_passive = session.execute(stmt).all()

        if not name_passive:
            await ctx.respond(**logger.err_msg('No Characters Found.', ctx.guild_id))
            return
        else:
            embed = Embed(title=f"Passive {skill}")
            for pair in name_passive:
                embed.add_field(name=pair[0], value=pair[1])

            await respond_with_long_embed(ctx, embed, ephemeral=True)


def setup(bot: SocketAwareBot):
    logger.critical("Loading")
    gm = GM(bot)
    bot.add_cog(gm)
