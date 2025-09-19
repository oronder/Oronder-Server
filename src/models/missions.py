from datetime import datetime
from typing import Optional, List, Tuple

import pytz
from discord import (
    Embed,
    Guild,
    ScheduledEvent,
    MISSING,
    Forbidden,
    VoiceChannel,
    StageChannel,
)
from discord.utils import format_dt
from jinja2 import Environment, Undefined
from pydantic import field_validator, AwareDatetime

from database import Session, CampaignTable
from database.actor_table import ActorTable
from database.guild_settings_table import GuildSettingsTable
from dnd.items import format_number
from models import CampaignModel
from models.actor import Actor
from models.base_model import OronderBaseModel
from models.systems import System
from utils import mention_safe, get_image_bytes, getLogger, check_permissions

logger = getLogger(__name__)


class Mission(OronderBaseModel):
    guild_id: int
    title: str = None
    id: Optional[int] = None
    system: str = System.dnd5e.value
    xp: Optional[int] = None
    gold: Optional[int] = None
    min_pc_count: int = 1
    max_pc_count: int = 23
    date_time: AwareDatetime = None
    gm_id: Optional[int] = None
    gm_pc: Optional[str] = None
    gm_xp: int = 0
    pcs: list[str] = []
    pcs_standby: list[str] = []
    hook: Optional[str] = None
    event_id: Optional[int] = None
    channel_or_thread_id: Optional[int] = None
    message_id: Optional[int] = None
    channel_override: bool = False
    image_url: Optional[str] = None
    campaign_id: Optional[int] = None

    @field_validator("date_time", mode="before")
    @classmethod
    def set_utc_timezone(cls, date_time: datetime) -> datetime:
        return date_time.astimezone(pytz.UTC)

    def created_thread(self):
        return (
            not self.channel_override and self.channel_or_thread_id == self.message_id
        )

    def get_actors(self, standby=False) -> List[Actor]:
        pcs = self.pcs_standby if standby else self.pcs
        if not pcs:
            return []
        try:
            with Session() as session:
                actors_standby = [
                    Actor.model_validate(a)
                    for a in session.query(ActorTable)
                    .filter_by(guild_id=self.guild_id)
                    .filter(ActorTable.id.in_(pcs))
                    .all()
                ]
            return actors_standby
        except Exception as e:
            logger.error(str(e), stack_info=True)
            return []

    def title_link(self, bot):
        channel_or_thread = bot.get_guild(self.guild_id).get_channel_or_thread(
            self.channel_or_thread_id
        )
        if not channel_or_thread:
            return self.title
        elif self.created_thread():
            return channel_or_thread.jump_url
        else:
            return f"{self.title}\n{channel_or_thread.get_partial_message(self.message_id).jump_url}"

    def pc_count(self):
        return len(self.pcs)

    def msg_embed(self, guild: Guild, title=None, static=False):
        assert self.guild_id == guild.id
        if title is None and not self.created_thread() and self.channel_or_thread_id:
            title = self.title
        pc_name, pc_value = (
            ("Max Players", str(self.max_pc_count))
            if static
            else ("Player Count", f"{len(self.pcs)}/{self.max_pc_count}")
        )

        embed = (
            Embed(title=title)
            .add_field(name="Hook", value=self.render_hook())
            .add_field(name="GM", value=mention_safe(guild.get_member(self.gm_id)))
            .add_field(name=pc_name, value=pc_value)
            .add_field(name="Date", value=format_dt(self.date_time))
        )
        if self.image_url:
            embed.set_image(url=self.image_url)
            # embed.set_thumbnail(url=self.image_url)

        if static:
            return embed
        pc_strings = []
        level_sum = 0
        for pc in self.get_actors():
            pc_strings.append(f"- {pc.name} | {pc.desc_string()}")
            level_sum += pc.details.level
        for pc in self.get_actors(standby=True):
            pc_strings.append(f"- *{pc.name} | {pc.desc_string()}*")
        if len(pc_strings):
            embed.add_field(name="Characters", value="\n".join(pc_strings))
            embed.add_field(
                name="Average Level", value=f"{level_sum / len(self.pcs):.1f}"
            )

        if self.xp:
            embed.add_field(name="Experience", value=format_number(self.xp, "XP"))

        if self.gold:
            embed.add_field(name="Gold", value=format_number(self.gold))

        return embed

    def get_campaign(self) -> Optional[CampaignModel]:
        if not self.campaign_id:
            return None

        with Session() as session:
            return CampaignModel.model_validate(
                session.query(CampaignTable)
                .filter_by(id=self.campaign_id, guild_id=self.guild_id)
                .one()
            )

    async def create_event(
        self,
        guild: Guild,
        campaign: CampaignModel | None = None,
        image_bytes: bytes | None = None,
    ) -> Tuple[ScheduledEvent, str]:
        if not image_bytes:
            image_bytes = get_image_bytes(self.image_url)
            if self.image_url and not image_bytes:
                return None, f"{self.image_url} is not a valid image url."

        if not campaign:
            campaign = self.get_campaign()

        if campaign:
            voice_channel_id = campaign.voice_channel_id
        else:
            guild_settings = GuildSettingsTable.lookup(guild.id)
            voice_channel_id = guild_settings.voice_channel_id

        location: VoiceChannel | StageChannel | str = guild.get_channel(
            voice_channel_id
        )
        try:
            scheduled_event = await guild.create_scheduled_event(
                name=self.title,
                description=self.event_description(guild),
                start_time=self.date_time,
                location=location,
                image=image_bytes if image_bytes else MISSING,
            )
            self.event_id = scheduled_event.id
            return scheduled_event, None
        except Forbidden as f:
            missing_perms = check_permissions(location, guild.self_role)
            logger.warning(
                f"{guild.name=}\n{guild.self_role.permissions=}\n{missing_perms}"
            )
            return None, missing_perms or str(f)

    def render_hook(self):
        valid_props = {
            k: v
            for (k, v) in self.__dict__.items()
            if "id" not in k and k not in ["a1", "channel_override"]
        }
        env = Environment(undefined=Undefined)
        template = env.from_string(self.hook)
        rendered_template = template.render(**valid_props, pc_count=self.pc_count)
        return rendered_template

    def event_description(self, guild: Guild):
        assert self.guild_id == guild.id
        hook = self.render_hook()
        description_str = [hook, ""]

        if self.created_thread():
            thread = guild.get_thread(self.channel_or_thread_id)
            description_str.append(thread.mention)
        else:
            message = guild.get_channel_or_thread(
                self.channel_or_thread_id
            ).get_partial_message(self.message_id)
            description_str.append(message.jump_url)

        description_str.extend(
            [
                "",
                f"**GM**: {mention_safe(guild.get_member(self.gm_id))}",
                f"**Players**: {len(self.pcs)}/{self.max_pc_count}",
            ]
        )

        for pc in self.get_actors():
            description_str.append(pc.name)
        for pc in self.get_actors(standby=True):
            description_str.append(f"*{pc.name}*")

        out = "\n".join(description_str)

        if len(out) > 1000:
            overage = 1000 - len(out) - 3
            out = f"{out[:len(hook) - overage]}...{out[len(hook):]}"
            if len(out) > 1000:
                logger.warning('YOU FUCKED UP')
                out = out[:1000]
        return out
