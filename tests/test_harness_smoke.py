"""Smoke tests: the whole server runs without Discord and serves both the
Foundry-facing surface (REST + socket.io) and the fake Discord side."""

import asyncio

import fake_discord
from harness import seed


async def test_heartbeat(api):
    r = await api.get("/zqaBTpcyxNdiS2uRjC0pl7WP9snUPkZy")
    assert r.status_code == 200


async def test_health_reports_fake_guild(api):
    r = await api.get("/testing/health")
    assert r.status_code == 200
    body = r.json()
    assert body["fake_discord"] is True
    assert any(g["id"] == fake_discord.DEFAULT_GUILD_ID for g in body["guilds"])


async def test_actor_upsert_and_delete(api, seeded_guild):
    payload = seed.dnd5e_actor_payload(actor_id="smokeactor000001", name="Smoke Hero")
    headers = {"Authorization": seeded_guild.auth_token}

    r = await api.put("/actor", json=payload, headers=headers)
    assert r.status_code == 200, r.text

    r = await api.delete("/actor/smokeactor000001", headers=headers)
    assert r.status_code == 200, r.text

    r = await api.delete("/actor/smokeactor000001", headers=headers)
    assert r.status_code == 404


async def test_actor_upsert_rejects_bad_auth(api):
    payload = seed.dnd5e_actor_payload()
    r = await api.put("/actor", json=payload, headers={"Authorization": "wrong"})
    assert r.status_code == 401


async def test_guild_endpoint(api, seeded_guild):
    r = await api.get("/guild", headers={"Authorization": seeded_guild.auth_token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert int(body["id"]) == fake_discord.DEFAULT_GUILD_ID
    assert body["name"] == "Fake Guild"
    assert any(c["name"] == "combat" for c in body["text_channels"])


async def test_socket_connect_and_combat_to_discord(api, sim_foundry):
    r = await api.get("/testing/health")
    assert fake_discord.DEFAULT_GUILD_ID in r.json()["connected_foundry_guilds"]

    await api.post("/testing/reset")
    await sim_foundry.emit(
        "combat", {"title": "Round 1", "description": "The goblin lunges!"}
    )
    for _ in range(20):
        await asyncio.sleep(0.25)
        messages = (await api.get("/testing/messages")).json()
        if messages:
            break
    assert messages, "combat event never reached the fake Discord channel"
    embed = messages[0]["embeds"][0]
    assert embed["title"] == "Round 1"
    assert messages[0]["channel_id"] == str(fake_discord.DEFAULT_CHANNELS["combat"])


async def test_roll_round_trip_discord_to_foundry(api, sim_foundry, seeded_guild):
    """/roll on Discord -> server -> (sim) Foundry rolls -> Discord embed."""
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id="rolltrip00000001", name="Roll Tripper")
    )

    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Roll Tripper",
            "stat": "Athletics",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deferred"] is True

    assert sim_foundry.received["roll"], "Foundry never received the roll request"
    roll_req = sim_foundry.received["roll"][0]
    assert roll_req["type"] == "skill"
    assert roll_req["stat"] == "ath"
    assert roll_req["actor_id"] == "rolltrip00000001"

    assert body["responses"], "no Discord response recorded"
    fields = body["responses"][0]["embeds"][0]["fields"]
    assert sim_foundry.roll_response["res"] in fields[0]["value"]


async def test_roll_falls_back_to_server_side_dice(api, seeded_guild):
    """Without a Foundry connection the server rolls the dice itself."""
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id="fallback00000001", name="Fallback Fred")
    )

    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Fallback Fred",
            "stat": "Strength",
            "save": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], body
    field = body["responses"][0]["embeds"][0]["fields"][0]
    assert "Saving Throw" in field["name"]
    assert "1d20" in field["value"] or "d20" in field["value"]
