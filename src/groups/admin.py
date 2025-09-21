import asyncio
import secrets
import textwrap
from asyncio import Task
from datetime import datetime
from typing import Dict, Tuple

import pytz
from discord import (
    SlashCommandGroup,
    ApplicationContext,
    option,
    Embed,
    Guild,
    TextChannel,
    ForumChannel,
    VoiceChannel,
    Role,
    Cog,
    Permissions,
    StageChannel,
    InteractionContextType,
    PartialMessage,
    Forbidden,
    Message,
    Interaction,
)
from discord.ext.commands import Bot, bot_has_guild_permissions
from discord.utils import format_dt
from sqlalchemy.exc import NoResultFound

from database import Session
from database.guild_settings_table import GuildSettingsTable
from system.items import format_number
from groups import no_init_err_msg
from groups.autocomplete import timezone_autocomplete
from models.guild_settings import GuildSettings, current_subscription, Day
from utils import (
    is_url,
    hours_list,
    mention_safe,
    chris_discord_id,
    my_guild_ids,
    getLogger,
)

logger = getLogger(__name__)

gm_xp_str = (
    "How much experience a GM may reward one of their PCs for running a session."
)
starting_lvl_str = "Starting level for new PCs. Override by assigning a PC to a Campaign. (/campaign ...)"
tz_str = "Default timezone for scheduling and roll calls."
foundry_url_str = (
    "URL for Foundry VTT server. ex: https://my_foundry.com or http://1.2.3.4:5678"
)
downtime_channel_str = "Text Channel where players will post downtime activities."
voice_channel_str = "Voice Channel for sessions."
scheduling_channel_str = "Text Channel where new games will be advertised."
gm_role_str = "Discord Role for GMs."
session_channel_str = "Forum or Text Channel where games will be held."


