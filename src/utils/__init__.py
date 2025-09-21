import hashlib
import json
import logging
import os
import platform
import re
from datetime import datetime
from io import BytesIO
from logging import Logger
from typing import List, Tuple, Optional
from urllib.parse import urlparse

import aiohttp
import dateparser
import httpx
import numpy as np
import pytz
import pytzdata
import tabulate as tabulate_lib
import uvicorn.logging
from PIL import Image
from discord import (
    Thread,
    Role,
    Member,
    Embed,
    ApplicationContext,
    ForumChannel,
    TextChannel,
    VoiceChannel,
    StageChannel,
)

from discord.abc import GuildChannel

disord_token_url = "https://discord.com/api/oauth2/token"

oronder_server_id = 860520082697617468
oronder_dnd_server_id = 933858354177118228
oronder_permissions_test_server_id = 1147549883310551181
my_guild_ids = [oronder_server_id, oronder_permissions_test_server_id]
supporter_role_id = 1127646084743839877
beta_tester_role_id = 1199113447540015115

oronder_changelog_channel_id = 1160679333506076752

chris_discord_id = 579751233871544363
gander7_discord_id = 159326185778577408
tomp_discord_id = 858570374349979648
johan_discord_id = 352211993249185803
kabs_discord_id = 129420236767232000

oronder_bot_prod = 1064553830810923048
oronder_bot_test = 1148024288973160529
oronder_bot_dev = 1126179973284237374

log_level = os.getenv("LOG_LEVEL", "INFO")


class OronderLogger(Logger):
    def __init__(self, name):
        super().__init__(name, log_level)

    def err_msg(self, msg: str, guild_id: int | None = None):
        self.error(f"{guild_id}: {msg}" if guild_id else msg)
        return {"content": msg[:2000], "ephemeral": True}


logging.setLoggerClass(OronderLogger)


def getLogger(name) -> OronderLogger:
    return logging.getLogger(name)


logger = getLogger(__name__)

NOT_FOUND = "Not Found"

hours_list = []
for hour in range(1, 12):
    hours_list.append(f"{hour}:00 AM")
hours_list.append("12:00 PM")
for hour in range(1, 12):
    hours_list.append(f"{hour}:00 PM")
hours_list.append("12:00 AM")
hours_list = [*hours_list[14:], *hours_list[:14]]
# hours_list = [j for i in hours_list for j in [i, i.replace('00', '30')]]

timezones: List[str] = [
    *[t for t in pytzdata.timezones if t.split("/")[0] == "US"],
    *[t for t in pytzdata.timezones if t.split("/")[0] != "US"],
]

time_format = "%m/%d/%Y %I:%M %p"


def parse_time(
    time_string: str, timezone_default: str
) -> Tuple[Optional[datetime], Optional[str]]:
    try:
        might_have_tz = (
            time_string[-2:].casefold() not in ["am", "pm"]
            and not time_string[-1].isnumeric()
        )
        timezone_parsed = time_string.split(" ")[-1] if might_have_tz else None
        timezone_parsed = (
            timezone_parsed
            if timezone_parsed in pytzdata.timezones
            else timezone_default
        )
        dt_string = " ".join(time_string.split()[:-1]) if might_have_tz else time_string
        dt_string = datetime.strptime(dt_string, time_format)
        out = pytz.timezone(timezone_parsed).localize(dt_string)
    except ValueError:
        settings = {
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "TIMEZONE": timezone_default,
        }

        out = dateparser.parse(time_string, settings=settings)

    return (
        (out.replace(microsecond=0), None)
        if out
        else (None, f"Unable to parse date time: *{time_string}*")
    )


def format_time(dt: datetime) -> str:
    return f"{dt.strftime(time_format)} {dt.tzinfo.zone if dt.tzinfo else ''}".replace(
        " 0", " "
    )


# time_format_example = format_time(parse_time("10/29/1929 9:30 AM", 'US/Eastern')[0])


