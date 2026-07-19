"""The Discord OAuth onboarding flow (/init) in fake-Discord mode.

/init normally exchanges an OAuth code with discord.com; in fake mode the
exchange is short-circuited so the whole onboarding path — mint token,
create guild settings, return the postMessage HTML the Foundry module's
popup handshake expects — runs without any Discord app.
"""

import base64

import fake_discord
from database.guild_settings_table import GuildSettingsTable


def _state(tz: str = "US/Eastern", hostname: str = "localhost:65434") -> str:
    return base64.b64encode(f"{tz}|{hostname}".encode()).decode()


async def test_init_onboards_new_guild(api):
    r = await api.get(
        "/init",
        params={
            "code": "fake-code",
            "guild_id": fake_discord.ONBOARDING_GUILD_ID,
            "state": _state(),
        },
    )
    assert r.status_code == 200, r.text
    assert "postMessage" in r.text

    guild_settings = GuildSettingsTable.lookup(fake_discord.ONBOARDING_GUILD_ID)
    assert guild_settings is not None
    assert guild_settings.auth_token
    assert guild_settings.foundry_hostname == "localhost:65434"
    assert guild_settings.timezone == "US/Eastern"
    assert guild_settings.auth_token in r.text
    assert "Onboarding Guild" in r.text

    # The minted token authenticates the Foundry-facing API.
    r = await api.get("/guild", headers={"Authorization": guild_settings.auth_token})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Onboarding Guild"


async def test_init_rotates_token_on_reauth(api):
    first = GuildSettingsTable.lookup(fake_discord.ONBOARDING_GUILD_ID)
    r = await api.get(
        "/init",
        params={
            "code": "fake-code-2",
            "guild_id": fake_discord.ONBOARDING_GUILD_ID,
            "state": _state(),
        },
    )
    assert r.status_code == 200
    second = GuildSettingsTable.lookup(fake_discord.ONBOARDING_GUILD_ID)
    assert second.auth_token != first.auth_token

    r = await api.get("/guild", headers={"Authorization": first.auth_token})
    assert r.status_code == 401


async def test_init_rejects_unknown_guild(api):
    """A guild the bot is not a member of cannot be onboarded."""
    r = await api.get(
        "/init",
        params={"code": "fake-code", "guild_id": 123456789, "state": _state()},
    )
    # /init reports errors through the postMessage HTML, not status codes.
    assert "must be a member" in r.text
