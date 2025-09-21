import sys
from datetime import time, datetime, timedelta
from enum import Enum
from typing import Optional, Dict, List, Annotated, Literal

import pytz
from discord import Bot, Guild, TextChannel, ForumChannel, VoiceChannel, StageChannel
from pydantic import Field
from pydantic.functional_validators import BeforeValidator, model_validator

import utils
from models.base_model import OronderBaseModel
from utils import (
    oronder_server_id,
    supporter_role_id,
    chris_discord_id,
    tomp_discord_id,
    getLogger,
    johan_discord_id,
    kabs_discord_id,
    gander7_discord_id,
    oronder_bot_test,
    oronder_bot_dev,
    beta_tester_role_id,
    check_permissions,
)

logger = getLogger(__name__)


class Subscription(Enum):
    none = 0
    supporter = 1
    exempt = 2


class Day(Enum):
    Monday = 0
    Tuesday = 1
    Wednesday = 2
    Thursday = 3
    Friday = 4
    Saturday = 5
    Sunday = 6


def check_snowflake(_id: int | str) -> str:
    if isinstance(_id, str):
        assert _id.isnumeric(), "Must be an integer"
        _id = int(_id)
    assert 0 <= _id <= sys.maxsize, f"Must be between 0 and {sys.maxsize}"
    return str(_id)


SnowflakeStr = Annotated[str, BeforeValidator(check_snowflake)]


class IdNamePair(OronderBaseModel):
    id: SnowflakeStr
    name: str = Field(min_length=1)


def get_id_name(x) -> List[IdNamePair]:
    return [IdNamePair(id=i.id, name=i.name) for i in x]


class GuildSettingsInterface(OronderBaseModel):
    name: Optional[str] = None
    id: Optional[SnowflakeStr] = None
    text_channels: Optional[List[IdNamePair]] = None
    voice_channels: Optional[List[IdNamePair]] = None
    stage_channels: Optional[List[IdNamePair]] = None
    forum_channels: Optional[List[IdNamePair]] = None
    roles: Optional[List[IdNamePair]] = None
    members: Optional[List[IdNamePair]] = None
    gm_role_id: SnowflakeStr
    gm_xp: int = Field(ge=0)
    session_channel_id: SnowflakeStr
    downtime_channel_id: SnowflakeStr
    downtime_gm_channel_id: Optional[SnowflakeStr] = None
    voice_channel_id: SnowflakeStr
    combat_channel_id: Optional[SnowflakeStr] = None
    roll_discord_to_foundry: bool = True
    scheduling_channel_id: SnowflakeStr
    subscription: Optional[str] = None
    timezone: Literal[*utils.timezones]
    starting_level: int = Field(ge=1, le=20)
    rollcall_enabled: bool = False
    rollcall_channel_id: Optional[SnowflakeStr] = None
    rollcall_role_id: Optional[SnowflakeStr] = None
    rollcall_day: Optional[Literal[*[d.name for d in Day]]] = None
    rollcall_time: Optional[str] = None

    @model_validator(mode="after")
    def check_rollcall(self) -> "GuildSettingsInterface":
        if self.rollcall_enabled:
            assert self.rollcall_channel_id, (
                "Channel Id is required when rollcall is enabled."
            )
            assert self.rollcall_role_id, (
                "Role id is required when rollcall is enabled."
            )
            assert self.rollcall_day, "Day is required when rollcall is enabled."
            assert self.rollcall_time, "Time is required when rollcall is enabled."
        return self


