"""CoC7 game-system tests: actor round-trip (characteristics/attribs live in
the abilities/attributes JSONB columns), socket roll vocabulary (SYSTEMS.md
contract 2), local d100 fallbacks, and the d100 roller itself."""

import re

import fake_discord
from harness import seed
from systems.coc7 import roll_d100, success_level

# dedicated guild with roll_discord_to_foundry=False so fallback tests can
# never race a sim_foundry client connected for the default guild
COC7_FALLBACK_GUILD_ID = 990000000000000902
COC7_FALLBACK_AUTH = "coc7-fallback-token"

RESULT_RE = re.compile(r"^\d+ / rolled \d+: .+")


class SeqRng:
    """Deterministic randint source for d100 tests."""

    def __init__(self, seq):
        self.seq = list(seq)

    def randint(self, a, b):
        return self.seq.pop(0)


async def test_actor_put_delete_roundtrip(api, seeded_guild):
    payload = seed.coc7_actor_payload(
        actor_id="coc7put000000001", name="CoC7 Put Hero"
    )
    headers = {"Authorization": seeded_guild.auth_token}

    r = await api.put("/actor", json=payload, headers=headers)
    assert r.status_code == 200, r.text

    # characteristics land in the abilities column, attribs in attributes
    from database import Session
    from database.actor_table import ActorTable
    from systems import validate_actor_row
    from systems.coc7 import CoC7Actor

    with Session() as session:
        row = session.get(
            ActorTable, {"id": "coc7put000000001", "guild_id": seeded_guild.id}
        )
        assert row is not None
        assert row.world["system"] == "CoC7"
        assert row.abilities["str"]["value"] == 60
        assert row.attributes["san"]["value"] == 55
        assert row.attributes["db"] == "+1d4"
        actor = validate_actor_row(row)

    assert isinstance(actor, CoC7Actor)
    assert actor.abilities["pow"].value == 60
    assert actor.attrib_value("lck") == 45
    assert [s.name for s in actor.skills] == ["Spot Hidden", "Firearms (Handgun)"]
    assert actor.desc_string() == "Private Investigator, age 42"

    r = await api.delete("/actor/coc7put000000001", headers=headers)
    assert r.status_code == 200, r.text

    r = await api.delete("/actor/coc7put000000001", headers=headers)
    assert r.status_code == 404


async def test_roll_socket_payload_vocabulary(api, sim_foundry, seeded_guild):
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id="coc7roll00000001", name="CoC7 Roller")
    )

    for stat, expected_type, expected_stat in [
        ("Spot Hidden", "skill", "Spot Hidden"),
        ("STR", "characteristic", "str"),
        ("Sanity", "attribute", "san"),
        ("Luck", "attribute", "lck"),
    ]:
        r = await api.post(
            "/testing/roll",
            json={
                "guild_id": fake_discord.DEFAULT_GUILD_ID,
                "discord_id": fake_discord.DEFAULT_GM_USER_ID,
                "character": "CoC7 Roller",
                "stat": stat,
                "advantage": "Advantage" if stat == "STR" else None,
            },
        )
        assert r.status_code == 200, r.text
        assert sim_foundry.received["roll"], f"no roll request for {stat}"
        roll_req = sim_foundry.received["roll"][-1]
        assert roll_req["type"] == expected_type, stat
        assert roll_req["stat"] == expected_stat, stat
        assert roll_req["actor_id"] == "coc7roll00000001"
        if stat == "STR":
            assert roll_req["advantage"] == "Advantage"


