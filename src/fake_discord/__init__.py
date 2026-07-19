"""First-class fake Discord mode.

Lets the whole server run without any connection to Discord: no gateway
login, no HTTP calls to discord.com. Real py-cord model objects
(Guild/Member/TextChannel/...) are constructed from seed data and injected
into the bot's connection state, and the HTTP layer is replaced with an
in-memory recorder that answers with canned payloads. Everything above
that boundary (cogs, routers, socket namespace) runs unmodified.

Activation: set FAKE_DISCORD=1, or leave DISCORD_TOKEN unset/empty.
Seed data: FAKE_DISCORD_SEED may point to a JSON file describing guilds;
otherwise a single default guild is created (see DEFAULT_SEED).
"""

import itertools
import json
import os
from typing import Any

from utils import getLogger

logger = getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

FAKE_BOT_USER_ID = 990000000000000001
DEFAULT_GUILD_ID = 990000000000000100

# Channel ids for the default seed guild, exposed for tests.
DEFAULT_CHANNELS = {
    "general": 990000000000000101,
    "voice": 990000000000000102,
    "combat": 990000000000000103,
    "downtime": 990000000000000104,
    "scheduling": 990000000000000105,
}
DEFAULT_GM_ROLE_ID = 990000000000000201
DEFAULT_GM_USER_ID = 990000000000000301
DEFAULT_PLAYER_USER_ID = 990000000000000302

# A second guild reserved for onboarding (/init) tests: /init rotates the
# guild's auth token, so it must not share a guild with other tests.
ONBOARDING_GUILD_ID = 990000000000000600
ONBOARDING_CHANNELS = {
    "general": 990000000000000601,
    "voice": 990000000000000602,
}

DEFAULT_SEED = {
    "guilds": [
        {
            "id": DEFAULT_GUILD_ID,
            "name": "Fake Guild",
            "owner_id": DEFAULT_GM_USER_ID,
            "text_channels": [
                {"id": DEFAULT_CHANNELS["general"], "name": "general"},
                {"id": DEFAULT_CHANNELS["combat"], "name": "combat"},
                {"id": DEFAULT_CHANNELS["downtime"], "name": "downtime"},
                {"id": DEFAULT_CHANNELS["scheduling"], "name": "scheduling"},
            ],
            "voice_channels": [
                {"id": DEFAULT_CHANNELS["voice"], "name": "General"},
            ],
            "roles": [
                {"id": DEFAULT_GM_ROLE_ID, "name": "Game Master"},
            ],
            "members": [
                {
                    "id": DEFAULT_GM_USER_ID,
                    "name": "FakeGM",
                    "roles": [DEFAULT_GM_ROLE_ID],
                },
                {"id": DEFAULT_PLAYER_USER_ID, "name": "FakePlayer", "roles": []},
            ],
        },
        {
            "id": ONBOARDING_GUILD_ID,
            "name": "Onboarding Guild",
            "owner_id": DEFAULT_GM_USER_ID,
            "text_channels": [
                {"id": ONBOARDING_CHANNELS["general"], "name": "general"},
            ],
            "voice_channels": [
                {"id": ONBOARDING_CHANNELS["voice"], "name": "General"},
            ],
            "roles": [],
            "members": [
                {"id": DEFAULT_GM_USER_ID, "name": "FakeGM", "roles": []},
            ],
        },
    ]
}


def enabled() -> bool:
    flag = os.environ.get("FAKE_DISCORD", "").lower()
    if flag in _TRUTHY:
        return True
    if flag in _FALSY:
        return False
    return not os.environ.get("DISCORD_TOKEN")


def _oronder_hq_guild() -> dict:
    """The Oronder support server: subscription checks resolve roles on it."""
    from utils import beta_tester_role_id, oronder_server_id, supporter_role_id

    return {
        "id": oronder_server_id,
        "name": "Oronder HQ",
        "owner_id": DEFAULT_GM_USER_ID,
        "text_channels": [{"id": oronder_server_id + 1, "name": "general"}],
        "voice_channels": [],
        "roles": [
            {"id": supporter_role_id, "name": "Supporter"},
            {"id": beta_tester_role_id, "name": "Beta Tester"},
        ],
        "members": [{"id": DEFAULT_GM_USER_ID, "name": "FakeGM", "roles": []}],
    }


def load_seed() -> dict:
    path = os.environ.get("FAKE_DISCORD_SEED")
    if path:
        with open(path, encoding="utf-8") as f:
            seed = json.load(f)
    else:
        seed = json.loads(json.dumps(DEFAULT_SEED))  # deep copy

    from utils import oronder_server_id

    if not any(
        int(g["id"]) == oronder_server_id for g in seed.get("guilds", [])
    ):
        seed.setdefault("guilds", []).append(_oronder_hq_guild())
    return seed