def get_memory_usage():
    if platform.system() == "Linux":
        with open("/proc/self/status") as f:
            memusage = f.read().split("VmRSS:")[1].split("\n")[0][:-3]

        mbs = int(int(memusage.strip()) / 1024)
        if mbs < 1024:
            return f"Using {mbs}MB."
        else:
            return f"Using {int(mbs / 1024)}GB, {int(mbs % 1024)}MB."
    else:
        return ""


def tabulate(d: dict) -> str:
    tabulate_lib.MIN_PADDING = 0
    t = (
        tabulate_lib.tabulate(d, headers="keys", tablefmt="rounded_grid")
        .replace("│ ", "│")
        .replace(" │", "│")
        .replace("╭──", "╭")
        .replace("┬──", "┬")
        .replace("├──", "├")
        .replace("┼──", "┼")
        .replace("╰──", "╰")
        .replace("┴──", "┴")
    )
    return f"```\n{t}\n```"


def init_logger():
    class EndpointFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return (
                "/zqaBTpcyxNdiS2uRjC0pl7WP9snUPkZy" not in record.getMessage()
                and "HEAD" not in record.args
            )

    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

    stream_handler = logging.StreamHandler()
    stream_handler.formatter = uvicorn.logging.DefaultFormatter(
        fmt="{levelprefix} {asctime} | {filename}.{funcName}:{lineno} | {message}",
        style="{",
        use_colors=True,
    )
    logging.getLogger().setLevel(log_level)
    logging.getLogger().handlers = [stream_handler]
    logging.getLogger().propagate = True


def is_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def get_image_bytes(url: str | None) -> bytes | None:
    image_bytes = None
    if url and is_url(url):
        try:
            image_bytes = httpx.get(url).content
            Image.open(BytesIO(image_bytes))
        except Exception:
            image_bytes = None
    return image_bytes


def err_msg(msg, guild_id: int | None = None):
    logger.error(f"{guild_id}: {msg}" if guild_id else msg)
    return {"content": msg, "ephemeral": True}


def capitalize_title(s: str):
    if not s:
        return s
    articles = ["a", "an", "any", "the", "of", "or", "and", "in", "with"]
    result = []

    for word in s.split():
        if word.startswith("(") and word[1:].lower() not in articles:
            result.append(word[0] + word[1:].capitalize())
        elif word.lower() not in articles:
            result.append(word.capitalize())
        else:
            result.append(word.lower())

    return " ".join(result)


def check_permissions(
    channel: TextChannel | ForumChannel | VoiceChannel | StageChannel,
    role: Member | Role,
    requires_mention: bool = False,
    external: bool = False,
) -> str | None:
    """
    :param channel: channel to check permissions against
    :param role: Typically the bot's role, but could be used to check gm_role
    :param requires_mention: check if bot requires mention all permission for channel
    :param external: Format output string for non-Discord use
    :return: Description of required permissions if any.
    """
    perms = channel.permissions_for(role)
    if perms.administrator:
        return None

    errs = []
    if requires_mention and not perms.mention_everyone:
        errs.append("Mention Everyone")
    if not perms.view_channel:
        errs.append("View Channel")

    if isinstance(channel, StageChannel):
        if not perms.manage_channels:
            errs.append("Stage Moderator")
    if isinstance(channel, VoiceChannel) or isinstance(channel, StageChannel):
        if not perms.connect:
            errs.append("Connect")
        if not perms.use_voice_activation:
            errs.append("Use Voice Activation")
        if not perms.manage_events:
            errs.append("Manage Events")
    else:
        if not perms.embed_links:
            errs.append("Embed Links")

    if isinstance(channel, TextChannel):
        if not perms.send_messages:
            errs.append("Send Messages")
    if isinstance(channel, ForumChannel):
        if not perms.manage_threads:
            errs.append("Manage Threads")
        if not perms.send_messages_in_threads:
            errs.append("Send Messages in Posts")

    return (
        join_list([f"[{e.upper()}]" if external else f"**{e}**" for e in errs], " and ")
        if errs
        else None
    )


