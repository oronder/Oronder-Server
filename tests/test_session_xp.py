"""Mission / session XP lifecycle, driven entirely server-side.

/testing/session_start and /testing/session_stop call
SocketNamespace.start_stop_session with payloads shaped like
Events.on_scheduled_event_update, and 'xp' events are emitted over
socket.io exactly like the module's monks-token-bar integration
(id_to_xp is a list of [actor_id, xp] pairs; the server sums the first
pair of each event for the session total).
"""

import asyncio
import uuid

import fake_discord
from conftest import FOUNDRY_ORIGIN
from harness import seed
from models.systems import System

XP_GUILD_AUTH = "xp-guild-token"


async def _wait_for(predicate, timeout: float = 8.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


def _namespace():
    import discord_client

    return discord_client.bot.socket_namespace


async def _emit_xp_events(client, namespace, guild_id, mission_id, id_to_xp, count=2):
    """Emit `count` xp events and wait until the server recorded them all."""
    for _ in range(count):
        await client.emit("xp", {"session_id": mission_id, "id_to_xp": id_to_xp})
    assert await _wait_for(
        lambda: len(
            namespace.guilds_to_missions_to_xp.get(guild_id, {}).get(mission_id, [])
        )
        == count
    ), "server never recorded the xp events"


async def test_session_xp_full_loop(api, sim_foundry, seeded_guild):
    """start -> xp events -> stop: summary + level-up in channel, push to Foundry."""
    actor_id = uuid.uuid4().hex[:16]
    seed.seed_actor(seed.dnd5e_actor_payload(actor_id=actor_id, name="XP Loop Hero"))
    mission = seed.mission(title="XP Loop Mission", pcs=[actor_id])

    await api.post("/testing/reset")
    r = await api.post(
        "/testing/session_start",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "mission_id": mission.id,
            "name": mission.title,
        },
    )
    assert r.status_code == 200, r.text
    assert await _wait_for(
        lambda: any(
            p.get("id") == mission.id and p.get("status") == "start"
            for p in sim_foundry.received["session"]
        )
    ), "session start never reached the (sim) Foundry client"

    # 150 XP twice -> 300 total, which crosses the dnd5e level 2 threshold
    await _emit_xp_events(
        sim_foundry,
        _namespace(),
        fake_discord.DEFAULT_GUILD_ID,
        mission.id,
        [[actor_id, 150]],
    )

    r = await api.post(
        "/testing/session_stop",
        json={"guild_id": fake_discord.DEFAULT_GUILD_ID, "mission_id": mission.id},
    )
    assert r.status_code == 200, r.text

    messages = (await api.get("/testing/messages")).json()
    xp_msgs = [m for m in messages if "rewarded" in (m["content"] or "")]
    assert len(xp_msgs) == 1, messages
    msg = xp_msgs[0]
    assert msg["channel_id"] == str(fake_discord.DEFAULT_CHANNELS["general"])
    assert "300 XP" in msg["content"]
    assert "XP Loop Mission" in msg["content"]
    assert "**XP Loop Hero**: 1 -> 2" in msg["content"]

    # the mission row now carries the session XP
    from database import Session
    from database.missions import MissionTable

    with Session() as session:
        assert session.get(MissionTable, mission.id).xp == 300

    # and the total XP was pushed back to the connected Foundry world
    assert await _wait_for(lambda: sim_foundry.received["xp"])
    assert sim_foundry.received["xp"][0] == {actor_id: 300}


async def test_session_stop_coc7_only_skips_xp(api, sim_foundry, seeded_guild):
    """A CoC7-only mission has no XP-tracking actors: stop must no-op cleanly."""
    actor_id = uuid.uuid4().hex[:16]
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id=actor_id, name="XP Investigator")
    )
    mission = seed.mission(
        title="CoC7 Only Mission", pcs=[actor_id], system=System.CoC7.value
    )

    await api.post("/testing/reset")
    r = await api.post(
        "/testing/session_start",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "mission_id": mission.id,
            "name": mission.title,
        },
    )
    assert r.status_code == 200, r.text
    await _emit_xp_events(
        sim_foundry,
        _namespace(),
        fake_discord.DEFAULT_GUILD_ID,
        mission.id,
        [[actor_id, 100]],
    )

    r = await api.post(
        "/testing/session_stop",
        json={"guild_id": fake_discord.DEFAULT_GUILD_ID, "mission_id": mission.id},
    )
    assert r.status_code == 200, r.text

    await asyncio.sleep(0.3)  # allow any (wrong) async sends to surface
    messages = (await api.get("/testing/messages")).json()
    assert not [m for m in messages if "rewarded" in (m["content"] or "")], messages
    assert not sim_foundry.received["xp"]

    # the mission row must not have been rewarded either
    from database import Session
    from database.missions import MissionTable

    with Session() as session:
        assert session.get(MissionTable, mission.id).xp is None


