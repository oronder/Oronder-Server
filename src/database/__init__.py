import os
import uuid
from datetime import date as dt_date
from typing import List

from sqlalchemy import (
    String,
    BigInteger,
    Date,
    Integer,
    UUID,
    create_engine,
    func,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import (
    sessionmaker,
    mapped_column,
    Mapped,
    DeclarativeBase,
    MappedAsDataclass,
)

from models import DowntimeModel, CampaignModel
from utils import getLogger

logger = getLogger(__name__)


class Base(DeclarativeBase, MappedAsDataclass):
    pass


database_url = f"postgresql://postgres:{os.environ['POSTGRES_PASSWORD']}@{os.getenv('POSTGRES_HOSTNAME','oronder-db')}:5432/postgres"
engine = create_engine(database_url)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class BackBlazeBills(Base):
    __tablename__ = "storage_expenses"
    date: Mapped[dt_date] = mapped_column(Date, primary_key=True, index=True)
    standing: Mapped[str] = mapped_column(String, nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False)


class DowntimeTable(Base):
    __tablename__ = "downtime"
    player_message_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, index=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    player_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    player_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gm_custom_id: Mapped[str] = mapped_column(String, nullable=False)
    gm_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gm_id: Mapped[int] = mapped_column(BigInteger, nullable=True, default=None)

    @staticmethod
    def from_model(downtime_model: DowntimeModel) -> "DowntimeTable":
        return DowntimeTable(**downtime_model.to_dict())


class CampaignTable(Base):
    __tablename__ = "campaign"
    name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    starting_level: Mapped[int] = mapped_column(Integer, nullable=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    session_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    voice_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_ids: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String)), default_factory=list, nullable=False
    )

    @staticmethod
    def from_model(campaign: CampaignModel) -> "CampaignTable":
        return CampaignTable(**campaign.to_dict())


class XpAdjustmentsTable(Base):
    __tablename__ = "xp_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "guild_id", "actor_id", "comment", name="unique_guild_actor_comment"
        ),
    )
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        init=False,
        default_factory=lambda: None,
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[dt_date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )


# class DescriptionsTable(Base):
#     __tablename__ = "descriptions"
#     sha56: Mapped[str] = mapped_column(String, primary_key=True)
#     description: Mapped[str] = mapped_column(String, nullable=False)


class GoldLedger(Base):
    __tablename__ = "gold_ledger"
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    date: Mapped[dt_date] = mapped_column(Date, nullable=False)
    change: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False)


def init_db():
    with Session() as session:
        Base.metadata.create_all(
            session.get_bind().engine
        )
