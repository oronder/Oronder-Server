import os
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


async def get_bot():
    await discord_client.bot.wait_until_ready()
    return discord_client.bot


@router.get("/bot/info")
async def get_bot_info(authorization: str = Header(), bot: Bot = Depends(get_bot)):
    # Lists every guild with its owner and Foundry hostname, so it needs a
    # real secret. Read per request; unset fails closed.
    key = os.environ.get("ADMIN_API_KEY")
    if not key or not secrets.compare_digest(authorization, key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    with Session() as session:
        guild_settings = session.execute(select(GuildSettingsTable)).scalars().all()

    configs = {g.id: g for g in guild_settings}

    installs = {
        guild.id: {
            "name": guild.name,
            "member_count": guild.member_count,
            "owner": {
                # guild.owner is looked up in the member cache, which is now
                # loaded per guild on first use (utils.ensure_members).
                "id": str(guild.owner_id),
                "name": guild.owner.global_name if guild.owner else "UNKNOWN",
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
