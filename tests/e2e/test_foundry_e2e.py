"""End-to-end: real Foundry VTT + Oronder module + this server, no Discord.

Run with: pytest tests/e2e -m foundry [--foundry-world=test-pf2e]
The world id decides the game system under test.
"""

import asyncio
import json

import fake_discord
from database import Session
from database.actor_table import ActorTable

# Per-system test data: how to build a syncable character in the world, and
# a representative /roll stat.
SYSTEMS = {
    "dnd5e": {
        "actor_name": "E2E Fighter",
        "create_js": """async (name) => {
            const existing = game.actors.getName(name);
            if (existing) await existing.delete();
            const actor = await Actor.create({name: name + ' WIP', type: 'character'});
            await actor.createEmbeddedDocuments('Item', [
                {name: 'Fighter', type: 'class',
                 system: {levels: 3, identifier: 'fighter'}},
                {name: 'Human', type: 'race'},
                {name: 'Soldier', type: 'background'},
            ]);
            await actor.update({
                'system.abilities.str.value': 16,
                'system.attributes.hp.max': 28,
                'system.attributes.hp.value': 28,
            });
            // Rename last: fires one more updateActor sync with items present.
            await actor.update({name});
            return actor.id;
        }""",
        "roll_stat": "Athletics",
        "roll_field_fragment": "Athletics",
        "attack": {
            "name": "Dagger",
            "create_js": """async (id) => {
                const actor = game.actors.get(id);
                await actor.createEmbeddedDocuments('Item', [
                    {name: 'Dagger', type: 'weapon', system: {
                        type: {value: 'simpleM'},
                        damage: {base: {number: 1, denomination: 4, types: ['piercing']}},
                    }},
                ]);
                // hp-only updates are deliberately skippable; touch an
                // ability so the module re-syncs with the new weapon.
                await actor.update({'system.abilities.dex.value': 12});
                return actor.items.find(i => i.name === 'Dagger')?.id;
            }""",
        },
        "xp_writeback": "system.details.xp.value",
    },
    "pf2e": {
        "actor_name": "E2E Pathfinder",
        "create_js": """async (name) => {
            const existing = game.actors.getName(name);
            if (existing) await existing.delete();
            const actor = await Actor.create({name: name + ' WIP', type: 'character'});
            await actor.createEmbeddedDocuments('Item', [
                {name: 'Dwarf', type: 'ancestry'},
                {name: 'Warrior', type: 'background'},
                {name: 'Fighter', type: 'class'},
            ]);
            await actor.update({'system.details.level.value': 3});
            await actor.update({name});
            return actor.id;
        }""",
        "roll_stat": "Athletics",
        "roll_field_fragment": "Athletics",
        "second_roll": {"stat": "Fortitude", "save": True, "fragment": "Fortitude"},
        "attack": {
            "name": "Dagger",
            "create_js": """async (id) => {
                const actor = game.actors.get(id);
                // pf2e only builds strikes for held weapons.
                await actor.createEmbeddedDocuments('Item', [
                    {name: 'Dagger', type: 'weapon',
                     system: {equipped: {carryType: 'held', handsHeld: 1}}},
                ]);
                // Non-noop, non-skippable update to force a re-sync.
                await actor.update({'system.details.level.value': 4});
                return actor.items.find(i => i.name === 'Dagger')?.id;
            }""",
        },
        "xp_writeback": "system.details.xp.value",
    },
    "CoC7": {
        "actor_name": "E2E Investigator",
        "create_js": """async (name) => {
            const existing = game.actors.getName(name);
            if (existing) await existing.delete();
            const actor = await Actor.create({name: name + ' WIP', type: 'character'});
            await actor.createEmbeddedDocuments('Item', [{
                name: 'Spot Hidden', type: 'skill',
                system: {base: 25, adjustments: {personal: 40},
                         properties: {special: false, combat: false}},
            }]);
            await actor.update({
                'system.characteristics.str.value': 60,
                'system.characteristics.dex.value': 70,
                'system.characteristics.int.value': 75,
                'system.characteristics.pow.value': 55,
                'system.infos.occupation': 'Private Investigator',
            });
            await actor.update({name});
            return actor.id;
        }""",
        "roll_stat": "STR",
        "roll_field_fragment": "STR",
        "roll_value_fragment": "rolled",  # "60 / rolled 43: Regular success"
        "second_roll": {"stat": "Spot Hidden", "fragment": "Spot Hidden"},
        "attack": {
            "name": ".38 Revolver",
            "create_js": """async (id) => {
                const actor = game.actors.get(id);
                await actor.createEmbeddedDocuments('Item', [
                    {name: 'Firearms (Handgun)', type: 'skill',
                     system: {base: 20, adjustments: {personal: 30},
                              properties: {combat: true, special: false}}},
                    {name: '.38 Revolver', type: 'weapon',
                     system: {skill: {main: {name: 'Firearms (Handgun)'}},
                              range: {normal: {damage: '1d10'}}}},
                ]);
                await actor.update({'system.characteristics.con.value': 50});
                return actor.items.find(i => i.name === '.38 Revolver')?.id;
            }""",
        },
    },
}


