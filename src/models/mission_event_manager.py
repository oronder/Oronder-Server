from asyncio import Task
from typing import Dict

from discord import ScheduledEventStatus, ScheduledEvent
from discord.ext.commands import Bot

from database import Session
from database.missions import MissionTable
from utils import getLogger
from views.scheduling import auto_start_events

logger = getLogger(__name__)


class MissionEventManager:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.dict: Dict[int, Task] = {}
        self.bot.loop.create_task(self.__init_task())

    async def __init_task(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for scheduled_event in guild.scheduled_events:
                if (
                    scheduled_event.status
                    in [ScheduledEventStatus.scheduled, ScheduledEventStatus.active]
                    and scheduled_event.creator_id
                    and int(scheduled_event.creator_id) == self.bot.application_id
                ):
                    with Session() as session:
                        mission_id = (
                            session.query(MissionTable.id)
                            .filter_by(guild_id=guild.id, event_id=scheduled_event.id)
                            .scalar()
                        )
                    if mission_id:
                        self.upsert(mission_id, scheduled_event)

    def upsert(self, mission_id: int, event: ScheduledEvent):
        logger.debug(f"{mission_id=}\n{event=}\n{self.dict=}")
        self.remove(mission_id)
        self.dict[mission_id] = self.bot.loop.create_task(auto_start_events(event))

    def remove(self, mission_id: int):
        if mission_id in self.dict:
            self.dict.pop(mission_id).cancel()
