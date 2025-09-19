from sqlalchemy import BigInteger, String
from sqlalchemy.orm import mapped_column, Mapped

from database import Base, Session
from database.guild_settings_table import GuildSettingsTable


class GameMasterTable(Base):
    __tablename__ = "game_master"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    default_campaign_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    timezone: Mapped[str] = mapped_column(String, nullable=True, default=None)

    @staticmethod
    def lookup(discord_id: int, guild_id: int):
        with Session() as session:
            gm_settings = session.query(GameMasterTable).filter_by(
                id=discord_id, guild_id=guild_id
            ).one_or_none() or GameMasterTable(id=discord_id, guild_id=guild_id)

        if not all(i for i in [gm_settings.timezone]):
            guild_settings = GuildSettingsTable.lookup(guild_id)
            if gm_settings.timezone is None:
                gm_settings.timezone = guild_settings.timezone

        return gm_settings
