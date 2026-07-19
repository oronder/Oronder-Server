import os
import uuid
from datetime import date as dt_date
from pathlib import Path
from typing import List

from sqlalchemy import (
    String,
    BigInteger,
    Date,
    Integer,
    MetaData,
    UUID,
    create_engine,
    func,
    inspect,
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
    # Deterministic constraint names so future Alembic migrations can refer
    # to them.  Verified to produce zero autogenerate diffs against a
    # database created by the historical convention-less create_all():
    # "ix" matches SQLAlchemy's default, the only unique constraint has an
    # explicit name, there are no FK/CK constraints, and Alembic does not
    # diff primary-key constraint names.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _database_url() -> str:
    """DATABASE_URL wins; otherwise compose one from POSTGRES_* parts with
    localhost defaults so a stock `postgres` install works out of the box."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOSTNAME", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "oronder")
    credentials = f"{user}:{password}" if password else user
    return f"postgresql://{credentials}@{host}:{port}/{db}"


database_url = _database_url()
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


def _alembic_config(url: str):
    """Alembic Config built in code (no alembic.ini, no subprocess).

    The script location is resolved relative to this package file so it works
    from any CWD: ``<repo>/src/database`` has the scripts at
    ``<repo>/alembic``, the container's ``/app/database`` at ``/app/alembic``.
    """
    from alembic.config import Config

    package_dir = Path(__file__).resolve().parent
    for root in (package_dir.parent.parent, package_dir.parent):
        script_location = root / "alembic"
        if (script_location / "env.py").is_file():
            break
    else:
        raise RuntimeError(f"No alembic directory found relative to {package_dir}")

    config = Config()
    config.set_main_option("script_location", str(script_location))
    config.attributes["database_url"] = url
    return config


def init_db(bind=None):
    """Create or upgrade the schema. Idempotent; runs in the app lifespan.

    - Empty database (no tables): create_all, then stamp the alembic head.
    - Tables but no alembic_version (deployment that predates alembic):
      stamp head without touching the existing schema or data.
    - alembic_version present: run any pending migrations up to head.
    """
    from alembic import command

    db_engine = bind if bind is not None else engine
    config = _alembic_config(db_engine.url.render_as_string(hide_password=False))

    with db_engine.connect() as connection:
        config.attributes["connection"] = connection
        table_names = inspect(connection).get_table_names()

        if "alembic_version" in table_names:
            command.upgrade(config, "head")
        elif table_names:
            logger.warning("Existing schema without alembic_version: stamping head")
            command.stamp(config, "head")
        else:
            logger.warning("Empty database: creating schema and stamping head")
            Base.metadata.create_all(connection)
            command.stamp(config, "head")

        connection.commit()