async def test_attack_socket_payload(api, sim_foundry, seeded_guild):
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id="coc7atk000000001", name="CoC7 Attacker")
    )

    r = await api.post(
        "/testing/attack",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "CoC7 Attacker",
            "attack": ".38 Revolver",
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
    assert roll_req["item_id"] == "coc7weapon000001"
    assert roll_req["stat"] == "Firearms (Handgun)"
    assert roll_req["actor_id"] == "coc7atk000000001"


async def test_local_fallback_roll(api, app_server):
    seed.guild_settings(
        guild_id=COC7_FALLBACK_GUILD_ID,
        auth_token=COC7_FALLBACK_AUTH,
        roll_discord_to_foundry=False,
    )
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id="coc7fall00000001", name="CoC7 Fallback"),
        guild_id=COC7_FALLBACK_GUILD_ID,
    )

    for stat, field_name, value in [
        ("Spot Hidden", "Spot Hidden Check", 65),
        ("STR", "STR Check", 60),
        ("Sanity", "Sanity Check", 55),
    ]:
        r = await api.post(
            "/testing/roll",
            json={
                "guild_id": COC7_FALLBACK_GUILD_ID,
                "discord_id": fake_discord.DEFAULT_GM_USER_ID,
                "character": "CoC7 Fallback",
                "stat": stat,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["responses"], body
        field = body["responses"][0]["embeds"][0]["fields"][0]
        assert field["name"] == field_name
        assert RESULT_RE.match(field["value"]), field["value"]
        assert field["value"].startswith(f"{value} / rolled ")


async def test_local_fallback_attack(api, app_server):
    seed.guild_settings(
        guild_id=COC7_FALLBACK_GUILD_ID,
        auth_token=COC7_FALLBACK_AUTH,
        roll_discord_to_foundry=False,
    )
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id="coc7fall00000002", name="CoC7 Fallback Atk"),
        guild_id=COC7_FALLBACK_GUILD_ID,
    )

    r = await api.post(
        "/testing/attack",
        json={
            "guild_id": COC7_FALLBACK_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": "CoC7 Fallback Atk",
            "attack": ".38 Revolver",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], body
    embed = body["responses"][0]["embeds"][0]
    assert embed["title"] == ".38 Revolver"
    # d100 vs the weapon's skill value (50)
    assert embed["fields"][0]["value"].startswith("50 / rolled ")


def test_success_levels():
    # critical on 01, always
    assert success_level(1, 60) == "Critical success"
    assert success_level(1, 5) == "Critical success"
    # extreme = value / 5 (floor)
    assert success_level(12, 60) == "Extreme success"
    assert success_level(13, 60) == "Hard success"
    assert success_level(10, 50) == "Extreme success"
    assert success_level(11, 50) == "Hard success"
    # hard = value / 2 (floor)
    assert success_level(30, 60) == "Hard success"
    assert success_level(31, 60) == "Regular success"
    assert success_level(25, 50) == "Hard success"
    assert success_level(26, 50) == "Regular success"
    # regular up to the value itself
    assert success_level(60, 60) == "Regular success"
    assert success_level(61, 60) == "Failure"
    # fumble on 96-100 when value < 50, else only on 100
    assert success_level(96, 40) == "Fumble"
    assert success_level(100, 40) == "Fumble"
    assert success_level(95, 40) == "Failure"
    assert success_level(96, 50) == "Failure"
    assert success_level(99, 60) == "Failure"
    assert success_level(100, 50) == "Fumble"
    assert success_level(100, 60) == "Fumble"


def test_roll_d100_plain_and_zero_maps_to_100():
    total, tens, units = roll_d100(None, SeqRng([5, 3]))
    assert (total, tens, units) == (35, [3], 5)

    total, _, _ = roll_d100(None, SeqRng([0, 0]))
    assert total == 100


def test_check_result_exposes_hard_and_extreme_thresholds():
    from systems.coc7 import check_result

    result = check_result(65)
    assert RESULT_RE.match(result), result
    assert result.startswith("65 / rolled ")
    assert result.endswith("(Hard 32, Extreme 13)")


def test_markdown_sheet():
    from systems import get_system

    system = get_system("CoC7")
    actor = system.parse_actor(seed.coc7_actor_payload())
    sheet = system.summary_text(actor)

    # header from desc_string
    assert "**Private Investigator, age 42**" in sheet
    # characteristics table with half/fifth thresholds
    assert "**Characteristics** — value (half/fifth)" in sheet
    assert "STR  60 (30/12)" in sheet
    assert "EDU  80 (40/16)" in sheet
    # pools and derived stats
    assert "HP **12/12** • SAN **55/99** • MP **12/12** • Luck **45**" in sheet
    assert "Damage Bonus **+1d4** • Build **1** • Move **8**" in sheet
    # skills sorted by value desc, with half/fifth
    assert "Spot Hidden 65 (32/13)" in sheet
    assert "Firearms (Handgun) 50 (25/10)" in sheet
    assert sheet.index("Spot Hidden 65") < sheet.index("Firearms (Handgun) 50 (")
    # weapons with skill value and damage
    assert ".38 Revolver — Firearms (Handgun) 50, damage 1d10" in sheet
    assert len(sheet) <= 4096


def test_markdown_sheet_sparse_payload():
    from systems import get_system

    system = get_system("CoC7")
    actor = system.parse_actor({"id": "coc7sparse000001", "name": "Sparse"})
    sheet = system.summary_text(actor)

    assert "**Investigator**" in sheet
    assert "Characteristics" not in sheet
    assert "Skills" not in sheet
    assert "Weapons" not in sheet
    assert len(sheet) <= 4096


def test_markdown_sheet_truncates_to_embed_limit():
    from systems import get_system

    payload = seed.coc7_actor_payload()
    payload["skills"] = [
        {"id": f"skill{i:011d}", "name": f"Obscure Occult Discipline {i:03d}", "value": 90 - i % 60}
        for i in range(400)
    ]
    system = get_system("CoC7")
    sheet = system.summary_text(system.parse_actor(payload))

    assert len(sheet) <= 4096
    assert "… and" in sheet
    # fixed sections survive truncation
    assert "**Characteristics**" in sheet
    assert ".38 Revolver — Firearms (Handgun) 50, damage 1d10" in sheet


def test_roll_d100_bonus_and_penalty_dice():
    # bonus die: extra tens die, keep the lowest total
    total, tens, units = roll_d100("Advantage", SeqRng([5, 3, 7]))
    assert tens == [3, 7]
    assert total == 35

    # penalty die: extra tens die, keep the highest total
    total, tens, units = roll_d100("Disadvantage", SeqRng([5, 3, 7]))
    assert total == 75

    # 00 tens + 0 units reads as 100, so the bonus die keeps the lower 40
    total, _, _ = roll_d100("Advantage", SeqRng([0, 0, 4]))
    assert total == 40
