"""End-to-end with REAL premade characters from the systems' own compendia.

Instead of minimal hand-built actors, these tests import fully-equipped
characters shipped by each game system (dnd5e Starter Heroes, pf2e
Iconics, CoC7 Examples), sync them through the module, and drive rolls,
attacks and sheet rendering against the real data.
"""

import asyncio
import json

import fake_discord
from database import Session
from database.actor_table import ActorTable

IMPORT_JS = """async (cfg) => {
    const pack = game.packs.get(cfg.pack);
    if (!pack) return {error: 'pack not found: ' + cfg.pack + ' (have: '
        + [...game.packs.keys()].filter(k => k.includes('.')).slice(0, 40).join(', ') + ')'};
    const index = await pack.getIndex();
    const entries = index.filter(e => e.name === cfg.name);
    if (!entries.length) return {error: 'no index entry named ' + cfg.name};
    const docs = [];
    for (const e of entries) docs.push(await pack.getDocument(e._id));
    // Multiple entries share a name at different levels; take the richest.
    docs.sort((a, b) => b.items.size - a.items.size);
    const src = docs[0];

    const existing = game.actors.getName(cfg.newName);
    if (existing) await existing.delete();

    const data = src.toObject();
    data.name = cfg.newName;
    delete data._id;
    const actor = await Actor.create(data);
    if (cfg.set) await actor.update(cfg.set);
    return {
        id: actor.id,
        items: actor.items.size,
        types: [...new Set(actor.items.map(i => i.type))],
    };
}"""

TRIGGER_SYNC_JS = """async (id) => {
    // A rename is a substantive, non-skippable update on every system.
    const actor = game.actors.get(id);
    await actor.update({name: actor.name + '!'});
    await actor.update({name: actor.name.slice(0, -1)});
    return actor.name;
}"""

PREMADE = {
    "dnd5e": {
        "pack": "dnd5e.actors24",
        "name": "Akra",
        "new_name": "Premade Akra",
        "roll_stat": "Religion",
        "row_checks": {
            "race": "Dragonborn",
            "min_weapons": 1,
        },
    },
    "pf2e": {
        "pack": "pf2e.iconics",
        "name": "Seelah (Level 5)",
        "new_name": "Premade Seelah",
        "roll_stat": "Athletics",
        "row_checks": {
            "level": 5,
            "weapon_named": "Longsword",
            "min_weapons": 1,
        },
    },
    "CoC7": {
        "pack": "CoC7.examples",
        "name": "1920 Character",
        "new_name": "Premade Investigator",
        # The example ships without an occupation; the module requires one.
        "set": {"system.infos.occupation": "Antiquarian"},
        "roll_stat": "Spot Hidden",
        "row_checks": {
            "min_skills": 30,
        },
    },
}


async def _import_premade(foundry_gm):
    system_id = await foundry_gm.system_id()
    assert system_id in PREMADE, f"no premade config for {system_id}"
    cfg = PREMADE[system_id]

    await foundry_gm.set_id_map({"Gamemaster": str(fake_discord.DEFAULT_GM_USER_ID)})
    out = await foundry_gm.page.evaluate(
        IMPORT_JS,
        {
            "pack": cfg["pack"],
            "name": cfg["name"],
            "newName": cfg["new_name"],
            "set": cfg.get("set"),
        },
    )
    assert "error" not in out, out
    assert out["items"] > 10, f"expected a fully-equipped character: {out}"
    await foundry_gm.page.evaluate(TRIGGER_SYNC_JS, out["id"])
    return system_id, cfg, out["id"]


async def _wait_for_row(actor_id, guild_id, name, timeout=60):
    row = None
    for _ in range(timeout):
        with Session() as session:
            row = (
                session.query(ActorTable)
                .filter_by(id=actor_id, guild_id=guild_id)
                .one_or_none()
            )
        if row and row.name == name:
            return row
        await asyncio.sleep(1)
    raise AssertionError(f"premade actor {name} never synced (row={row})")


async def test_premade_character_syncs_with_real_data(foundry_gm, api, seeded_guild):
    system_id, cfg, actor_id = await _import_premade(foundry_gm)
    row = await _wait_for_row(actor_id, seeded_guild.id, cfg["new_name"])

    assert row.world["system"] == system_id
    checks = cfg["row_checks"]
    weapons = row.weapons or []
    if "min_weapons" in checks:
        assert len(weapons) >= checks["min_weapons"], weapons
    if "weapon_named" in checks:
        # Substring match: premade gear carries rune/material prefixes,
        # e.g. Seelah's "+1 Striking Longsword".
        assert any(checks["weapon_named"] in w.get("name", "") for w in weapons), (
            weapons
        )
    if "race" in checks:
        assert row.details.get("race") == checks["race"], row.details
    if "level" in checks:
        assert row.details.get("level") == checks["level"], row.details
    if "min_skills" in checks:
        assert len(row.skills or []) >= checks["min_skills"], len(row.skills or [])


async def test_premade_character_sheet_renders(foundry_gm, api, seeded_guild):
    cfg = PREMADE[await foundry_gm.system_id()]
    r = await api.post(
        "/testing/lookup_character",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": cfg["new_name"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], body
    embed = body["responses"][0]["embeds"][0]
    blob = json.dumps(embed)
    assert cfg["new_name"] in blob or (embed.get("description") or ""), embed
    # A real character sheet has substance.
    assert len(embed.get("description") or "") + sum(
        len(f.get("value") or "") for f in embed.get("fields", [])
    ) > 200, embed


async def test_premade_character_roll(foundry_gm, api, seeded_guild):
    cfg = PREMADE[await foundry_gm.system_id()]
    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": cfg["new_name"],
            "stat": cfg["roll_stat"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], f"no response: {json.dumps(body)}"
    assert body["responses"][0]["embeds"], f"no embed: {json.dumps(body)}"
    field = body["responses"][0]["embeds"][0]["fields"][0]
    assert cfg["roll_stat"].lower() in field["name"].lower(), field
    assert any(ch.isdigit() for ch in field["value"]), field


async def test_premade_character_attack(foundry_gm, api, seeded_guild):
    cfg = PREMADE[await foundry_gm.system_id()]
    # Attack with whatever the synced row says the character carries.
    with Session() as session:
        row = (
            session.query(ActorTable)
            .filter_by(guild_id=fake_discord.DEFAULT_GUILD_ID)
            .filter(ActorTable.name == cfg["new_name"])
            .one()
        )
    weapons = row.weapons or []
    assert weapons, "premade character synced without weapons"
    wanted = cfg.get("row_checks", {}).get("weapon_named")
    weapon_name = next(
        (w["name"] for w in weapons if wanted and wanted in w["name"]),
        weapons[0]["name"],
    )

    r = await api.post(
        "/testing/attack",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": cfg["new_name"],
            "attack": weapon_name,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], f"no response: {json.dumps(body)}"
    embed = body["responses"][0]["embeds"][0]
    assert embed.get("title") == weapon_name, embed
    assert embed.get("fields"), embed
    assert any(ch.isdigit() for ch in embed["fields"][0]["value"]), embed
