import base64
import json
import os
import secrets
from pprint import pformat
from typing import Annotated

import aiohttp
from discord import Bot
from fastapi import Depends, HTTPException, APIRouter, Query, Header, status, FastAPI
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import NoResultFound

import discord_client
from database import Session
from database.actor_table import ActorTable
from database.guild_settings_table import GuildSettingsTable
from integrations.wikijs import upload_to_wiki, delete_from_wiki
from models.actor import Actor
from models.guild_settings import (
    GuildSettings,
    GuildSettingsInterface,
    current_subscription,
)
from utils import (
    oronder_dnd_server_id,
    getLogger,
    oronder_server_id,
    oronder_changelog_channel_id,
    disord_token_url,
    timezones,
)
from utils.WikiJsTaskQueue import wikijs_task_queue

logger = getLogger(__name__)
router = APIRouter()

DISCORD_CLIENT_SECRET = os.environ["DISCORD_CLIENT_SECRET"]
REDIRECT_URI = f"{os.environ['API_URL']}/init"


def session_handler():
    with Session() as session:
        yield session


async def get_bot():
    await discord_client.bot.wait_until_ready()
    return discord_client.bot


def init_return(d: dict):
    if "status_code" not in d:
        d["status_code"] = 200
    if "errs" not in d:
        d["errs"] = []
    msg = json.dumps(d)
    return f'<html lang="en"><body><script>window.addEventListener("message", (e) => {{e.source.postMessage({msg}, e.origin)}})</script></body></html>'


class InitException(Exception):
    def __init__(
        self, status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR, detail: str = ""
    ):
        self.status_code: int = status_code
        self.detail: str = detail
        return


def attach_exception_handler(app: FastAPI):
    @app.exception_handler(InitException)
    async def exc_handler(_, exc: InitException):
        return HTMLResponse(
            init_return({"status_code": exc.status_code, "errs": [exc.detail]})
        )