_snowflakes = itertools.count(990001000000000000)


def next_snowflake() -> int:
    return next(_snowflakes)


def _user_payload(user_id: int, name: str, bot: bool = False) -> dict:
    return {
        "id": str(user_id),
        "username": name,
        "discriminator": "0",
        "global_name": name,
        "avatar": None,
        "bot": bot,
    }


def _member_payload(user_id: int, name: str, roles: list, bot: bool = False) -> dict:
    return {
        "user": _user_payload(user_id, name, bot),
        "nick": None,
        "roles": [str(r) for r in roles],
        "joined_at": "2024-01-01T00:00:00+00:00",
        "deaf": False,
        "mute": False,
        "flags": 0,
    }


def _role_payload(role_id: int, name: str, **extra) -> dict:
    payload = {
        "id": str(role_id),
        "name": name,
        "permissions": str(0x8),  # administrator
        "position": 1,
        "color": 0,
        "colors": {"primary_color": 0, "secondary_color": None, "tertiary_color": None},
        "hoist": False,
        "managed": False,
        "mentionable": True,
        "flags": 0,
    }
    payload.update(extra)
    return payload


def _channel_payload(channel_id: int, name: str, channel_type: int) -> dict:
    return {
        "id": str(channel_id),
        "name": name,
        "type": channel_type,
        "position": 0,
        "permission_overwrites": [],
        "parent_id": None,
        "nsfw": False,
        "rate_limit_per_user": 0,
        "topic": None,
        "last_message_id": None,
    }


def guild_create_payload(seed_guild: dict) -> dict:
    guild_id = int(seed_guild["id"])
    channels = [
        _channel_payload(int(c["id"]), c["name"], 0)
        for c in seed_guild.get("text_channels", [])
    ] + [
        _channel_payload(int(c["id"]), c["name"], 2)
        for c in seed_guild.get("voice_channels", [])
    ]
    roles = [
        # @everyone role shares the guild id. Administrator permissions keep
        # check_permissions() quiet in tests.
        _role_payload(guild_id, "@everyone", position=0),
        _role_payload(
            next_snowflake(),
            "Oronder",
            managed=True,
            tags={"bot_id": str(FAKE_BOT_USER_ID)},
        ),
        *[_role_payload(int(r["id"]), r["name"]) for r in seed_guild.get("roles", [])],
    ]
    members = [
        _member_payload(FAKE_BOT_USER_ID, "Oronder", [], bot=True),
        *[
            _member_payload(int(m["id"]), m["name"], m.get("roles", []))
            for m in seed_guild.get("members", [])
        ],
    ]
    return {
        "id": str(guild_id),
        "name": seed_guild.get("name", f"Guild {guild_id}"),
        "owner_id": str(seed_guild.get("owner_id", members[-1]["user"]["id"])),
        "icon": None,
        "splash": None,
        "discovery_splash": None,
        "afk_channel_id": None,
        "afk_timeout": 300,
        "verification_level": 0,
        "default_message_notifications": 0,
        "explicit_content_filter": 0,
        "features": [],
        "mfa_level": 0,
        "application_id": None,
        "system_channel_id": None,
        "system_channel_flags": 0,
        "rules_channel_id": None,
        "vanity_url_code": None,
        "description": None,
        "banner": None,
        "premium_tier": 0,
        "premium_subscription_count": 0,
        "preferred_locale": "en-US",
        "public_updates_channel_id": None,
        "nsfw_level": 0,
        "premium_progress_bar_enabled": False,
        "stickers": [],
        "emojis": [],
        "roles": roles,
        "channels": channels,
        "threads": [],
        "members": members,
        "member_count": len(members),
        "large": False,
        "unavailable": False,
        "voice_states": [],
        "presences": [],
        "guild_scheduled_events": seed_guild.get("scheduled_events", []),
        "joined_at": "2024-01-01T00:00:00+00:00",
        "max_members": 500000,
        "max_presences": None,
    }


def _make_fake_http_base():
    from discord.http import HTTPClient

    return HTTPClient


