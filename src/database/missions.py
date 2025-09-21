from datetime import datetime
from typing import Optional, List, Callable

import pytz
from discord import Guild, ScheduledEvent, ScheduledEventStatus, MISSING, HTTPException
from sqlalchemy import BigInteger, String, Integer, DateTime, Boolean, ARRAY
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from database import Base, Session
from models.missions import Mission
from utils import getLogger

logger = getLogger(__name__)


class MissionTable(Base):
    __tablename__ = "missions"
    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        init=False,
        default_factory=lambda: None,
    )
    guild_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    system: Mapped[str] = mapped_column(String, nullable=False)
    min_pc_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_pc_count: Mapped[int] = mapped_column(Integer, nullable=False)
    gm_xp: Mapped[int] = mapped_column(Integer, nullable=False)
    hook: Mapped[str] = mapped_column(String, nullable=False)
    xp: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    gold: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    date_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    gm_id: Mapped[int] = mapped_column(BigInteger, nullable=True, default=None)
    gm_pc: Mapped[str] = mapped_column(String, nullable=True, default=None)
    event_id: Mapped[int] = mapped_column(BigInteger, nullable=True, default=None)
    channel_or_thread_id: Mapped[int] = mapped_column(
        BigInteger, nullable=True, default=None
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=True, default=None)
    channel_override: Mapped[bool] = mapped_column(Boolean, nullable=True, default=None)
    image_url: Mapped[str] = mapped_column(String, nullable=True, default=None)
    campaign_id: Mapped[int] = mapped_column(BigInteger, nullable=True, default=None)
    pcs: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String)), default_factory=list
    )
    pcs_standby: Mapped[List[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(String)), default_factory=list
    )

    @staticmethod
    def from_model(mission_model: Mission) -> "MissionTable":
        mission_table = MissionTable(
            **{k: v for k, v in mission_model.to_dict().items() if k != "id"}
        )
        if mission_model.id:
            mission_table.id = mission_model.id
        return mission_table


async def edit_mission(
    guild: Guild,
    mission: Mission,
    add_event_fun: Callable = (lambda *args, **kwargs: None),
    image_bytes: bytes | None = None,
) -> list[str]:
    """
    use this one unless you're creating a new mission!
    """
    errors = []

    scheduled_event: Optional[ScheduledEvent] = guild.get_scheduled_event(
        mission.event_id
    )
    if (
        scheduled_event
        and scheduled_event.status == ScheduledEventStatus.active
        and scheduled_event.start_time != mission.date_time
    ):
        await scheduled_event.complete()
        scheduled_event = None

    if not scheduled_event:
        if mission.date_time > datetime.now(pytz.UTC):
            scheduled_event, scheduled_event_error = await mission.create_event(
                guild, image_bytes=image_bytes
            )
            if scheduled_event_error:
                errors.append(scheduled_event_error)
    elif scheduled_event.status == ScheduledEventStatus.scheduled:
        if mission.date_time < datetime.now(pytz.UTC):
            await scheduled_event.cancel()
        else:
            await scheduled_event.edit(
                name=mission.title,
                description=mission.event_description(guild),
                start_time=mission.date_time,
                cover=image_bytes if image_bytes else MISSING,
            )
    elif scheduled_event.status == ScheduledEventStatus.active:
        await scheduled_event.edit(
            name=mission.title,
            description=mission.event_description(guild),
            cover=image_bytes if image_bytes else MISSING,
        )

    upset_errors = upsert_mission(guild, mission, add_event_fun)
    errors.extend(upset_errors)

    channel_or_thread = guild.get_channel_or_thread(mission.channel_or_thread_id)
    if channel_or_thread:
        message = channel_or_thread.get_partial_message(mission.message_id)
        if message:
            try:
                await message.edit(content=None, embed=mission.msg_embed(guild))
            except HTTPException as error:
                errors.append(f"Could not write message for {mission.title}.")
                logger.error(f"{error=}")
        else:
            errors.append(f"Could not find message for {mission.title}.")
        if mission.created_thread():
            await channel_or_thread.edit(name=mission.title)
    else:
        errors.append(f"Could not find channel for {mission.title}.")

    return errors


def upsert_mission(
    guild: Guild, mission: Mission, add_event_fun: Callable
) -> list[str]:
    """
    You probably want to use edit_mission unless you're creating a new mission,
    and you probably shouldn't be creating a new mission.
    """
    errors = []
    mission_table = MissionTable.from_model(mission)
    try:
        with Session() as session:
            if mission_table.id:
                session.merge(mission_table)
            else:
                session.add(mission_table)
            session.commit()
            add_event_fun(
                mission_id=mission_table.id,
                event=guild.get_scheduled_event(mission.event_id),
            )
    except Exception as error:
        errors.append("Failed to write to database.")
        logger.error(f"{error=}")

    return errors