class GuildSettings(OronderBaseModel):
    id: int
    gm_role_id: int
    gm_xp: int
    session_channel_id: int
    downtime_channel_id: int
    downtime_gm_channel_id: Optional[int] = None
    voice_channel_id: int
    scheduling_channel_id: int
    combat_channel_id: Optional[int] = None
    roll_discord_to_foundry: bool = True
    subscription: Subscription
    foundry_hostname: str
    auth_token: str
    timezone: str
    starting_level: int
    rollcall_enabled: bool = False
    rollcall_channel_id: Optional[int] = None
    rollcall_role_id: Optional[int] = None
    rollcall_day: Optional[Day] = None
    rollcall_time: Optional[time] = None
    pending_xp: Optional[Dict[str, int]] = None
    last_indexed_message_id: Optional[int] = None

    def next_rollcall(self) -> datetime | None:
        if not self.rollcall_enabled:
            return None

        now = datetime.now(pytz.timezone(self.timezone))
        next_run = now.replace(
            hour=self.rollcall_time.hour, minute=self.rollcall_time.minute
        )
        days_til_next_run = (self.rollcall_day.value - now.weekday()) % 7
        if days_til_next_run == 0 and now.time() > self.rollcall_time:
            next_run += timedelta(weeks=1)
        else:
            next_run += timedelta(days=days_til_next_run)
        return next_run

    def enqueue_xp(self, actor_id_to_xp: dict):
        if self.pending_xp:
            for actor_id, xp in actor_id_to_xp:
                if xp > self.pending_xp[actor_id]:
                    self.pending_xp[actor_id] = xp
        else:
            self.pending_xp = actor_id_to_xp

    def validate_channels(self, guild: Guild, external=False) -> list[str]:
        errs = []

        def err_str(
            title: str,
            required_perm: str,
            channel: TextChannel | ForumChannel | VoiceChannel | StageChannel,
        ):
            permission = f"permission{'s' if ' and ' in required_perm else ''}"
            if external:
                return f"Error in {title}: Oronder requires {required_perm} {permission} in #{channel.name}"
            else:
                return f"`{title.lower().replace(' ', '_')}`: {required_perm} {permission} required for {channel.mention}"

        scheduling_channel = guild.get_channel(self.scheduling_channel_id)
        missing_permissions = check_permissions(
            scheduling_channel,
            guild.self_role,
            requires_mention=True,
            external=external,
        )
        if missing_permissions:
            errs.append(
                err_str("Scheduling Channel", missing_permissions, scheduling_channel)
            )

        if self.scheduling_channel_id != self.session_channel_id:
            session_channel = guild.get_channel(self.session_channel_id)
            missing_permissions = check_permissions(
                session_channel, guild.self_role, external=external
            )
            if missing_permissions:
                errs.append(
                    err_str("Session Channel", missing_permissions, session_channel)
                )

        voice_channel = guild.get_channel(self.voice_channel_id)
        missing_permissions = check_permissions(
            voice_channel, guild.self_role, external=external
        )
        if missing_permissions:
            errs.append(err_str("Voice Channel", missing_permissions, voice_channel))

        if self.downtime_channel_id != self.scheduling_channel_id:
            downtime_channel = guild.get_channel(self.downtime_channel_id)
            missing_permissions = check_permissions(
                downtime_channel, guild.self_role, external=external
            )
            if missing_permissions:
                errs.append(
                    err_str("Downtime Channel", missing_permissions, downtime_channel)
                )

        if self.downtime_gm_channel_id:
            downtime_gm_channel = guild.get_channel(self.downtime_gm_channel_id)
            missing_permissions = check_permissions(
                downtime_gm_channel, guild.self_role, external=external
            )
            if missing_permissions:
                errs.append(
                    err_str(
                        "Downtime GM Channel", missing_permissions, downtime_gm_channel
                    )
                )

        return errs

    def to_interface(self, guild: Guild) -> GuildSettingsInterface:
        assert guild.id == self.id

        return GuildSettingsInterface(
            name=guild.name,
            id=guild.id,
            text_channels=get_id_name(guild.text_channels),
            voice_channels=get_id_name(guild.voice_channels),
            stage_channels=get_id_name(guild.stage_channels),
            forum_channels=get_id_name(guild.forum_channels),
            roles=get_id_name(guild.roles),
            members=[
                {"id": str(m.id), "name": m.display_name}
                for m in guild.members
                if not m.bot
            ],
            gm_role_id=str(self.gm_role_id),
            gm_xp=self.gm_xp,
            session_channel_id=self.session_channel_id,
            downtime_channel_id=self.downtime_channel_id,
            downtime_gm_channel_id=self.downtime_gm_channel_id,
            voice_channel_id=self.voice_channel_id,
            scheduling_channel_id=self.scheduling_channel_id,
            combat_channel_id=self.combat_channel_id,
            roll_discord_to_foundry=self.roll_discord_to_foundry,
            subscription=self.subscription.name,
            timezone=self.timezone,
            starting_level=self.starting_level,
            rollcall_enabled=self.rollcall_enabled,
            rollcall_channel_id=self.rollcall_channel_id,
            rollcall_role_id=self.rollcall_role_id,
            rollcall_day=self.rollcall_day.name if self.rollcall_day else None,
            rollcall_time=":".join(str(self.rollcall_time).split(":")[:2])
            if self.rollcall_time
            else None,
        )

    def from_interface(self, gsi: GuildSettingsInterface):
        self.gm_role_id = int(gsi.gm_role_id)
        self.gm_xp = gsi.gm_xp
        self.session_channel_id = int(gsi.session_channel_id)
        self.downtime_channel_id = int(gsi.downtime_channel_id)
        self.downtime_gm_channel_id = (
            int(gsi.downtime_gm_channel_id) if gsi.downtime_gm_channel_id else None
        )
        self.voice_channel_id = int(gsi.voice_channel_id)
        self.scheduling_channel_id = int(gsi.scheduling_channel_id)
        self.combat_channel_id = (
            int(gsi.combat_channel_id) if gsi.combat_channel_id else None
        )
        self.roll_discord_to_foundry = gsi.roll_discord_to_foundry
        self.timezone = gsi.timezone
        self.starting_level = gsi.starting_level
        self.rollcall_enabled = gsi.rollcall_enabled
        self.rollcall_channel_id = (
            int(gsi.rollcall_channel_id) if gsi.rollcall_channel_id else None
        )
        self.rollcall_role_id = (
            int(gsi.rollcall_role_id) if gsi.rollcall_role_id else None
        )
        self.rollcall_day = (
            next(day for day in Day if day.name == gsi.rollcall_day)
            if gsi.rollcall_day
            else None
        )
        self.rollcall_time = (
            (
                datetime.strptime(
                    ":".join(gsi.rollcall_time.split(":")[:2]), "%H:%M"
                ).time()
            )
            if gsi.rollcall_time
            else None
        )
        return self


def current_subscription(bot: Bot, guild: Guild):
    out = Subscription.none

    if bot.application_id in [oronder_bot_test, oronder_bot_dev]:
        out = Subscription.exempt
    if guild.owner_id in [
        chris_discord_id,
        tomp_discord_id,
        johan_discord_id,
        kabs_discord_id,
        gander7_discord_id,
    ]:
        out = Subscription.exempt
    elif (
        guild.owner_id
        in bot.get_guild(oronder_server_id).get_role(beta_tester_role_id).members
    ):
        out = Subscription.exempt
    elif guild.owner_id in bot.get_guild(oronder_server_id).get_role(supporter_role_id).members:
        out = Subscription.supporter

    logger.info(f'SUBSCRIPTION: {guild.id} = {out}')
    return Subscription.exempt