class FakeHTTPClient(_make_fake_http_base()):
    """discord.http.HTTPClient that never talks to Discord.

    Subclassing keeps every typed helper (send_message, start_private_message,
    create_guild_scheduled_event, ...) — they all funnel into request(),
    which records the call and answers with a canned payload for the routes
    the app actually exercises. Unknown routes get an empty dict, which
    py-cord tolerates for the fire-and-forget calls used here.
    """

    def __init__(self, user_payload: dict):
        super().__init__()
        self.requests: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self._user_payload = user_payload
        self.token = "fake-token"

    # --- recorder -----------------------------------------------------
    def record(self, route, kwargs) -> dict:
        entry = {
            "method": route.method,
            "path": route.path,
            "url": route.url,
            "json": kwargs.get("json"),
            "params": kwargs.get("params"),
        }
        self.requests.append(entry)
        return entry

    def reset(self):
        self.requests.clear()
        self.messages.clear()

    # --- canned responses ---------------------------------------------
    def _message_payload(self, channel_id: str, body: dict | None) -> dict:
        body = body or {}
        payload = {
            "id": str(next_snowflake()),
            "channel_id": str(channel_id),
            "author": self._user_payload,
            "content": body.get("content") or "",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "edited_timestamp": None,
            "tts": False,
            "mention_everyone": False,
            "mentions": [],
            "mention_roles": [],
            "attachments": [],
            "embeds": body.get("embeds", []),
            "pinned": False,
            "type": 0,
            "flags": 0,
        }
        if body.get("message_reference"):
            payload["message_reference"] = body["message_reference"]
        return payload

    async def request(self, route, **kwargs):
        entry = self.record(route, kwargs)
        method, path = route.method, route.path
        body = kwargs.get("json")

        if method == "POST" and path.endswith("/messages"):
            channel_id = getattr(route, "channel_id", None) or entry["url"].split(
                "/channels/"
            )[1].split("/")[0]
            message = self._message_payload(channel_id, body)
            self.messages.append(
                {
                    "channel_id": str(channel_id),
                    "content": message["content"],
                    "embeds": message["embeds"],
                }
            )
            return message

        if method == "POST" and path == "/users/@me/channels":
            recipient_id = (body or {}).get("recipient_id", str(next_snowflake()))
            return {
                "id": str(next_snowflake()),
                "type": 1,
                "recipients": [_user_payload(int(recipient_id), "dm-recipient")],
                "last_message_id": None,
            }

        if method == "POST" and "/scheduled-events" in path:
            event = dict(body or {})
            event.update(
                {
                    "id": str(next_snowflake()),
                    "guild_id": str(getattr(route, "guild_id", 0)),
                    "creator_id": self._user_payload["id"],
                    "status": 1,
                    "entity_id": None,
                    "user_count": 0,
                }
            )
            event.setdefault("entity_metadata", None)
            event.setdefault("description", None)
            event.setdefault("scheduled_end_time", None)
            return event

        if method == "PATCH" and "/scheduled-events/" in path:
            event_id = path.rsplit("/", 1)[-1]
            event = dict(body or {})
            event.update(
                {
                    "id": str(event_id),
                    "guild_id": str(getattr(route, "guild_id", 0)),
                    "creator_id": self._user_payload["id"],
                    "entity_id": None,
                    "user_count": 0,
                }
            )
            event.setdefault("status", 1)
            event.setdefault("name", "event")
            event.setdefault("entity_metadata", None)
            event.setdefault("scheduled_start_time", "2024-01-01T00:00:00+00:00")
            event.setdefault("scheduled_end_time", None)
            return event

        if method == "POST" and path.endswith("/crosspost"):
            return self._message_payload(getattr(route, "channel_id", 0), {})

        logger.debug(f"FakeHTTPClient unhandled route {method} {path}")
        return {}

    async def close(self):
        return None

    async def get_from_cdn(self, url):  # avatars etc.
        return b""


def install(bot) -> FakeHTTPClient:
    """Wire a py-cord Bot for offline use: fake HTTP, self user, guilds."""
    from discord.user import ClientUser

    state = bot._connection
    user_payload = _user_payload(FAKE_BOT_USER_ID, "Oronder", bot=True)
    fake_http = FakeHTTPClient(user_payload)

    # Replace the HTTP layer everywhere py-cord keeps a reference.
    bot.http = fake_http
    state.http = fake_http

    state.user = ClientUser(state=state, data=user_payload)
    state.application_id = FAKE_BOT_USER_ID
    state.application_flags = 0

    for seed_guild in load_seed().get("guilds", []):
        state._add_guild_from_data(guild_create_payload(seed_guild))

    bot.fake_http = fake_http
    logger.critical(
        f"FAKE DISCORD ENABLED | guilds: {[g.id for g in bot.guilds]}"
    )
    return fake_http


def mark_ready(bot):
    """Complete the fake 'login': unblock wait_until_ready and fire on_ready."""
    bot._ready.set()
    bot.dispatch("ready")