async def _system_config(foundry_gm):
    system_id = await foundry_gm.system_id()
    assert system_id in SYSTEMS, f"no e2e config for system {system_id}"
    return system_id, SYSTEMS[system_id]


async def test_module_connected(foundry_gm, api):
    r = await api.get("/testing/health")
    assert fake_discord.DEFAULT_GUILD_ID in r.json()["connected_foundry_guilds"]


async def test_actor_syncs_to_server(foundry_gm, api, seeded_guild):
    system_id, cfg = await _system_config(foundry_gm)

    await foundry_gm.set_id_map({"Gamemaster": str(fake_discord.DEFAULT_GM_USER_ID)})
    actor_id = await foundry_gm.page.evaluate(cfg["create_js"], cfg["actor_name"])
    assert actor_id

    row = None
    for _ in range(60):
        with Session() as session:
            row = (
                session.query(ActorTable)
                .filter_by(id=actor_id, guild_id=seeded_guild.id)
                .one_or_none()
            )
        if row and row.name == cfg["actor_name"]:
            break
        await asyncio.sleep(1)

    assert row, f"actor {actor_id} never reached the server"
    assert row.name == cfg["actor_name"], "final rename sync never arrived"
    assert row.world["system"] == system_id


async def test_roll_round_trip_through_real_foundry(foundry_gm, api, seeded_guild):
    """Discord /roll -> server -> real Foundry executes the roll -> embed."""
    system_id, cfg = await _system_config(foundry_gm)

    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": cfg["actor_name"],
            "stat": cfg["roll_stat"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], f"no response: {json.dumps(body)}"
    embed = body["responses"][0]["embeds"][0]
    field = embed["fields"][0]
    assert cfg["roll_field_fragment"].lower() in field["name"].lower()
    # A real roll result contains digits.
    assert any(ch.isdigit() for ch in field["value"]), field
    if "roll_value_fragment" in cfg:
        assert cfg["roll_value_fragment"].lower() in field["value"].lower(), field


async def test_second_stat_roll(foundry_gm, api, seeded_guild):
    """A second, system-flavored stat: pf2e save / CoC7 skill roll."""
    system_id, cfg = await _system_config(foundry_gm)
    second = cfg.get("second_roll")
    if not second:
        import pytest

        pytest.skip(f"no second roll configured for {system_id}")

    r = await api.post(
        "/testing/roll",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": cfg["actor_name"],
            "stat": second["stat"],
            "save": second.get("save", False),
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], f"no response: {json.dumps(body)}"
    assert body["responses"][0]["embeds"], f"no embed: {json.dumps(body)}"
    field = body["responses"][0]["embeds"][0]["fields"][0]
    assert second["fragment"].lower() in field["name"].lower(), field
    assert any(ch.isdigit() for ch in field["value"]), field


