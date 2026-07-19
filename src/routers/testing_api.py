"""Test-harness REST surface.

Only mounted when fake-Discord mode is active (see fake_discord.enabled).
Exposes what a Discord user would normally do via slash commands, plus
introspection of everything the bot "sent" to Discord, so tests and the
Foundry e2e harness can drive full round-trips without Discord.
"""

import asyncio
import time
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

import discord_client
from groups.top_level import roll, roll_attack
from utils import getLogger

logger = getLogger(__name__)

router = APIRouter(prefix="/testing")


class FakeApplicationContext:
    """Duck-typed discord.ApplicationContext for driving command handlers."""

    def __init__(
        self,
        guild_id: int,
        user_id: int,
        command_name: str = "test",
        channel_id: Optional[int] = None,
    ):
        bot = discord_client.bot
        self.bot = bot
        self.guild_id = guild_id
        self.guild = bot.get_guild(guild_id)
        self.user = (self.guild and self.guild.get_member(user_id)) or SimpleNamespace(
            id=user_id, display_name=str(user_id)
        )
        self.interaction = SimpleNamespace(user=self.user)
        self.command = SimpleNamespace(name=command_name)
        self.channel_id = channel_id
        self.channel = (
            self.guild.get_channel(channel_id) if self.guild and channel_id else None
        )
        self.deferred = False
        self.responses: list[dict] = []

    async def defer(self, **_):
        self.deferred = True

    async def respond(
        self,
        content: Any = None,
        *,
        embed=None,
        embeds=None,
        ephemeral: bool = False,
        **kwargs,
    ):
        all_embeds = [e for e in [embed, *(embeds or [])] if e is not None]
        self.responses.append(
            {
                "content": content if isinstance(content, str) else None,
                "embeds": [e.to_dict() for e in all_embeds],
                "ephemeral": ephemeral,
            }
        )

    def result(self) -> dict:
        return {"deferred": self.deferred, "responses": self.responses}

    async def wait_for_responses(self, timeout: float = 8.0) -> dict:
        """Command flows respond asynchronously (e.g. after Foundry acks a
        roll); wait so callers get the final state in one request."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self.responses and loop.time() < deadline:
            await asyncio.sleep(0.05)
        return self.result()


class RollRequest(BaseModel):
    guild_id: int
    discord_id: int
    character: str
    stat: str
    advantage: Optional[str] = None
    save: bool = False


class AttackRequest(BaseModel):
    guild_id: int
    discord_id: int
    character: str
    attack: str
    advantage: Optional[str] = None
    spell_level: Optional[int] = None
    attack_mode: Optional[str] = None


def _socket_namespace():
    ns = getattr(discord_client.bot, "socket_namespace", None)
    if ns is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Socket namespace not registered yet",
        )
    return ns


@router.get("/health")
async def health():
    bot = discord_client.bot
    return {
        "fake_discord": True,
        "guilds": [{"id": g.id, "name": g.name} for g in bot.guilds],
        "connected_foundry_guilds": list(_socket_namespace().guilds_to_sids.keys()),
    }


@router.get("/messages")
async def messages(clear: bool = False):
    fake_http = discord_client.bot.fake_http
    out = list(fake_http.messages)
    if clear:
        fake_http.messages.clear()
    return out


@router.get("/requests")
async def requests(clear: bool = False):
    fake_http = discord_client.bot.fake_http
    out = [
        {k: v for k, v in r.items() if k in ("method", "path", "json")}
        for r in fake_http.requests
    ]
    if clear:
        fake_http.requests.clear()
    return out


@router.post("/reset")
async def reset():
    discord_client.bot.fake_http.reset()
    return {"ok": True}


class XpSyncRequest(BaseModel):
    guild_id: int
    actor_id_to_xp: dict[str, int]


@router.post("/xp_sync")
async def xp_sync(req: XpSyncRequest):
    """Push XP to the connected Foundry world, like a session reward would."""
    from database.guild_settings_table import GuildSettingsTable

    guild_settings = GuildSettingsTable.lookup(req.guild_id)
    if guild_settings is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await _socket_namespace().xp_sync(guild_settings, dict(req.actor_id_to_xp))
    return {"ok": True}


class SessionStartRequest(BaseModel):
    guild_id: int
    mission_id: int
    name: str = "Test Session"
    start_ts: Optional[int] = None


class SessionStopRequest(BaseModel):
    guild_id: int
    mission_id: int


@router.post("/session_start")
async def session_start(req: SessionStartRequest):
    """Start a session, shaped like Events.on_scheduled_event_update does."""
    payload = {
        "name": req.name,
        "id": req.mission_id,
        "start_ts": req.start_ts or int(time.time() * 1000),
        "status": "start",
    }
    await _socket_namespace().start_stop_session(req.guild_id, payload)
    return {"ok": True}


@router.post("/session_stop")
async def session_stop(req: SessionStopRequest):
    await _socket_namespace().start_stop_session(
        req.guild_id, {"id": req.mission_id, "status": "stop"}
    )
    return {"ok": True}


class LookupRequest(BaseModel):
    guild_id: int
    discord_id: int
    kind: str  # item | spell | feat | rule | background
    name: str
    display: str = "public"


@router.post("/lookup")
async def command_lookup(req: LookupRequest):
    """Drive a /lookup content command (dnd5e SRD data, no actor needed)."""
    from groups.lookups import Lookups

    commands = {
        "item": Lookups.item_lookup,
        "spell": Lookups.spell_lookup,
        "feat": Lookups.feat_lookup,
        "rule": Lookups.rule_lookup,
        "background": Lookups.background_lookup,
    }
    command = commands.get(req.kind)
    if command is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown lookup kind {req.kind!r}",
        )
    cog = discord_client.bot.cogs.get("Lookups")
    if cog is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lookups cog not loaded",
        )
    ctx = FakeApplicationContext(req.guild_id, req.discord_id, f"lookup {req.kind}")
    await command.callback(cog, ctx, req.name, req.display)
    return ctx.result()


class CharacterLookupRequest(BaseModel):
    guild_id: int
    discord_id: int
    character: str
    detail: Optional[str] = None
    display: str = "public"


@router.post("/lookup_character")
async def command_lookup_character(req: CharacterLookupRequest):
    from groups.lookups import lookup_character

    ctx = FakeApplicationContext(req.guild_id, req.discord_id, "character")
    await lookup_character(
        ctx, req.character, req.detail, req.display, _socket_namespace()
    )
    return await ctx.wait_for_responses()


class DowntimeBuyRequest(BaseModel):
    guild_id: int
    discord_id: int
    character: str
    channel_id: Optional[int] = None
    item: Optional[str] = None
    extra_weeks: int = 0
    extra_gold: int = 0


@router.post("/downtime_buy")
async def command_downtime_buy(req: DowntimeBuyRequest):
    from groups.downtime import Downtime

    cog = discord_client.bot.cogs.get("Downtime")
    if cog is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Downtime cog not loaded",
        )
    ctx = FakeApplicationContext(
        req.guild_id, req.discord_id, "buy", channel_id=req.channel_id
    )
    await Downtime.command_downtime_buy.callback(
        cog, ctx, req.character, req.extra_weeks, req.extra_gold, req.item
    )
    return ctx.result()


@router.post("/roll")
async def command_roll(req: RollRequest):
    ctx = FakeApplicationContext(req.guild_id, req.discord_id, "roll")
    await roll(ctx, req.character, req.stat, req.advantage, req.save, _socket_namespace())
    return await ctx.wait_for_responses()


@router.post("/attack")
async def command_attack(req: AttackRequest):
    ctx = FakeApplicationContext(req.guild_id, req.discord_id, "attack")
    await roll_attack(
        ctx,
        req.character,
        req.attack,
        _socket_namespace(),
        req.advantage,
        req.spell_level,
        req.attack_mode,
    )
    return await ctx.wait_for_responses()
