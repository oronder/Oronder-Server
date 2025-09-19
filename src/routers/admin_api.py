import secrets

from discord import Bot
from fastapi import Depends, HTTPException, APIRouter, Header, status
from sqlalchemy import select

import discord_client
from database import Session
from database.guild_settings_table import GuildSettingsTable
from utils import getLogger

logger = getLogger(__name__)
router = APIRouter(prefix="/admin")
key = "6YvBnmaLk7lvsawEGz8hVG8Cru_ZAmFPALaJxeYrz4g"


async def get_bot():
    await discord_client.bot.wait_until_ready()
    return discord_client.bot


@router.get("/bot/info")
async def get_bot_info(authorization: str = Header(), bot: Bot = Depends(get_bot)):
    if not secrets.compare_digest(authorization, key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    with Session() as session:
        guild_settings = session.execute(select(GuildSettingsTable)).scalars().all()

    configs = {g.id: g for g in guild_settings}

    installs = {
        guild.id: {
            "name": guild.name,
            "member_count": guild.member_count,
            "owner": {
                "id": str(guild.owner.id) or "UNKNOWN",
                "name": guild.owner.global_name or "UNKNOWN",
            },
        }
        for guild in bot.guilds
    }

    out = []
    for guild_id in {*installs.keys(), *configs.keys()}:
        guild = {"id": str(guild_id)}

        if guild_id in configs:
            guild["timezone"] = configs[guild_id].timezone
            guild["hostname"] = configs[guild_id].foundry_hostname
            guild["status"] = "present" if guild_id in installs else "past"
        else:
            guild["status"] = "future"

        if guild_id in installs:
            guild.update(installs[guild_id])

        out.append(guild)

    return out
