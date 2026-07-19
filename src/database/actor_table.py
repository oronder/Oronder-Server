from typing import List

from sqlalchemy import String, BigInteger
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.base_model import OronderBaseModel


class ActorTable(Base):
    __tablename__ = "actors"
    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, index=True, nullable=False
    )
    weapons: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    portrait_url: Mapped[str] = mapped_column(String, nullable=True, default=None)
    currency: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSONB), nullable=True, default=None
    )
    abilities: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    bonuses: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    skills: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    tools: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    details: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    traits: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    classes: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    world: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    discord_ids: Mapped[List[int]] = mapped_column(
        MutableList.as_mutable(ARRAY(BigInteger)), nullable=False, default_factory=list
    )
    equipment: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String)), default_factory=list
    )

    @staticmethod
    def from_model(actor_model: OronderBaseModel, guild_id: int) -> "ActorTable":
        """Accepts any system's actor model; to_dict() keys must be a subset
        of this table's columns (per-system payloads nest inside the shared
        JSONB columns, see SYSTEMS.md)."""
        return ActorTable(**actor_model.to_dict(), guild_id=guild_id)