async def test_attack_round_trip(foundry_gm, api, seeded_guild):
    """Discord /attack -> server -> real Foundry rolls the weapon."""
    system_id, cfg = await _system_config(foundry_gm)
    attack = cfg.get("attack")
    if not attack:
        import pytest

        pytest.skip(f"no attack configured for {system_id}")

    actor_id = await foundry_gm.page.evaluate(
        "(name) => game.actors.getName(name)?.id", cfg["actor_name"]
    )
    assert actor_id, "actor from sync test missing"
    weapon_id = await foundry_gm.page.evaluate(attack["create_js"], actor_id)
    assert weapon_id, "weapon item was not created"

    # Wait until the synced row carries the weapon.
    for _ in range(30):
        with Session() as session:
            row = (
                session.query(ActorTable)
                .filter_by(id=actor_id, guild_id=seeded_guild.id)
                .one_or_none()
            )
        if row and any(w.get("name") == attack["name"] for w in (row.weapons or [])):
            break
        await asyncio.sleep(1)
    else:
        raise AssertionError(
            f"weapon {attack['name']} never appeared in synced weapons: "
            f"{row.weapons if row else None}"
        )

    r = await api.post(
        "/testing/attack",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "discord_id": fake_discord.DEFAULT_GM_USER_ID,
            "character": cfg["actor_name"],
            "attack": attack["name"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["responses"], f"no response: {json.dumps(body)}"
    embed = body["responses"][0]["embeds"][0]
    assert embed["title"] == attack["name"], embed
    assert embed["fields"], embed
    assert any(ch.isdigit() for ch in embed["fields"][0]["value"]), embed


async def test_xp_writeback_to_foundry(foundry_gm, api, seeded_guild):
    """Server-side XP reward lands in the Foundry actor sheet."""
    system_id, cfg = await _system_config(foundry_gm)
    xp_path = cfg.get("xp_writeback")
    if not xp_path:
        import pytest

        pytest.skip(f"{system_id} has no XP model")

    actor_id = await foundry_gm.page.evaluate(
        "(name) => game.actors.getName(name)?.id", cfg["actor_name"]
    )
    assert actor_id

    r = await api.post(
        "/testing/xp_sync",
        json={
            "guild_id": fake_discord.DEFAULT_GUILD_ID,
            "actor_id_to_xp": {actor_id: 4321},
        },
    )
    assert r.status_code == 200, r.text

    for _ in range(30):
        value = await foundry_gm.page.evaluate(
            """([id, path]) => foundry.utils.getProperty(
                game.actors.get(id), path)""",
            [actor_id, xp_path],
        )
        if value == 4321:
            break
        await asyncio.sleep(1)
    assert value == 4321, f"xp writeback never arrived (last={value})"


async def test_oauth_popup_handshake(foundry_gm, api):
    """The real /init popup <-> opener postMessage handshake, in a browser."""
    result = await foundry_gm.page.evaluate(
        """async (guild_id) => {
            const state = btoa('US/Eastern|' + window.location.host);
            const url = 'http://localhost:65435/init?code=e2e-code'
                + '&guild_id=' + guild_id + '&state=' + state;
            const popup = window.open(url, 'oronder_init_test');
            if (!popup) return {error: 'popup blocked'};
            try {
                return await new Promise((resolve, reject) => {
                    const timer = setTimeout(
                        () => reject(new Error('handshake timeout')), 45000);
                    const ping = setInterval(
                        () => popup.postMessage('', '*'), 250);
                    window.addEventListener('message', e => {
                        clearTimeout(timer);
                        clearInterval(ping);
                        resolve(e.data);
                    }, {once: true});
                });
            } finally {
                popup.close();
            }
        }""",
        str(fake_discord.ONBOARDING_GUILD_ID),
    )
    assert result.get("auth"), result
    assert result["guild"]["name"] == "Onboarding Guild", result
    assert result["status_code"] == 200, result


async def test_combat_event_reaches_discord(foundry_gm, api, seeded_guild):
    """Starting a combat in Foundry posts an embed to the combat channel."""
    system_id, cfg = await _system_config(foundry_gm)

    await api.post("/testing/reset")
    out = await foundry_gm.page.evaluate(
        """async (name) => {
            const actor = game.actors.getName(name);
            if (!actor) return {error: 'actor missing'};
            let scene = game.scenes.getName('E2E Arena');
            if (!scene) scene = await Scene.create(
                {name: 'E2E Arena', width: 1000, height: 1000});
            await scene.view();
            for (const c of game.combats.contents) await c.delete();
            const [token] = await scene.createEmbeddedDocuments('Token',
                [{actorId: actor.id, x: 100, y: 100}]);
            const combat = await Combat.create(
                {scene: scene.id, active: true});
            await combat.createEmbeddedDocuments('Combatant',
                [{tokenId: token.id, actorId: actor.id, initiative: 10}]);
            await combat.startCombat();
            return {combat: combat.id};
        }""",
        cfg["actor_name"],
    )
    assert "error" not in out, out

    messages = []
    for _ in range(30):
        r = await api.get("/testing/messages")
        messages = [
            m
            for m in r.json()
            if m["channel_id"] == str(fake_discord.DEFAULT_CHANNELS["combat"])
        ]
        if messages:
            break
        await asyncio.sleep(1)
    assert messages, "combat start never reached the Discord combat channel"
    combined = json.dumps(messages)
    assert cfg["actor_name"] in combined, combined


async def test_zz_actor_delete_syncs(foundry_gm, api, seeded_guild):
    """Deleting the actor in Foundry removes it server-side. Runs last."""
    system_id, cfg = await _system_config(foundry_gm)
    actor_id = await foundry_gm.page.evaluate(
        "(name) => game.actors.getName(name)?.id", cfg["actor_name"]
    )
    assert actor_id
    await foundry_gm.page.evaluate(
        "async (id) => { await game.actors.get(id).delete(); }", actor_id
    )
    for _ in range(30):
        with Session() as session:
            row = (
                session.query(ActorTable)
                .filter_by(id=actor_id, guild_id=seeded_guild.id)
                .one_or_none()
            )
        if row is None:
            break
        await asyncio.sleep(1)
    assert row is None, "actor row still present after deletion in Foundry"
