from datetime import datetime
from typing import Optional

from discord.utils import snowflake_time

from models.base_model import OronderBaseModel


class DowntimeModel(OronderBaseModel):
    player_message_id: int
    player_channel_id: int
    player_id: int
    gm_custom_id: str
    gm_channel_id: int
    gm_id: Optional[int] = None
    guild_id: int

    def datetime(self) -> datetime:
        return snowflake_time(self.player_message_id)


class GameMasterModel(OronderBaseModel):
    id: int
    guild_id: int
    default_campaign_id: int
    timezone: str


class CampaignModel(OronderBaseModel):
    id: int
    name: str
    starting_level: int
    guild_id: int
    actor_ids: list[str]
    session_channel_id: int
    voice_channel_id: int
