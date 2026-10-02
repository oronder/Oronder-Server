import base64
import json
import math
import os
import secrets

import discord
import time
from pprint import pformat
from typing import Annotated

import aiohttp
from discord import Bot
from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.exc import NoResultFound

import discord_client
from database import Session
from database.actor_table import ActorTable
from database.system_actor_table import SystemActorTable
from database.guild_settings_table import GuildSettingsTable
from integrations.wikijs import upload_to_wiki, delete_from_wiki
from models.actor import Actor
from models.base_model import OronderBaseModel
from models.game_systems import UnsupportedGameSystem, actor_model_for
from models.guild_settings import (
    GuildSettings,
    GuildSettingsInterface,
    current_subscription,
)
from utils import (
    CHANGELOG_CHANNEL_ID,
    WIKIJS_GUILD_IDS,
    ensure_members,
    getLogger,
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


# py-cord logs "Can't keep up" above 10s; normal is well under 1s.
MAX_GATEWAY_LATENCY = 30.0


def _app_id_from_token() -> str:
    # A bot token's first segment is the application id, base64-encoded, so this
    # answers before the gateway connects (bot.application_id is set at READY).
    first = os.environ["DISCORD_TOKEN"].split(".")[0]
    return base64.b64decode(first + "=" * (-len(first) % 4)).decode()


@router.get("/config")
async def config():
    """What a client needs before it can pair: which Discord application to
    authorize against, and the redirect that application must have registered.

    Unauthenticated, because both are public: the application id appears in
    every invite link, and the redirect is a URL on this server. Serving them
    means a self-hosted instance only has to tell the Foundry module its
    origin. The module previously hardcoded both, and they have to match the
    server exactly or Discord refuses the pairing.
    """
    app_id = discord_client.bot.application_id or _app_id_from_token()
    return {"discord_app_id": str(app_id), "redirect_uri": REDIRECT_URI}


@router.get("/health")
async def health():
    """Container healthcheck: the HTTP API *and* the Discord gateway.

    The heartbeat above only proves HTTP is up. On 2026-09-21 the gateway was
    dead for 11 hours -- the bot was bound to an event loop nothing ran --
    while the heartbeat kept answering 200 and Docker reported healthy.

    Reads client state only: no Discord calls, no waiting, so it answers fast
    even when the gateway is wedged.
    """
    bot = discord_client.bot
    problems = []
    if bot.is_closed():
        problems.append("gateway closed")
    if not bot.is_ready():
        problems.append("not ready")

    # nan with no websocket, inf before the first heartbeat ack. During the
    # incident this read ~2000s: it is the value behind py-cord's "websocket is
    # X s behind" warning.
    latency = bot.latency
    if not math.isfinite(latency) or latency > MAX_GATEWAY_LATENCY:
        shown = f"{latency:.1f}s" if math.isfinite(latency) else str(latency)
        problems.append(f"gateway latency {shown}")

    # A heartbeat thread that can no longer send stops receiving acks; catch it
    # even if the last measured latency looks fine. Absent mid-reconnect.
    keep_alive = getattr(getattr(bot, "ws", None), "_keep_alive", None)
    if keep_alive is not None:
        silent = time.perf_counter() - keep_alive._last_ack
        if silent > 3 * keep_alive.interval:
            problems.append(f"no heartbeat ack for {silent:.0f}s")

    if problems:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="; ".join(problems)
        )
    return {"status": "ok", "gateway_latency": round(latency, 3)}


