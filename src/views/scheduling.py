import asyncio
from datetime import datetime

import pytz
from discord import ScheduledEvent, ScheduledEventStatus

from utils import getLogger

logger = getLogger(__name__)


async def auto_start_events(event: ScheduledEvent):
    try:
        logger.debug(f"Starting autostart for event {event.name}")
        if event.status == ScheduledEventStatus.scheduled:
            now = datetime.now(pytz.UTC)
            await asyncio.sleep((event.start_time - now).total_seconds())
            event = event.guild.get_scheduled_event(event.id)
            if event.status in [
                ScheduledEventStatus.completed,
                ScheduledEventStatus.canceled,
            ]:
                return
            else:
                if event.status == ScheduledEventStatus.active:
                    logger.warning(f"Event {event.name} already running!")
                else:
                    logger.info(f"Starting Scheduled Event {event.name}.")
                    await event.start()
    except asyncio.CancelledError:
        logger.info(f"Event {event.name} canceled.")