class Admin(Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.rollcalls: Dict[int, Task] = {}

        async def init_rollcalls():
            await self.bot.wait_until_ready()
            for guild in self.bot.guilds:
                guild_settings = GuildSettingsTable.lookup(guild.id)
                if guild_settings and guild_settings.rollcall_enabled:
                    self.rollcalls[guild.id] = self.bot.loop.create_task(
                        self.sleep_for_rollcall(self.bot, guild, guild_settings)
                    )

        self.bot.loop.create_task(init_rollcalls())

    admin_group = SlashCommandGroup(
        "admin",
        "Oronder Management",
        default_member_permissions=Permissions(administrator=True),
        contexts={InteractionContextType.guild},
    )

    super_admin_group = SlashCommandGroup(
        "superadmin",
        "Oronder Bot Management",
        default_member_permissions=Permissions(administrator=True),
        contexts={InteractionContextType.guild},
    )

    @staticmethod
    async def get_msg(
        ctx: ApplicationContext, link: str
    ) -> Tuple[Message | PartialMessage | None, Dict | None]:
        split = [int(n) for n in link.split("/") if n.isnumeric()]
        if len(split) != 3:
            out = logger.err_msg("Invalid Link", ctx.guild_id)
        elif ctx.user.id != chris_discord_id:
            out = logger.err_msg("Insufficient Permissions", ctx.guild_id)
        else:
            guild = ctx.bot.get_guild(split[0])
            if not guild:
                out = logger.err_msg("Invalid Server ID", ctx.guild_id)
            else:
                channel_or_thread = guild.get_channel_or_thread(split[1])
                if not channel_or_thread:
                    out = logger.err_msg("Invalid Channel or Thread ID", ctx.guild_id)
                else:
                    try:
                        message = await channel_or_thread.fetch_message(split[2])
                    except Forbidden:
                        message = channel_or_thread.get_partial_message(split[2])

                    if not message:
                        out = logger.err_msg("Invalid Message ID", ctx.guild_id)
                    else:
                        return message, {"content": "", "ephemeral": True}

        return None, out

    @super_admin_group.command(
        name="delete_bot_message",
        description="Delete Bot Message.",
        guild_ids=my_guild_ids,
    )
    @option("link", str, description="Message Link.")
    async def delete_msg(self, ctx: ApplicationContext, link: str):
        message, response = await self.get_msg(ctx, link)
        if message:
            await message.delete()
            response["content"] = "Message Deleted"
        await ctx.respond(**response)

    @super_admin_group.command(
        name="react", description="React to Message with Emoji", guild_ids=my_guild_ids
    )
    @option("link", str, description="Message Link.")
    @option(
        "emoji",
        str,
        parameter_name="emoji_names",
        description="Comma seperated list of emoji names.",
    )
    async def react_msg(self, ctx: ApplicationContext, link: str, emoji_names: str):
        message, response = await self.get_msg(ctx, link)
        response_interaction: Interaction = None
        try:
            awaitables = []
            if message:
                emoji_name_list = [n.strip() for n in emoji_names.split(",")]

                removed_emoji_names = []
                if isinstance(message, Message):
                    reactions = [
                        r
                        for r in message.reactions
                        if r.emoji.name in emoji_name_list
                        and ctx.bot.application_id in [u.id async for u in r.users()]
                    ]
                    if reactions:
                        awaitables.extend([r.remove(ctx.bot.user) for r in reactions])
                        removed_emoji_names = [r.emoji.name for r in reactions]
                        response["content"] += "Removed: " + "".join(
                            [str(r.emoji) for r in reactions]
                        )

                emojis = [
                    e
                    for e in self.bot.app_emojis
                    if e.name in emoji_name_list and e.name not in removed_emoji_names
                ]

                if emojis:
                    awaitables.extend([message.add_reaction(emoji) for emoji in emojis])
                    response["content"] = (
                        response["content"]
                        + "\nAdded: "
                        + "".join([str(e) for e in emojis])
                    ).lstrip()

            response_interaction = await ctx.respond(**response)
            await asyncio.gather(*awaitables)
        except Exception as e:
            response["content"] = (response["content"] + "\n\n" + str(e)).strip()
            if response_interaction:
                await response_interaction.edit(content=response["content"])
            else:
                await ctx.respond(**response)

    @admin_group.command(name="init", description="Initialize Oronder.")
    @option("foundry_url", str, description=foundry_url_str)
    @option("timezone", str, description=tz_str, autocomplete=timezone_autocomplete)
    @option(
        "starting_level", int, min_value=1, max_value=20, description=starting_lvl_str
    )
    @option("gm_role", Role, description=gm_role_str)
    @option(
        "session_channel",
        ForumChannel | TextChannel,
        default=None,
        description=session_channel_str,
    )
    @option(
        "scheduling_channel",
        TextChannel,
        default=None,
        description=scheduling_channel_str,
    )
    @option(
        "voice_channel",
        VoiceChannel | StageChannel,
        default=None,
        description=voice_channel_str,
    )
    @option(
        "downtime_channel", TextChannel, default=None, description=downtime_channel_str
    )
    @option(
        "downtime_gm_channel",
        TextChannel,
        default=None,
        description="Optional separate Channel for GMs to manage downtime activities.",
    )
    @option("gm_xp", int, min_value=0, default=0, description=gm_xp_str)
    async def init(
        self,
        ctx: ApplicationContext,
        foundry_url: str,
        timezone: str,
        starting_level: int,
        gm_role: Role,
        session_channel: ForumChannel | TextChannel | None = None,
        scheduling_channel: TextChannel | None = None,
        voice_channel: VoiceChannel | StageChannel | None = None,
        downtime_channel: TextChannel | None = None,
        downtime_gm_channel: TextChannel | None = None,
        gm_xp: int = 0,
    ):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if guild_settings:
            await ctx.respond(
                **logger.err_msg(
                    "Oronder has already been initialized. Consider `/admin edit`",
                    ctx.guild_id,
                )
            )
            return

        if not scheduling_channel or not downtime_channel or not session_channel:
            text_channels = ctx.guild.text_channels
            if not text_channels:
                await ctx.respond(
                    **logger.err_msg(
                        "Must have at least one text channel!", ctx.guild_id
                    )
                )
                return

            default_text_channel = next(
                (c for c in text_channels if c.name == "general"), text_channels[0]
            )

            if not session_channel:
                session_channel = default_text_channel
            if not scheduling_channel:
                scheduling_channel = default_text_channel
            if not downtime_channel:
                downtime_channel = default_text_channel

        if not voice_channel:
            voice_channels = ctx.guild.voice_channels
            if not voice_channels:
                await ctx.respond(
                    **logger.err_msg(
                        "Must have at least one voice channel!", ctx.guild_id
                    )
                )
                return
            voice_channel = next(
                (c for c in voice_channels if c.name == "General"), voice_channels[0]
            )

        token = secrets.token_urlsafe()
        guild_settings = GuildSettings(
            id=ctx.guild_id,
            gm_role_id=gm_role.id,
            gm_xp=gm_xp,
            scheduling_channel_id=scheduling_channel.id,
            session_channel_id=session_channel.id,
            voice_channel_id=voice_channel.id,
            downtime_channel_id=downtime_channel.id,
            downtime_gm_channel_id=downtime_gm_channel.id
            if downtime_gm_channel
            else None,
            subscription=current_subscription(ctx.bot, ctx.guild),
            foundry_hostname=foundry_url,
            auth_token=token,
            timezone=timezone,
            starting_level=starting_level,
        )

        errs = guild_settings.validate_channels(ctx.guild)
        if not is_url(foundry_url):
            errs.append(f"{foundry_url} is not a valid url.")
        if errs:
            await ctx.respond(
                **logger.err_msg(
                    "Could not initialize Oronder!\n- " + "\n- ".join(errs),
                    ctx.guild_id,
                )
            )
            return

        GuildSettingsTable.commit(guild_settings)
        msg = textwrap.dedent(
            f"""Oronder has been successfully initialized. Your Foundry VTT token is:
            `{token}`
            Do not share this with anyone. You may regenerate this at any time using `/admin reset_token`."""
        )
        if (
            next((r for r in ctx.guild.roles if r.permissions.use_slash_commands), None)
            is None
        ):
            msg += textwrap.dedent("""
            
                **WARNING**: All Roles lack the *Use Application Commands* permission! 
                In Discord, go to **Server Settings** > **Roles** > **Default Permissions**.
                 In the text box search for *Use Application Commands* and enable it.""")

        await ctx.respond(
            content=f"Oronder has been successfully initialized. Your Foundry VTT token is:\n`{token}`"
            + "\nYou may regenerate this at any time using `/admin reset_token`.",
            ephemeral=True,
        )

    @admin_group.command(name="info", description="Show Oronder Settings.")
    async def info(self, ctx: ApplicationContext):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        embed = Embed(title="Oronder Settings")

        gm_role = ctx.guild.get_role(guild_settings.gm_role_id)
        embed.add_field(name="GM Role", value=mention_safe(gm_role))
        embed.add_field(
            name="GM Experience",
            value=format_number(guild_settings.gm_xp, "XP"),
        )
        embed.add_field(
            name="Scheduling Channel",
            value=mention_safe(
                ctx.guild.get_channel(guild_settings.scheduling_channel_id)
            ),
        )
        session_channel = ctx.guild.get_channel(guild_settings.session_channel_id)
        embed.add_field(
            name=f"Session {'Forum' if isinstance(session_channel, ForumChannel) else 'Channel'}",
            value=mention_safe(
                ctx.guild.get_channel(guild_settings.session_channel_id)
            ),
        )
        voice_channel = ctx.guild.get_channel(guild_settings.voice_channel_id)
        vc_name = (
            "Voice Channel"
            if isinstance(voice_channel, VoiceChannel)
            else "Stage Channel"
        )
        embed.add_field(
            name=vc_name,
            value=mention_safe(voice_channel),
        )
        embed.add_field(
            name="Downtime Channel",
            value=mention_safe(
                ctx.guild.get_channel(guild_settings.downtime_channel_id)
            ),
        )
        if guild_settings.downtime_gm_channel_id:
            embed.add_field(
                name="Downtime GM Channel",
                value=mention_safe(
                    ctx.guild.get_channel(guild_settings.downtime_gm_channel_id)
                ),
            )
        embed.add_field(
            name="Timezone",
            value=guild_settings.timezone,
        )
        if guild_settings.rollcall_enabled:
            rc_role = ctx.guild.get_role(guild_settings.rollcall_role_id)
            embed.add_field(
                name="Roll Call Channel",
                value=mention_safe(
                    ctx.guild.get_channel(guild_settings.rollcall_channel_id)
                ),
            )
            embed.add_field(name="Roll Call Role", value=mention_safe(rc_role))
            embed.add_field(
                name="Next Roll Call", value=format_dt(guild_settings.next_rollcall())
            )
        else:
            embed.add_field(name="Roll Call", value="Disabled")

        embed.add_field(
            name="Foundry VTT URL",
            value=guild_settings.foundry_hostname,
        )
        embed.add_field(
            name="Subscription",
            value=guild_settings.subscription.name.capitalize(),
        )

        await ctx.respond(embed=embed, ephemeral=True)

    @admin_group.command(name="edit", description="Edit Oronder Settings.")
    @option("gm_role", Role, description=gm_role_str)
    @option("scheduling_channel", TextChannel, description=scheduling_channel_str)
    @option(
        "session_channel", ForumChannel | TextChannel, description=session_channel_str
    )
    @option("voice_channel", VoiceChannel | StageChannel, description=voice_channel_str)
    @option("downtime_channel", TextChannel, description=downtime_channel_str)
    @option(
        "downtime_gm_channel",
        TextChannel,
        description="Separate Channel for GMs to manage downtime activities. Set to downtime_channel to disable.",
    )
    @option("foundry_url", str, description=foundry_url_str)
    @option("timezone", str, description=tz_str, autocomplete=timezone_autocomplete)
    @option(
        "starting_level", int, min_value=1, max_value=20, description=starting_lvl_str
    )
    @option("gm_xp", int, min_value=0, description=gm_xp_str)
    async def edit(
        self,
        ctx: ApplicationContext,
        gm_role: Role | None = None,
        scheduling_channel: TextChannel | None = None,
        session_channel: ForumChannel | TextChannel | None = None,
        voice_channel: VoiceChannel | StageChannel | None = None,
        downtime_channel: TextChannel | None = None,
        downtime_gm_channel: TextChannel | None = None,
        foundry_url: str | None = None,
        timezone: str | None = None,
        starting_level: int | None = None,
        gm_xp: int | None = None,
    ):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return
        if not any(
            i is not None
            for i in [
                gm_role,
                scheduling_channel,
                session_channel,
                voice_channel,
                downtime_channel,
                downtime_gm_channel,
                foundry_url,
                timezone,
                starting_level,
                gm_xp,
            ]
        ):
            await ctx.respond("No changes selected.", ephemeral=True)
            return

        embed = Embed(title="Oronder Settings Update")

        if foundry_url:
            embed.add_field(
                name="Foundry URL",
                value=f"**{guild_settings.foundry_hostname}** -> **{foundry_url}**",
            )
            guild_settings.foundry_hostname = foundry_url

        if timezone:
            embed.add_field(
                name="Timezone",
                value=f"**{guild_settings.timezone}** -> **{timezone}**",
            )
            if guild_settings.rollcall_day:
                next_rollcall = guild_settings.next_rollcall()
                guild_settings.rollcall_day = next(
                    d for d in Day if d.value == next_rollcall.day
                )
                guild_settings.rollcall_time = next_rollcall.time()
            guild_settings.timezone = timezone
            if guild_settings.rollcall_day:
                if ctx.guild_id in self.rollcalls:
                    self.rollcalls[ctx.guild_id].cancel()
                self.rollcalls[ctx.guild.id] = self.bot.loop.create_task(
                    self.sleep_for_rollcall(self.bot, ctx.guild, guild_settings)
                )

        if gm_role:
            embed.add_field(
                name="GM Role",
                value=f"**{mention_safe(ctx.guild.get_role(guild_settings.gm_role_id))}** -> **{mention_safe(gm_role)}**",
            )
            guild_settings.gm_role_id = gm_role.id

        if scheduling_channel:
            embed.add_field(
                name="Scheduling Channel",
                value=f"{mention_safe(ctx.guild.get_channel(guild_settings.scheduling_channel_id))} -> {scheduling_channel.mention}",
            )
            guild_settings.scheduling_channel_id = scheduling_channel.id

        if session_channel:
            channel_type_str = (
                "Channel" if isinstance(session_channel, TextChannel) else "Forum"
            )
            embed.add_field(
                name=f"Session {channel_type_str}",
                value=f"{mention_safe(ctx.guild.get_channel(guild_settings.session_channel_id))} -> {session_channel.mention}",
            )
            guild_settings.session_channel_id = session_channel.id

        if voice_channel:
            vc_name = (
                "Voice Channel"
                if isinstance(voice_channel, VoiceChannel)
                else "Stage Channel"
            )
            embed.add_field(
                name=vc_name,
                value=f"{mention_safe(ctx.guild.get_channel(guild_settings.voice_channel_id))} -> {voice_channel.mention}",
            )
            guild_settings.voice_channel_id = voice_channel.id

        if downtime_channel:
            embed.add_field(
                name="Downtime Channel",
                value=f"{mention_safe(ctx.guild.get_channel(guild_settings.downtime_channel_id))} -> {downtime_channel.mention}",
            )
            guild_settings.downtime_channel_id = downtime_channel.id

        if downtime_gm_channel:
            embed.add_field(
                name="Downtime GM Channel",
                value=f"{mention_safe(ctx.guild.get_channel(guild_settings.downtime_gm_channel_id))} -> {downtime_gm_channel.mention}",
            )
            if downtime_gm_channel == downtime_channel:
                guild_settings.downtime_gm_channel_id = None
            else:
                guild_settings.downtime_gm_channel_id = downtime_gm_channel.id

        if starting_level is not None:
            embed.add_field(
                name="Starting Level",
                value=f"{guild_settings.starting_level} -> {starting_level}",
            )
            guild_settings.starting_level = starting_level

        if gm_xp is not None:
            embed.add_field(
                name="GM XP",
                value=f"{'{:,.0f}'.format(guild_settings.gm_xp)} -> {'{:,.0f}'.format(gm_xp)}",
            )
            guild_settings.gm_xp = gm_xp

        errs = guild_settings.validate_channels(ctx.guild)

        if not is_url(guild_settings.foundry_hostname):
            errs.append(f"{guild_settings.foundry_hostname} is not a valid url.")

        if errs:
            await ctx.respond(
                **logger.err_msg(
                    "Errors in configuration found!\n- " + "\n- ".join(errs),
                    ctx.guild_id,
                )
            )
        else:
            GuildSettingsTable.commit(guild_settings)
            await ctx.respond(embed=embed, ephemeral=True)

    @admin_group.command(
        name="reset_token", description="Regenerate Foundry VTT auth token."
    )
    @option(
        "confirmation", str, description="Type your discord server's name to confirm."
    )
    async def auth(self, ctx: ApplicationContext, confirmation: str):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        errors = []
        if not guild_settings:
            errors.append(no_init_err_msg)
        if confirmation != ctx.guild.name:
            errors.append(
                f"`{confirmation}` does not match server name `{ctx.guild.name}`."
            )

        if errors:
            await ctx.respond(
                content=errors[0]
                if len(errors) == 1
                else "\n".join([f"- {e}" for e in errors]),
                ephemeral=True,
            )
            return

        token = secrets.token_urlsafe()
        with Session() as session:
            record_to_update = (
                session.query(GuildSettingsTable).filter_by(id=ctx.guild_id).one()
            )
            record_to_update.auth_token = token
            session.commit()

        await ctx.respond(
            f"Your Foundry VTT token is:\n`{token}`\nDo not share this with anyone.",
            ephemeral=True,
        )

    @admin_group.command(
        name="rollcall",
        description="Configure Weekly Rollcall. Leave all fields blank to disable.",
    )
    @option(
        "channel",
        TextChannel,
        description="Text Channel Roll Call should be posted to.",
    )
    @option("role", Role, description="Role to mention for rollcall.")
    @option("day", choices=[d.name for d in Day], description="Day to post Roll Call.")
    @option("time", choices=hours_list, description="Time to post Roll Call.")
    @bot_has_guild_permissions(mention_everyone=True)
    async def rollcall(
        self,
        ctx: ApplicationContext,
        channel: TextChannel | None = None,
        role: Role | None = None,
        day: str | None = None,
        time: str | None = None,
    ):
        guild_settings = GuildSettingsTable.lookup(ctx.guild_id)
        if not guild_settings:
            await ctx.respond(**logger.err_msg(no_init_err_msg, ctx.guild_id))
            return

        if (
            role.id == ctx.guild_id
            and not channel.permissions_for(ctx.guild.self_role).mention_everyone
        ):
            await ctx.respond(
                **logger.err_msg(
                    f'Oronder does not have "Mention Everyone" permissions in {channel.mention}',
                    ctx.guild_id,
                )
            )
            return

        try:
            with Session() as session:
                guild_settings_table = (
                    session.query(GuildSettingsTable).filter_by(id=ctx.guild_id).one()
                )
                previously_enabled = guild_settings.rollcall_enabled
                if channel:
                    guild_settings_table.rollcall_channel_id = channel.id
                if role:
                    guild_settings_table.rollcall_role_id = role.id
                if day:
                    guild_settings_table.rollcall_day = next(
                        d for d in Day if d.name == day
                    )
                if time:
                    guild_settings_table.rollcall_time = datetime.strptime(
                        time, "%I:%M %p"
                    ).time()

                guild_settings_table.rollcall_enabled = all(
                    [
                        any([channel, role, day, time]),
                        guild_settings_table.rollcall_channel_id,
                        guild_settings_table.rollcall_role_id,
                        guild_settings_table.rollcall_day,
                        guild_settings_table.rollcall_time,
                    ]
                )

                guild_settings = GuildSettings.model_validate(guild_settings_table)
                session.commit()
        except NoResultFound:
            await ctx.respond(no_init_err_msg, ephemeral=True)
            return

        if ctx.guild_id in self.rollcalls:
            self.rollcalls[ctx.guild_id].cancel()
        if guild_settings.rollcall_enabled:
            self.rollcalls[ctx.guild.id] = self.bot.loop.create_task(
                self.sleep_for_rollcall(self.bot, ctx.guild, guild_settings)
            )

        content = (
            "Roll Call disabled."
            if not guild_settings.rollcall_enabled
            else textwrap.dedent(
                f"""Roll Call {"updated" if previously_enabled else "enabled"}.
            Next run at {format_dt(guild_settings.next_rollcall())}"""
            )
        )
        await ctx.respond(content=content, ephemeral=True)

    @staticmethod
    async def sleep_for_rollcall(bot: Bot, guild: Guild, guild_settings: GuildSettings):
        task_key = f"rollcall_task_{guild.id}"
        if hasattr(bot, task_key):
            # Cancel existing task before starting new one
            existing_task = getattr(bot, task_key)
            if not existing_task.done():
                existing_task.cancel()

        setattr(bot, task_key, asyncio.current_task())

        logger.info(f"Roll Call task for {guild.name} starting.")
        try:
            while not bot.is_closed():
                now = datetime.now(pytz.timezone(guild_settings.timezone))
                next_rollcall = guild_settings.next_rollcall()
                time_difference = next_rollcall - now
                sleep_seconds = time_difference.total_seconds()
                if int(sleep_seconds / 60 / 60) > 25:  # if more than 25 hours to go
                    await asyncio.sleep(
                        sleep_seconds % (60 * 60 * 24)
                    )  # sleep for 24 hours
                else:
                    await asyncio.sleep(sleep_seconds)
                    guild_settings = GuildSettingsTable.lookup(
                        guild.id
                    )  # refresh settings before running
                    rollcall_channel = guild.get_channel(
                        guild_settings.rollcall_channel_id
                    )
                    mention = mention_safe(
                        guild.get_role(guild_settings.rollcall_role_id)
                    )
                    msgs = [
                        f"{mention}\n- Sunday",
                        "- Monday",
                        "- Tuesday",
                        "- Wednesday",
                        "- Thursday",
                        "- Friday",
                        "- Saturday",
                    ]
                    for msg in msgs:
                        await rollcall_channel.send(msg)
                        await asyncio.sleep(.6)
        except asyncio.CancelledError:
            logger.info(f"Roll Call task for {guild.name} canceled.")
        finally:
            # Clean up task reference when done
            if hasattr(bot, task_key):
                delattr(bot, task_key)


def setup(bot: Bot):
    logger.critical("Loading")
    bot.add_cog(Admin(bot))
