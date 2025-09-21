import pprint
from typing import Dict, List, Callable

import socketio
from discord import Bot, ScheduledEventStatus, Guild, Embed
from fastapi import status, HTTPException
from sqlalchemy import select

from database import Session, GoldLedger
from database.guild_settings_table import GuildSettingsTable
from database.missions import MissionTable, edit_mission
from system.items import format_number
from system.rules import get_lvl
from models.guild_settings import GuildSettings
from models.missions import Mission
from routers.foundry_api import guild_auth
from routers.socket_io import sio
from utils import getLogger, gander7_discord_id, chris_discord_id

logger = getLogger(__name__)


def get_active_session(guild: Guild, bot: Bot):
    event = next(
        (
            e
            for e in guild.scheduled_events
            if e.status == ScheduledEventStatus.active
            and e.creator_id == bot.application_id
        ),
        None,
    )
    if not event:
        return None

    with Session() as session:
        mission: MissionTable = (
            session.query(MissionTable)
            .filter_by(guild_id=guild.id, event_id=event.id)
            .one()
        )

    return {
        "name": mission.title,
        "id": mission.id,
        "start_ts": int(event.start_time.timestamp() * 1000),
        "status": "start",
    }


# noinspection PyMethodMayBeStatic
class SocketNamespace(socketio.AsyncNamespace):
    guilds_to_sids: Dict[int, List[str]] = {}
    sid_to_guild: Dict[str, int] = {}
    guilds_to_missions_to_xp: Dict[int, Dict[int, list]] = {}
    bot: Bot

    def __init__(self, bot: Bot, namespace: str):
        self.bot = bot
        bot.socket_namespace = self
        super().__init__(namespace)

    def stop(self):
        for guild_id, missions in self.guilds_to_missions_to_xp.items():
            for mission, xps in missions.items():
                logger.warning(f"{guild_id=} {mission=} {sum(xps)=}")

    async def on_connect(self, sid: str, environ, auth):
        logger.info(f"{sid} connected")

        guild_settings = await guild_auth(
            environ["HTTP_ORIGIN"], auth.get("Authorization")
        )

        self.guilds_to_sids.setdefault(guild_settings.id, list()).append(sid)
        self.sid_to_guild[sid] = guild_settings.id

        if len(self.guilds_to_sids[guild_settings.id]) == 1:
            await self.__xp_resync(guild_settings, sid)
            guild = self.bot.get_guild(guild_settings.id)
            if guild:
                start_session_payload = get_active_session(guild, self.bot)
                if start_session_payload:
                    await sio.emit("session", start_session_payload, to=sid)
            else:
                logger.warning(f"Unknown Guild Id: {guild_settings.id}")

    async def on_xp(self, sid: str, payload: Dict):
        guild_id = self.sid_to_guild.get(sid)
        if not guild_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
        mission_id = payload["session_id"]
        if guild_id not in self.guilds_to_missions_to_xp:
            self.guilds_to_missions_to_xp[guild_id] = {}
        if mission_id not in self.guilds_to_missions_to_xp[guild_id]:
            self.guilds_to_missions_to_xp[guild_id][mission_id] = []

        self.guilds_to_missions_to_xp[guild_id][mission_id].append(payload["id_to_xp"])

    async def get_description(
        self, guild_id: int, actor_id: str, item_id: str, cb: Callable
    ) -> bool:
        sid = next(iter(self.guilds_to_sids.get(guild_id) or []), None)
        if sid:
            await sio.emit(
                "item_desc",
                {"actor_id": actor_id, "item_id": item_id},
                to=sid,
                callback=cb,
            )
            return True
        else:
            return False

    async def send_roll(
        self, guild_id: int, payload: dict, cb: Callable = lambda **kwargs: None
    ) -> bool:
        sid = next(iter(self.guilds_to_sids.get(guild_id) or []), None)
        if sid:
            await sio.emit("roll", payload, to=sid, callback=lambda r: cb(**r))
            return True
        else:
            return False

    async def on_combat(self, sid: str, payload: dict):
        guild_id = self.sid_to_guild.get(sid)
        if not guild_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

        guild_settings = GuildSettingsTable.lookup(guild_id)
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(guild_settings.combat_channel_id)
        if not channel:
            return

        if (
            isinstance(payload, dict)
            and isinstance(payload.get("title", None), str)
            and isinstance(payload.get("description", None), str)
        ):
            embed = Embed(
                title=payload["title"][:256], description=payload["description"][:4096]
            )
            if isinstance(payload.get("fields", None), list):
                for field in payload["fields"][:25]:
                    if (
                        isinstance(field, dict)
                        and isinstance(field.get("name", None), str)
                        and isinstance(field.get("value", None), str)
                    ):
                        embed.add_field(
                            name=field["name"][:256],
                            value=field["value"][:1024],
                            inline=bool(field.get("inline", True)),
                        )
            await channel.send(embed=embed)
        elif isinstance(payload, str) and payload:
            await channel.send(content=payload[:2000])
        elif guild.owner_id in [gander7_discord_id, chris_discord_id]:
            await guild.owner.send(content=pprint.pformat(payload)[:2000])
        else:
            logger.warning(pprint.pformat(payload))

    async def start_stop_session(self, guild_id: int, payload: dict) -> None:
        sids = self.guilds_to_sids.get(guild_id)
        if sids:
            await sio.emit("session", payload, to=sids[0])

        if payload["status"] == "stop":
            mission_id = payload["id"]

            if guild_id not in self.guilds_to_missions_to_xp:
                logger.warning(f"{guild_id=} {mission_id=} | Guild not in session obj!")
                return

            if mission_id not in self.guilds_to_missions_to_xp[guild_id]:
                logger.warning(
                    f"{guild_id=} {mission_id=} | Mission not in session obj!"
                )
                return

            xp = sum(
                i[0][1] for i in self.guilds_to_missions_to_xp[guild_id][mission_id]
            )
            del self.guilds_to_missions_to_xp[guild_id][mission_id]
            if not self.guilds_to_missions_to_xp[guild_id]:
                del self.guilds_to_missions_to_xp[guild_id]

            if not xp:
                return

            guild_settings = GuildSettingsTable.lookup(guild_id)

            with Session() as session:
                stmt = select(MissionTable).filter_by(guild_id=guild_id, id=mission_id)
                mission = Mission.model_validate(session.scalar(stmt))

            if not mission.pcs:
                logger.info(f"Mission {mission.title} has no actors!")
                return

            mission.xp = xp
            guild = self.bot.get_guild(guild_id)
            actors_names_to_levels_before = {
                a.name: get_lvl(a.get_exp(guild_settings)) for a in mission.get_actors()
            }
            await edit_mission(guild, mission)
            name_id_xp = [
                [a.name, a.id, a.get_exp(guild_settings)] for a in mission.get_actors()
            ]
            actor_names_to_levels = {name: get_lvl(xp) for [name, _, xp] in name_id_xp}

            out_str = "\n".join(
                [
                    f"**{format_number(xp, 'XP')} rewarded for **{mission.title}**!**",
                    *[
                        f"**{name}**: {actors_names_to_levels_before[name]} -> {lvl}"
                        for (name, lvl) in actor_names_to_levels.items()
                        if lvl > actors_names_to_levels_before[name]
                    ],
                ]
            )

            await guild.get_channel_or_thread(mission.channel_or_thread_id).send(
                out_str
            )
            await self.xp_sync(guild_settings, {_id: xp for [_, _id, xp] in name_id_xp})

    async def xp_sync(
        self, guild_settings: GuildSettings, actor_id_to_xp: Dict[str, int]
    ):
        if not actor_id_to_xp:
            logger.error("No XP??")
            return

        had_pending = bool(guild_settings.pending_xp)
        guild_settings.enqueue_xp(actor_id_to_xp)
        sid = next(iter(self.guilds_to_sids.get(guild_settings.id) or []), None)
        if sid:
            await sio.emit("xp", guild_settings.pending_xp, to=sid)
            guild_settings.pending_xp = None

        if not sid or had_pending:
            GuildSettingsTable.commit(guild_settings)

    async def __xp_resync(self, guild_settings: GuildSettings, sid: str):
        if guild_settings.pending_xp:
            await sio.emit("xp", guild_settings.pending_xp, to=sid)
            guild_settings.pending_xp = None
            GuildSettingsTable.commit(guild_settings)
            logger.info(f"XP Synced!\n{pprint.pformat(guild_settings.pending_xp)}")
        else:
            logger.debug("No pending XP")

    async def gold_sync(
        self, guild_settings: GuildSettings, gold_ledgers: List[GoldLedger]
    ):
        pass

    def on_disconnect(self, sid):
        logger.info(f"{sid} disconnected")
        if sid in self.sid_to_guild:
            guild_id = self.sid_to_guild.pop(sid)
            if guild_id:
                self.guilds_to_sids[guild_id].remove(sid)