async def guild_auth(
    origin: str = Header(), authorization: str = Header()
) -> GuildSettings:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST)
    with Session() as session:
        guild_settings = (
            session.query(GuildSettingsTable)
            .filter_by(auth_token=authorization)
            .one_or_none()
        )

    if guild_settings:
        return GuildSettings.model_validate(guild_settings)
    else:
        logger.error(f"{origin=} {authorization=}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)


@router.get("/zqaBTpcyxNdiS2uRjC0pl7WP9snUPkZy")
async def heart_beat():
    return "thump thump"


@router.post("/update_discord")
async def update_foundry_module(js: dict, authorization: str = Header()):
    key = "CiHI3kGl1eMJBY4pvAxcHSAai5jdPhkaIPDlOeHuxg9GUpaSROTcTAehTb8vMH8xZVrX97tmy508WQd2fJa98CRC2P4qs"
    if not secrets.compare_digest(authorization, key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    logger.critical(f"POSTING ADDON UPDATE MSG {js=}")

    version = js.get("version")
    changes = js.get("changes")
    changes = "\n".join([f"- {c}" for c in changes])
    if version and changes:
        bot = await get_bot()
        changelog_channel = bot.get_guild(oronder_server_id).get_channel(
            oronder_changelog_channel_id
        )
        msg = await changelog_channel.send(f"**Foundry Module {version}**\n{changes}")
        await msg.publish()


@router.put("/actor")
async def upsert_actor(
    actor: Actor, guild_settings=Depends(guild_auth), session=Depends(session_handler)
):
    actor_orm = ActorTable.from_model(actor, guild_settings.id)
    session.merge(actor_orm)
    session.commit()
    if guild_settings.id in [oronder_dnd_server_id]:
        logger.warning(f"Upserting {actor.name} to wiki!")
        wikijs_task_queue.add_task(upload_to_wiki, actor)


@router.delete("/actor/{actor_id}")
async def delete_actor(
    actor_id: str, guild_settings=Depends(guild_auth), session=Depends(session_handler)
):
    try:
        actor = (
            session.query(ActorTable)
            .filter_by(id=actor_id, guild_id=guild_settings.id)
            .one()
        )

        if guild_settings.id in [oronder_dnd_server_id]:
            logger.warning(f"Deleting {actor.name} from wiki!")
            wikijs_task_queue.add_task(delete_from_wiki, Actor.model_validate(actor))

        session.delete(actor)
        session.commit()
    except NoResultFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Actor {actor_id} not found"
        )


@router.get("/init", response_class=HTMLResponse)
async def init(
    code: Annotated[str, Query()],
    guild_id: Annotated[int, Query()],
    state: Annotated[str, Query()],
    bot: Bot = Depends(get_bot),
):
    async with aiohttp.ClientSession() as http_session:
        async with http_session.post(
            disord_token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": str(bot.application_id),
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
        ) as response:
            token_response = await response.json()
            if not response.ok:
                if (
                    response.status == status.HTTP_429_TOO_MANY_REQUESTS
                    and "message" in token_response
                    and "retry_after" in token_response
                ):
                    detail = f"{token_response['message']} Retry after {int(token_response['retry_after'] / 60)} minutes."
                elif "error_description" in token_response:
                    detail = token_response["error_description"]
                else:
                    detail = response.reason

                logger.error(
                    f"{guild_id=}\n{response.status=}\n{detail=}\n{REDIRECT_URI=}\ntoken_response={pformat(token_response)}\n"
                )
                raise InitException(status_code=response.status, detail=detail)

    if guild_id != int(token_response["guild"]["id"]):
        raise InitException(status_code=status.HTTP_401_UNAUTHORIZED)

    guild = bot.get_guild(guild_id)
    if not guild:
        raise InitException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Oronder must be a member of Discord Server",
        )

    guild_settings: GuildSettings | None = GuildSettingsTable.lookup(guild_id)
    auth_token = secrets.token_urlsafe()

    if guild_settings:
        guild_settings.auth_token = auth_token
    else:
        text_channels = guild.text_channels
        if not text_channels:
            raise InitException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Must have at least one text channel!",
            )
        default_text_channel = next(
            (c for c in text_channels if c.name == "general"), text_channels[0]
        )

        voice_channels = guild.voice_channels
        if not voice_channels:
            raise InitException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Must have at least one voice channel!",
            )

        voice_channel = next(
            (c for c in voice_channels if c.name == "General"), voice_channels[0]
        )

        state_decoded = base64.b64decode(state).decode("utf-8")
        tz = state_decoded[: state_decoded.index("|")]
        tz = tz if tz in timezones else "US/Eastern"
        foundry_hostname = state_decoded[state_decoded.index("|") + 1 :]

        guild_settings: GuildSettings = GuildSettings(
            id=guild_id,
            gm_role_id=guild.default_role.id,
            gm_xp=0,
            scheduling_channel_id=default_text_channel.id,
            session_channel_id=default_text_channel.id,
            voice_channel_id=voice_channel.id,
            downtime_channel_id=default_text_channel.id,
            downtime_gm_channel_id=None,
            subscription=current_subscription(bot, guild),
            foundry_hostname=foundry_hostname,
            auth_token=auth_token,
            timezone=tz,
            starting_level=1,
        )

    GuildSettingsTable.commit(guild_settings)
    return init_return(
        {
            "auth": auth_token,
            "guild": guild_settings.to_interface(guild).to_dict(),
            "errs": guild_settings.validate_channels(guild, True),
        }
    )


@router.get("/guild", response_model=GuildSettingsInterface)
async def get_guild_info(
    guild_settings: GuildSettings = Depends(guild_auth),
    bot: Bot = Depends(get_bot),
):
    guild = bot.get_guild(guild_settings.id)
    if not guild:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bot cannot connect to Discord Server: {guild_settings.id}.",
        )

    return guild_settings.to_interface(guild)


@router.post("/guild")
async def update_guild_info(
    guild_settings_interface: GuildSettingsInterface,
    guild_settings: GuildSettings = Depends(guild_auth),
    bot: Bot = Depends(get_bot),
):
    guild_settings = guild_settings.from_interface(guild_settings_interface)
    GuildSettingsTable.commit(guild_settings)
    guild = bot.get_guild(guild_settings.id)

    return {'errs': guild_settings.validate_channels(guild, True)}
