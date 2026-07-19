"""pf2e game-system tests: actor round-trip, socket roll vocabulary
(SYSTEMS.md contract 2), and local dice fallbacks."""

import fake_discord
from harness import seed

# dedicated guild with roll_discord_to_foundry=False so fallback tests can
# never race a sim_foundry client connected for the default guild
PF2E_FALLBACK_GUILD_ID = 990000000000000901
PF2E_FALLBACK_AUTH = "pf2e-fallback-token"


async def test_actor_put_delete_roundtrip(api, seeded_guild):
    payload = seed.pf2e_actor_payload(
        actor_id="pf2eput000000001", name="Pf2e Put Hero"
    )
    headers = {"Authorization": seeded_guild.auth_token}

    r = await api.put("/actor", json=payload, headers=headers)
    assert r.status_code == 200, r.text

    # row round-trips through the registry into the pf2e model
    from database import Session
    from database.actor_table import ActorTable
    from systems import validate_actor_row
    from systems.pf2e import Pf2eActor

    with Session() as session:
        row = session.get(
            ActorTable, {"id": "pf2eput000000001", "guild_id": seeded_guild.id}
        )
        assert row is not None
        assert row.world["system"] == "pf2e"
        assert row.attributes["saves"]["fortitude"]["mod"] == 9
        actor = validate_actor_row(row)

    assert isinstance(actor, Pf2eActor)
    assert actor.skills["athletics"].mod == 5
    assert actor.details.level == 5
    assert actor.desc_string() == "Dwarf Fighter 5"

    r = await api.delete("/actor/pf2eput000000001", headers=headers)
    assert r.status_code == 200, r.text

    r = await api.delete("/actor/pf2eput000000001", headers=headers)
    assert r.status_code == 404


async def test_roll_skill_socket_payload(api, sim_foundry, seeded_guild):
    seed.seed_actor(
        seed.pf2e_actor_payload(actor_id="pf2eroll00000001", name="Pf2e Skiller")
    )

    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Pf2e Skiller",
            "stat": "Athletics",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert sim_foundry.received["roll"], "Foundry never received the roll request"
    roll_req = sim_foundry.received["roll"][-1]
    assert roll_req["type"] == "skill"
    assert roll_req["stat"] == "athletics"
    assert roll_req["advantage"] is None
    assert roll_req["actor_id"] == "pf2eroll00000001"

    assert body["responses"], "no Discord response recorded"
    fields = body["responses"][0]["embeds"][0]["fields"]
    assert sim_foundry.roll_response["res"] in fields[0]["value"]


async def test_roll_save_and_perception_socket_payload(api, sim_foundry, seeded_guild):
    seed.seed_actor(
        seed.pf2e_actor_payload(actor_id="pf2eroll00000002", name="Pf2e Saver")
    )

    for stat, expected_type, expected_stat in [
        ("Fortitude", "save", "fortitude"),
        ("Perception", "perception", "perception"),
        ("Warfare Lore", "skill", "lore-warfare"),
    ]:
        r = await api.post(
            "/testing/roll",
            json={
                "guild_id": fake_discord.DEFAULT_GUILD_ID,
                "discord_id": fake_discord.DEFAULT_GM_USER_ID,
                "character": "Pf2e Saver",
                "stat": stat,
            },
        )
        assert r.status_code == 200, r.text
        roll_req = sim_foundry.received["roll"][-1]
        assert roll_req["type"] == expected_type, stat
        assert roll_req["stat"] == expected_stat, stat


async def test_attack_socket_payload(api, sim_foundry, seeded_guild):
    seed.seed_actor(
        seed.pf2e_actor_payload(actor_id="pf2eatk000000001", name="Pf2e Attacker")
    )

    r = await api.post(
        "/testing/attack",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Pf2e Attacker",
            "attack": "Warhammer",
        },
    )
    assert r.status_code == 200, r.text

    assert sim_foundry.received["roll"], "Foundry never received the attack request"
    roll_req = sim_foundry.received["roll"][-1]
    assert roll_req["type"] == "attack"

    # The {res} ack from Foundry must round-trip into the Discord embed.
    body = r.json()
    assert body["responses"], body
    embed = body["responses"][0]["embeds"][0]
    assert sim_foundry.roll_response["res"] in embed["fields"][0]["value"], embed
    assert roll_req["item_id"] == "pf2eweapon000001"
    assert roll_req["actor_id"] == "pf2eatk000000001"


async def test_local_fallback_roll(api, app_server):
    seed.guild_settings(
        guild_id=PF2E_FALLBACK_GUILD_ID,
        auth_token=PF2E_FALLBACK_AUTH,
        roll_discord_to_foundry=False,
    )
    seed.seed_actor(
        seed.pf2e_actor_payload(actor_id="pf2efall00000001", name="Pf2e Fallback"),
        guild_id=PF2E_FALLBACK_GUILD_ID,
    )

    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": PF2E_FALLBACK_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Pf2e Fallback",
            "stat": "Athletics",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], body
    field = body["responses"][0]["embeds"][0]["fields"][0]
    assert field["name"] == "Athletics Skill Check"
    assert "1d20" in field["value"]

    # saves roll d20 + the save mod
    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": PF2E_FALLBACK_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Pf2e Fallback",
            "stat": "Fortitude",
        },
    )
    field = r.json()["responses"][0]["embeds"][0]["fields"][0]
    assert field["name"] == "Fortitude Saving Throw"
    assert "1d20" in field["value"]


async def test_local_fallback_attack(api, app_server):
    seed.guild_settings(
        guild_id=PF2E_FALLBACK_GUILD_ID,
        auth_token=PF2E_FALLBACK_AUTH,
        roll_discord_to_foundry=False,
    )
    seed.seed_actor(
        seed.pf2e_actor_payload(actor_id="pf2efall00000002", name="Pf2e Fallback Atk"),
        guild_id=PF2E_FALLBACK_GUILD_ID,
    )

    r = await api.post(
        "/testing/attack",
        json={
            "guild_id": PF2E_FALLBACK_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "Pf2e Fallback Atk",
            "attack": "Warhammer",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], body
    embed = body["responses"][0]["embeds"][0]
    assert embed["title"] == "Warhammer"
    assert "1d20" in embed["fields"][0]["value"]


def test_xp_model():
    from systems import get_system

    system = get_system("pf2e")
    assert system.supports_xp is True
    assert system.get_lvl(0) == 1
    assert system.get_lvl(999) == 1
    assert system.get_lvl(1000) == 2
    assert system.get_lvl(19000) == 20
    assert system.get_lvl(1_000_000) == 20
