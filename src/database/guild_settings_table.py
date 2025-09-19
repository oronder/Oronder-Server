from datetime import time
from typing import Optional

from discord import Bot, Guild
from sqlalchemy import BigInteger, Enum, String, Time, Integer, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import mapped_column, Mapped

from database import Session, Base
from models.guild_settings import Subscription, GuildSettings, Day, current_subscription
from utils import getLogger

logger = getLogger(__name__)


# Some example adds
# ALTER TABLE guild_settings ADD COLUMN roll_discord_to_foundry BOOLEAN DEFAULT FALSE
# ALTER TABLE guild_settings ADD COLUMN pending_xp JSONB
# ALTER TABLE guild_settings ADD COLUMN last_indexed_message_id bigint
class GuildSettingsTable(Base):
    __tablename__ = "guild_settings"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    gm_role_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gm_xp: Mapped[int] = mapped_column(Integer, nullable=False)
    session_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    downtime_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scheduling_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voice_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subscription: Mapped[Subscription] = mapped_column(
        Enum(Subscription), nullable=False
    )
    foundry_hostname: Mapped[str] = mapped_column(String, nullable=False)
    auth_token: Mapped[str] = mapped_column(String, nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    starting_level: Mapped[int] = mapped_column(Integer, nullable=False)
    combat_channel_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    downtime_gm_channel_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    rollcall_channel_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    rollcall_role_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    rollcall_day: Mapped[Day] = mapped_column(Enum(Day), nullable=True, default=None)
    rollcall_time: Mapped[time] = mapped_column(Time, nullable=True, default=None)
    pending_xp: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    last_indexed_message_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    roll_discord_to_foundry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    rollcall_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    @staticmethod
    def from_model(guild_settings: GuildSettings) -> "GuildSettingsTable":
        return GuildSettingsTable(**guild_settings.to_dict())

    @staticmethod
    def commit(guild_settings: GuildSettings):
        with Session() as session:
            guild_settings_table = GuildSettingsTable.from_model(guild_settings)
            session.merge(guild_settings_table)
            session.commit()

    @staticmethod
    def lookup(guild_id: int) -> Optional[GuildSettings]:
        with Session() as session:
            res = session.query(GuildSettingsTable).filter_by(id=guild_id).one_or_none()
            return GuildSettings.model_validate(res) if res else None

    @staticmethod
    def update_subscription(bot: Bot, guild: Guild):
        guild_settings = GuildSettingsTable.lookup(guild.id)
        subscription = current_subscription(bot, guild)
        if subscription != guild_settings.subscription:
            logger.warning(
                f"{guild.name}: {guild_settings.subscription.name} -> {subscription.name}"
            )
            guild_settings.subscription = subscription
            with Session() as session:
                session.merge(GuildSettingsTable.from_model(guild_settings))
                session.commit()
