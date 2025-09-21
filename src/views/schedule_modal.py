from datetime import datetime, timedelta
from urllib.parse import urljoin

import pytz
from discord import (
    Thread,
    TextChannel,
    Member,
    InputTextStyle,
    Interaction,
    ForumChannel,
    Forbidden,
)
from discord.ui import Modal, InputText
from discord.utils import format_dt
from sqlalchemy import select, and_, any_

from database import Session
from database.actor_table import ActorTable
from database.game_master_table import GameMasterTable
from database.missions import MissionTable, upsert_mission
from models import CampaignModel
from models.guild_settings import GuildSettings
from models.mission_event_manager import MissionEventManager
from models.missions import Mission
from utils import (
    format_time,
    OronderLogger,
    parse_time,
    get_image_bytes,
    check_permissions,
    getLogger,
)

logger = getLogger(__name__)


class ScheduleModal(Modal):
    def __init__(
        self,
        mission: Mission,
        guild_settings: GuildSettings,
        mission_event_manager: MissionEventManager,
        channel_override: Thread | TextChannel,
        gm_pc_name: str,
        initiating_user: Member,
        campaign: CampaignModel,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.gm_settings = GameMasterTable.lookup(initiating_user.id, guild_settings.id)
        self.now = datetime.now(pytz.timezone(self.gm_settings.timezone))
        self.mission = mission
        self.guild_settings = guild_settings
        self.mission_event_manager = mission_event_manager
        self.channel_override = channel_override
        self.initiating_user = initiating_user
        self.campaign = campaign

        eight_pm_two_days_from_now = (self.now + timedelta(days=2)).replace(
            hour=20, minute=0, second=0
        )

        self.add_item(
            InputText(
                label="Title",
                row=0,
                min_length=1,
                max_length=50,
                placeholder="Super Mario Bros",
            )
        )
        self.add_item(
            InputText(
                label="Datetime",
                row=1,
                min_length=1,
                max_length=50,
                value=format_time(eight_pm_two_days_from_now),
            )
        )
        self.add_item(
            InputText(
                label="Hook",
                row=2,
                min_length=1,
                max_length=1024,
                placeholder="Rescue the Princess",
                style=InputTextStyle.long,
            )
        )
        self.add_item(
            InputText(
                label="Image URL",
                row=3,
                max_length=1024,
                required=False,
                placeholder=urljoin(self.guild_settings.foundry_hostname, "image.jpg"),
            )
        )
        if gm_pc_name:
            self.add_item(
                InputText(
                    label="GM PC",
                    row=4,
                    max_length=50,
                    required=False,
                    value=gm_pc_name,
                    placeholder="Toad",
                )
            )

    async def handle_error(
        self,
        msg: str,
        interaction: Interaction | None = None,
        logger: OronderLogger = logger,
    ):
        msg = f"Error scheduling **{self.mission.title}**!\n\n{msg}"
        if interaction and interaction.response:
            await interaction.response.send_message(content=msg, ephemeral=True)
            p = interaction.permissions
            missing_perms = {
                a: p.__getattribute__(a)
                for a in p.__dir__()
                if isinstance(p.__getattribute__(a), bool) and not p.__getattribute__(a)
            }
            logger.warning(
                f"\n  {interaction.guild.name=}\n  {interaction.guild.id=}\n  {missing_perms=}"
            )
        else:
            await self.initiating_user.send(msg, delete_after=30)

    async def callback(self, interaction: Interaction):
        if not interaction:
            await self.handle_error("Lost Interaction. Timeout?")
            return

        self.mission.title = self.children[0].value
        self.mission.hook = self.children[2].value
        date_time, time_error = parse_time(
            self.children[1].value, self.gm_settings.timezone
        )

        if time_error:
            await self.handle_error(time_error, interaction)
            return
        if self.now > date_time:
            await self.handle_error(
                f"Date time {format_dt(date_time)} is in the past.", interaction
            )
            return

        self.mission.date_time = date_time.astimezone(pytz.UTC)
        if datetime.now(pytz.UTC) + timedelta(seconds=5) > self.mission.date_time:
            self.mission.date_time = date_time.astimezone(pytz.UTC) + timedelta(
                seconds=5
            )

        stmt = select(MissionTable).where(
            and_(
                MissionTable.guild_id == self.mission.guild_id,
                MissionTable.title == self.mission.title,
            )
        )

        with Session() as session:
            if len(session.scalars(stmt).all()):
                await self.handle_error("Session already exists.", interaction)
                return

        self.mission.image_url = self.children[3].value
        image_bytes = get_image_bytes(self.mission.image_url)
        if self.mission.image_url and not image_bytes:
            await self.handle_error(
                f"{self.mission.image_url} is not a valid image url.", interaction
            )
            return

        if len(self.children) == 5 and self.children[4].value:
            stmt = select(ActorTable.id).where(
                and_(
                    ActorTable.guild_id == self.guild_settings.id,
                    interaction.user.id == any_(ActorTable.discord_ids),
                    ActorTable.name.icontains(self.children[4].value),
                )
            )

            with Session() as session:
                gm_pc_id = session.scalars(stmt).first()
                if not gm_pc_id:
                    await self.handle_error(
                        f"GM PC {self.children[4].value} not found.", interaction
                    )
                    return
        else:
            gm_pc_id = None

        self.mission.gm_pc = gm_pc_id
        self.mission.gm_xp = self.guild_settings.gm_xp

        if self.campaign:
            self.mission.pcs = self.campaign.actor_ids
            forum_or_channel = interaction.guild.get_channel(
                self.campaign.session_channel_id
            )
        else:
            forum_or_channel = interaction.guild.get_channel(
                self.guild_settings.session_channel_id
            )

        embed = self.mission.msg_embed(interaction.guild)

        if not self.channel_override and isinstance(forum_or_channel, ForumChannel):
            missing_permissions = check_permissions(
                forum_or_channel, interaction.guild.self_role
            )
            if missing_permissions:
                await self.handle_error(
                    f"{missing_permissions} required for {forum_or_channel.mention}. Check channel permissions!",
                    interaction,
                )
                return
            embed.title = (
                None  # can't rely on msg_embed because mission hasn't been fully built
            )
            try:
                channel_or_thread = await forum_or_channel.create_thread(
                    name=self.mission.title,
                    embed=embed,
                )
                message = channel_or_thread.get_partial_message(channel_or_thread.id)
            except Forbidden:
                await self.handle_error(
                    f"Insufficient permissions for {forum_or_channel.mention}. Check channel permissions!",
                    interaction,
                )
                return
        else:
            embed.title = (
                self.mission.title
            )  # can't rely on msg_embed because mission hasn't been fully built
            channel_or_thread = self.channel_override or forum_or_channel
            missing_permissions = check_permissions(
                channel_or_thread, interaction.guild.self_role
            )
            if missing_permissions:
                await self.handle_error(
                    f"{missing_permissions} required for {channel_or_thread.mention}. Check channel permissions!",
                    interaction,
                )
                return
            try:
                message = await channel_or_thread.send(embed=embed)
            except Forbidden:
                await self.handle_error(
                    f"Insufficient permissions for {channel_or_thread.mention}. Check channel permissions!",
                    interaction,
                )
                return

        self.mission.message_id = message.id
        self.mission.channel_or_thread_id = channel_or_thread.id

        scheduled_event, scheduled_event_error = await self.mission.create_event(
            interaction.guild, self.campaign, image_bytes
        )

        if scheduled_event_error:
            await self.handle_error(scheduled_event_error, interaction)
            return

        create_mission_errors = upsert_mission(
            interaction.guild,
            self.mission,
            add_event_fun=self.mission_event_manager.upsert,
        )
        if create_mission_errors:
            await self.handle_error("\n".join(create_mission_errors), interaction)
            return

        msg = f"**{self.mission.title}** successfully created.\n{message.jump_url}"
        if interaction.response:
            await interaction.response.send_message(content=msg, ephemeral=True)
        else:
            logger.error(f"response not found. {interaction=}")
            await interaction.user.send(msg)

        if (
            self.guild_settings.scheduling_channel_id
            and self.guild_settings.scheduling_channel_id != channel_or_thread.id
        ):
            await interaction.guild.get_channel(self.guild_settings.scheduling_channel_id) \
                .send(content=message.jump_url, embed=self.mission.msg_embed(interaction.guild, static=True))