@router.post("/update_discord")
async def update_foundry_module(js: dict, authorization: str = Header()):
    # Posts to the changelog channel as the bot, so it needs a real secret.
    # Read per request rather than at import: self-hosted instances have no
    # module release pipeline and shouldn't need to set it. Fail closed when
    # it is unset -- a missing key must never mean "no check".
    key = os.environ.get("UPDATE_DISCORD_KEY")
    if not key or not secrets.compare_digest(authorization, key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    logger.critical(f"POSTING ADDON UPDATE MSG {js=}")

    version = js.get("version")
    changes = js.get("changes")
    if version and changes:
        await _post_changelog(f"Foundry Module {version}", changes)


@router.post("/changelog")
async def post_changelog(js: dict, authorization: str = Header()):
    """Announce a release in the changelog channel, as the bot.

    The module pipeline has /update_discord; this is the general form, used by
    this repo's own workflow to post backend changes under their own heading.
    """
    key = os.environ.get("UPDATE_DISCORD_KEY")
    if not key or not secrets.compare_digest(authorization, key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    title = js.get("title")
    changes = js.get("changes")
    if not title or not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="title and changes are both required.",
        )
    await _post_changelog(title, changes)


async def _post_changelog(header: str, changes: list[str]) -> None:
    bot = await get_bot()
    changelog_channel = (
        bot.get_channel(CHANGELOG_CHANNEL_ID) if CHANGELOG_CHANNEL_ID else None
    )
    if changelog_channel is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Changelog channel not configured.",
        )
    body = "\n".join(f"- {c}" for c in changes)
    try:
        msg = await changelog_channel.send(f"**{header}**\n{body}")
    except discord.Forbidden as e:
        # The likeliest misconfiguration: the channel exists but the bot cannot
        # write to it. Say so, rather than returning a traceback.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Cannot post in #{changelog_channel}: {e}",
        ) from e
    try:
        # Only announcement channels can publish, and only those let other
        # servers follow the changelog. A plain text channel is a perfectly
        # reasonable choice for a self-hosted instance, so do not fail on it.
        await msg.publish()
    except discord.HTTPException as e:
        logger.info(f"not published ({e}); posted to #{changelog_channel} anyway")


async def synced_actor(payload: Annotated[dict, Body()]) -> OronderBaseModel:
    """Validate a synced actor against the model for its game system.

    See models.game_systems for the contract. A payload with no game_system
    tag is dnd5e, so module versions that predate the tag are unaffected.
    """
    try:
        model = actor_model_for(payload)
    except UnsupportedGameSystem as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    try:
        return model.model_validate(payload)
    except ValidationError as e:
        # The same 422 FastAPI produced back when this was a typed body
        # parameter, down to the "body" prefix on each error's location.
        raise RequestValidationError(
            [
                {**error, "loc": ("body", *error["loc"])}
                for error in e.errors(include_url=False, include_context=False)
            ]
        ) from e


@router.put("/actor")
async def upsert_actor(
    # Order matters: FastAPI resolves dependencies in parameter order, and auth
    # must come before validating the body, or an unauthenticated caller with a
    # malformed body gets a 422 describing the schema instead of a 401.
    guild_settings: Annotated[GuildSettings, Depends(guild_auth)],
    actor: Annotated[OronderBaseModel, Depends(synced_actor)],
    session=Depends(session_handler),
):
    # dnd5e keeps its own table, shaped like dnd5e's roll data; every other
    # system shares system_actors. See models.game_systems.
    if isinstance(actor, Actor):
        session.merge(ActorTable.from_model(actor, guild_settings.id))
    else:
        session.merge(SystemActorTable.from_model(actor, guild_settings.id))
    session.commit()
    # The wiki export renders a dnd5e sheet, so it only knows dnd5e actors.
    if isinstance(actor, Actor) and guild_settings.id in WIKIJS_GUILD_IDS:
        logger.warning(f"Upserting {actor.name} to wiki!")
        wikijs_task_queue.add_task(upload_to_wiki, actor)


@router.delete("/actor/{actor_id}")
async def delete_actor(
    actor_id: str, guild_settings=Depends(guild_auth), session=Depends(session_handler)
):
    # The id alone does not say which system the actor belongs to, so look in
    # both tables: dnd5e's, then everyone else's.
    try:
        actor = (
            session.query(ActorTable)
            .filter_by(id=actor_id, guild_id=guild_settings.id)
            .one()
        )
        if guild_settings.id in WIKIJS_GUILD_IDS:
            logger.warning(f"Deleting {actor.name} from wiki!")
            wikijs_task_queue.add_task(delete_from_wiki, Actor.model_validate(actor))
    except NoResultFound:
        actor = (
            session.query(SystemActorTable)
            .filter_by(id=actor_id, guild_id=guild_settings.id)
            .one_or_none()
        )
        if actor is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Actor {actor_id} not found",
            ) from None

    session.delete(actor)
    session.commit()


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
    await ensure_members(guild)
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

    await ensure_members(guild)
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

    return {"errs": guild_settings.validate_channels(guild, True)}
