"""/lookup command coverage driven through /testing endpoints.

Content lookups (item/spell/feat/rule/background) use stable SRD entries;
/lookup character is exercised for each game system (dnd5e full sheet,
pf2e/CoC7 summary embed).
"""

import re

import fake_discord
from harness import seed


def _embed(body: dict) -> dict:
    assert body["responses"], body
    embeds = body["responses"][0]["embeds"]
    assert embeds, body
    return embeds[0]


def _field_text(embed: dict) -> str:
    return "\n".join(
        f"{f.get('name', '')} {f.get('value', '')}" for f in embed.get("fields", [])
    )


async def _lookup(api, kind: str, name: str) -> dict:
    r = await api.post(
        "/testing/lookup",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "kind": kind,
            "name": name,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_lookup_item(api, seeded_guild):
    embed = _embed(await _lookup(api, "item", "Longsword"))
    assert embed["title"] == "Longsword"
    text = _field_text(embed)
    assert "Martial" in text
    assert "15 gp" in text
    assert "1d8" in text


async def test_lookup_spell(api, seeded_guild):
    embed = _embed(await _lookup(api, "spell", "Fireball"))
    assert embed["title"] == "Fireball"
    text = _field_text(embed)
    assert "8d6" in text
    assert "evocation" in text.lower()


async def test_lookup_feat(api, seeded_guild):
    embed = _embed(await _lookup(api, "feat", "Alert"))
    assert embed["title"] == "Alert"
    assert "surprised" in _field_text(embed).lower()


async def test_lookup_rule(api, seeded_guild):
    embed = _embed(await _lookup(api, "rule", "Action: Dash"))
    assert embed["title"] == "Dash"
    assert embed["footer"]["text"].startswith("Action")
    assert "movement" in _field_text(embed).lower()


async def test_lookup_rule_unknown_is_clean_error(api, seeded_guild):
    body = await _lookup(api, "rule", "Action: Definitely Not A Rule")
    assert body["responses"], body
    response = body["responses"][0]
    assert not response["embeds"]
    assert "not found" in response["content"].lower()
    assert response["ephemeral"] is True


async def test_lookup_background(api, seeded_guild):
    embed = _embed(await _lookup(api, "background", "Soldier"))
    assert embed["title"] == "Soldier"
    assert "Athletics" in _field_text(embed)


async def _lookup_character(api, character: str) -> dict:
    r = await api.post(
        "/testing/lookup_character",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": character,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_lookup_character_dnd5e_full_sheet(api, seeded_guild):
    seed.seed_actor(
        seed.dnd5e_actor_payload(actor_id="lkchar5e00000001", name="Lookup Hero")
    )
    embed = _embed(await _lookup_character(api, "Lookup Hero"))
    assert embed["title"] == "Lookup Hero"
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["AC"] == "16"
    assert "XP" in fields
    assert "Ability Scores" in fields
    assert "Longsword" in fields  # best weapon
    assert "Gold" in embed["footer"]["text"]


async def test_lookup_character_pf2e_summary(api, seeded_guild):
    """Exact summary formatting belongs to the systems layer; assert the key
    stats are present without pinning the layout."""
    seed.seed_actor(
        seed.pf2e_actor_payload(actor_id="lkcharpf00000001", name="Lookup Pathfinder")
    )
    embed = _embed(await _lookup_character(api, "Lookup Pathfinder"))
    assert embed["title"] == "Lookup Pathfinder"
    desc = embed["description"]
    assert re.search(r"HP\D{0,4}58", desc), desc
    assert re.search(r"AC\D{0,4}24", desc), desc
    assert "Dwarf" in desc
    assert "Warhammer" in desc


async def test_lookup_character_coc7_summary(api, seeded_guild):
    seed.seed_actor(
        seed.coc7_actor_payload(actor_id="lkcharcc00000001", name="Lookup Investigator")
    )
    embed = _embed(await _lookup_character(api, "Lookup Investigator"))
    assert embed["title"] == "Lookup Investigator"
    desc = embed["description"]
    assert "Private Investigator, age 42" in desc
    assert re.search(r"STR\D{0,4}60", desc), desc
    assert "Spot Hidden 65" in desc
    assert ".38 Revolver" in desc


async def test_lookup_character_unknown_is_clean_error(api, seeded_guild):
    body = await _lookup_character(api, "Nobody At All")
    response = body["responses"][0]
    assert "not found" in response["content"].lower()
    assert response["ephemeral"] is True