def mention_safe(mentionable: Thread | GuildChannel | Role | Member | None) -> str:
    if not mentionable:
        return NOT_FOUND
    elif isinstance(mentionable, Role) and mentionable.id == mentionable.guild.id:
        return mentionable.name  # handle @everyone
    # elif isinstance(mentionable, Member):
    else:
        return mentionable.mention


def hash_json_object(obj):
    if isinstance(obj, dict):
        # Sort the keys alphabetically
        sorted_json_object = {k: hash_json_object(v) for k, v in sorted(obj.items())}
        return hashlib.sha256(
            json.dumps(sorted_json_object, sort_keys=True).encode("utf-8")
        ).hexdigest()
    elif isinstance(obj, list):
        # For lists, recursively sort and hash each element
        sorted_list = [hash_json_object(item) for item in obj]
        return hashlib.sha256(
            json.dumps(sorted_list, sort_keys=True).encode("utf-8")
        ).hexdigest()
    else:
        # For non-dict and non-list types, return the string representation
        return str(obj)


def invalid(e: Embed):
    return len(e.fields) > 25 or len(e) > 6000


def field_index_to_pop(e: Embed):
    return (
        next(
            idx
            for idx, cum in reversed(
                list(
                    enumerate(
                        np.array([len(f.value) for f in e.fields]).cumsum().tolist()
                    )
                )
            )
            if cum < 6000
        )
        if len(e) > 6000
        else 25
    )


async def respond_with_long_embed(ctx: ApplicationContext, embed: Embed, **kwargs):
    embeds = [embed]
    while invalid(embed):
        cur = Embed(color=embed.color)
        field_index = field_index_to_pop(embed)
        while (
            invalid(embed)
            and len(cur.fields) < 25
            and len(cur) + len(embed.fields[field_index].value) < 6000
        ):
            cur.append_field(embed.fields[field_index])
            embed.remove_field(field_index)
            field_index = field_index_to_pop(embed)
        embeds.append(cur)

    if len(embeds) > 1:
        if embed.footer and embed.footer.text and embed.footer.icon_url:
            embeds[-1].set_footer(
                text=embed.footer.text, icon_url=embed.footer.icon_url
            )
            embed.set_footer(text=None, icon_url=None)
        if embed.image and embed.image.url:
            embeds[-1].set_image(url=embed.image.url)
            embed.set_image(url=None)

    if kwargs.get("ephemeral", False) and len(embeds) > 1:
        msgs = [await ctx.user.send(embed=embed) for embed in embeds]
        await ctx.respond(
            content=f"Response sent as private message.\n{msgs[0].jump_url}",
            ephemeral=True,
        )

    else:
        await ctx.respond(embed=embeds[0], **kwargs)
        for embed in embeds[1:]:
            await ctx.channel.send(embed=embed)


def join_list(list_of_strings: list, middle: str, end: str | None = None):
    if isinstance(list_of_strings, str):
        logger.warning(f"expecting list, but got {list_of_strings}")
        return list_of_strings

    if not len(list_of_strings):
        return ""

    if not end:
        end = middle

    return end.join(
        [
            str(s)
            for s in [
                middle.join([str(s) for s in list_of_strings[0:-1] if s]),
                list_of_strings[-1],
            ]
            if s
        ]
    )


def camel_to_words(camel_str):
    return re.sub(r"([A-Z][a-z0-9]*)", r" \1", camel_str).lstrip()


def truncate(string: str, length: int) -> str:
    return f"{string[: length - 3]}..." if len(string) > length else string


async def run_uptime_monitor():
    token = os.getenv("GITHUB_UPTIME_PAT")
    url = os.getenv("GITHUB_UPTIME_URL")

    if not token or not url:
        logger.warning(f"uptime endpoint unset {token=} {url=}")
        return

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={'ref': 'master'}) as response:
            if response.status == 204:
                logger.info('Uptime Github Workflow triggered successfully!')
            else:
                logger.error(f'Uptime Github Workflow Error: {await response.text()}')
