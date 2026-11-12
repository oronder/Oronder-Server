import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytz
from discord import HTTPException, ScheduledEventStatus

from database import missions


class FakeEvent:
    def __init__(self, start_time):
        self.status = ScheduledEventStatus.scheduled
        self.start_time = start_time
        self.canceled = False
        self.edits = []

    async def cancel(self):
        self.canceled = True

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class FakeGuild:
    def __init__(self, cached=None, fetched=None):
        self.cached, self.fetched = cached, fetched

    def get_scheduled_event(self, event_id):
        return self.cached

    async def fetch_scheduled_event(self, event_id):
        return self.fetched


def mission(date_time, create_event=None):
    return SimpleNamespace(
        event_id=1,
        title="Knock on Wood 2",
        date_time=date_time,
        create_event=create_event,
        event_description=lambda guild: "",
    )


NOW = datetime.now(pytz.UTC)


def test_moving_to_the_past_cancels_an_uncached_event():
    event = FakeEvent(NOW + timedelta(days=270))
    guild = FakeGuild(cached=None, fetched=event)
    asyncio.run(
        missions._sync_event(guild, mission(NOW - timedelta(days=90)), None, [])
    )
    assert event.canceled


def test_future_edit_moves_the_event():
    event = FakeEvent(NOW + timedelta(days=270))
    new_time = NOW + timedelta(days=3)
    asyncio.run(
        missions._sync_event(FakeGuild(cached=event), mission(new_time), None, [])
    )
    assert not event.canceled and event.edits[0]["start_time"] == new_time


def test_discord_refusing_the_event_still_saves_the_session(monkeypatch):
    saved = []

    async def refuse(*args, **kwargs):
        response = SimpleNamespace(status=400, reason="Bad Request")
        raise HTTPException(response, "must be within five years")

    monkeypatch.setattr(
        missions, "upsert_mission", lambda g, m, f: saved.append(m) or []
    )
    guild = FakeGuild()
    guild.get_channel_or_thread = lambda _id: None
    m = mission(NOW + timedelta(days=36500), create_event=refuse)
    m.channel_or_thread_id = 2
    errors = asyncio.run(missions.edit_mission(guild, m))
    assert saved == [m]
    assert any("Could not update the event" in e for e in errors)