async def test_session_mixed_system_rewards_only_xp_actors(
    api, sim_foundry, seeded_guild
):
    """dnd5e actor gets XP, CoC7 actor is skipped in message and push."""
    dnd_id = uuid.uuid4().hex[:16]
    coc_id = uuid.uuid4().hex[:16]
    seed.seed_actor(seed.dnd5e_actor_payload(actor_id=dnd_id, name="XP Mix Hero"))
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id=coc_id, name="XP Mix Investigator")
    )
    mission = seed.mission(title="Mixed System Mission", pcs=[dnd_id, coc_id])

    await api.post("/testing/reset")
    r = await api.post(
        "/testing/session_start",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "mission_id": mission.id,
            "name": mission.title,
        },
    )
    assert r.status_code == 200, r.text
    await _emit_xp_events(
        sim_foundry,
        _namespace(),
        fake_discord.DEFAULT_GUILD_ID,
        mission.id,
        [[dnd_id, 150], [coc_id, 150]],
    )

    r = await api.post(
        "/testing/session_stop",
        json={"guild_id": fake_discord.DEFAULT_GUILD_ID, "mission_id": mission.id},
    )
    assert r.status_code == 200, r.text

    messages = (await api.get("/testing/messages")).json()
    xp_msgs = [m for m in messages if "rewarded" in (m["content"] or "")]
    assert len(xp_msgs) == 1, messages
    assert "**XP Mix Hero**: 1 -> 2" in xp_msgs[0]["content"]
    assert "Investigator" not in xp_msgs[0]["content"]

    assert await _wait_for(lambda: sim_foundry.received["xp"])
    assert sim_foundry.received["xp"][0] == {dnd_id: 300}


async def test_session_xp_pending_persists_when_foundry_offline(api, app_server):
    """If Foundry disconnects before session stop, XP lands in
    guild_settings.pending_xp and is resynced on the next connect."""
    import socketio

    from database.guild_settings_table import GuildSettingsTable

    guild_id = fake_discord.XP_GUILD_ID
    seed.guild_settings(
        guild_id=guild_id,
        auth_token=XP_GUILD_AUTH,
        session_channel_id=fake_discord.XP_CHANNELS["general"],
        downtime_channel_id=fake_discord.XP_CHANNELS["general"],
        scheduling_channel_id=fake_discord.XP_CHANNELS["general"],
        combat_channel_id=fake_discord.XP_CHANNELS["general"],
        voice_channel_id=fake_discord.XP_CHANNELS["voice"],
    )
    actor_id = uuid.uuid4().hex[:16]
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id=actor_id, name="XP Pending Hero"),
        guild_id=guild_id,
    )
    mission = seed.mission(
        guild_id=guild_id,
        title="Pending XP Mission",
        pcs=[actor_id],
        channel_or_thread_id=fake_discord.XP_CHANNELS["general"],
    )

    def connect_client():
        client = socketio.AsyncClient()
        client.received_xp = []

        @client.on("xp")
        async def on_xp(data):
            client.received_xp.append(data)

        return client

    await api.post("/testing/reset")
    client = connect_client()
    await client.connect(
        app_server,
        transports=["websocket"],
        auth={"Authorization": XP_GUILD_AUTH},
        headers={"Origin": FOUNDRY_ORIGIN},
    )
    try:
        r = await api.post(
            "/testing/session_start",
            json={
                "guild_id": guild_id,
                "mission_id": mission.id,
                "name": mission.title,
            },
        )
        assert r.status_code == 200, r.text
        await _emit_xp_events(
            client, _namespace(), guild_id, mission.id, [[actor_id, 150]]
        )
    finally:
        await client.disconnect()

    # wait for the server to process the disconnect: no sids left
    assert await _wait_for(lambda: not _namespace().guilds_to_sids.get(guild_id))

    r = await api.post(
        "/testing/session_stop",
        json={"guild_id": guild_id, "mission_id": mission.id},
    )
    assert r.status_code == 200, r.text

    # XP message still lands in the mission channel
    messages = (await api.get("/testing/messages")).json()
    xp_msgs = [m for m in messages if "rewarded" in (m["content"] or "")]
    assert len(xp_msgs) == 1, messages
    assert xp_msgs[0]["channel_id"] == str(fake_discord.XP_CHANNELS["general"])

    # and the XP persisted for the offline world
    assert GuildSettingsTable.lookup(guild_id).pending_xp == {actor_id: 300}

    # reconnecting resyncs and clears the pending XP
    client = connect_client()
    await client.connect(
        app_server,
        transports=["websocket"],
        auth={"Authorization": XP_GUILD_AUTH},
        headers={"Origin": FOUNDRY_ORIGIN},
    )
    try:
        assert await _wait_for(lambda: client.received_xp)
        assert client.received_xp[0] == {actor_id: 300}
    finally:
        await client.disconnect()
    assert await _wait_for(
        lambda: GuildSettingsTable.lookup(guild_id).pending_xp is None
    )
