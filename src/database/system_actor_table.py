from sqlalchemy import BigInteger, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.shared_actor import SharedActor


class SystemActorTable(Base):
    """Actors from every game system except dnd5e.

    dnd5e actors live in `actors`, one column per dnd5e field, because their
    payload is dnd5e's own roll data. Other systems send the Oronder-defined
    shape in models.shared_actor, so they share one table: the common section
    as columns, the per-system part as a single `body` document.

    New, and purely additive -- created by create_all at startup, with no
    change to `actors`.
    """

    __tablename__ = "system_actors"
    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, index=True, nullable=False
    )
    game_system: Mapped[str] = mapped_column(String, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String, index=True, nullable=False)
    portrait_url: Mapped[str] = mapped_column(String, nullable=True, default=None)
    world: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    hp: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    skills: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    currency: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    body: Mapped[dict] = mapped_column(JSONB, nullable=True, default=None)
    discord_ids: Mapped[list[int]] = mapped_column(
        MutableList.as_mutable(ARRAY(BigInteger)), nullable=False, default_factory=list
    )

    @staticmethod
    def from_model(actor: SharedActor, guild_id: int) -> "SystemActorTable":
        data = actor.model_dump(mode="json")
        return SystemActorTable(
            id=data["id"],
            guild_id=guild_id,
            game_system=data["game_system"],
            name=data["name"],
            portrait_url=data["portrait_url"],
            world=data["world"],
            hp=data["hp"],
            skills=data["skills"],
            currency=data["currency"],
            body=data["body"],
            discord_ids=data["discord_ids"],
        )
