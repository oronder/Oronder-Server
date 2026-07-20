"""E2E: the module's settings form (ApplicationV2) in a real browser.

Opens the registered settings menu in the GM page, waits for the form to
populate itself from GET /guild, edits fields through the DOM (including
the show-advanced re-render path and the id_map player mapping), submits,
and verifies the changes landed in the server database and in the Foundry
world settings. Runs after test_foundry_e2e.py (alphabetical file order)
and restores both the guild_settings row and the world's id_map setting so
it leaves no trace.
"""

import asyncio

import fake_discord
from database.guild_settings_table import GuildSettingsTable
from harness import seed

MENU_KEY = "oronder.oronder_options"
FORM_SELECTOR = "#oronder-options"

NEW_GM_XP = 7
NEW_TIMEZONE = "US/Central"
# The form's combat-channel select lists text channels; move it off the
# seeded 'combat' channel.
NEW_COMBAT_CHANNEL_ID = str(fake_discord.DEFAULT_CHANNELS["downtime"])


async def _open_form(foundry_gm):
    opened = await foundry_gm.page.evaluate(
        f"""async () => {{
            const menu = game.settings.menus.get('{MENU_KEY}');
            if (!menu) return {{error: 'menu not registered'}};
            const app = new menu.type();
            window._oronder_settings_app = app;
            await app.render(true);
            return {{id: app.id}};
        }}"""
    )
    assert opened.get("id") == "oronder-options", opened

    # The form fetches /guild over HTTP on render; wait until the guild
    # data has actually been rendered into the selects.
    await foundry_gm.wait_for(
        f"document.querySelectorAll("
        f"'{FORM_SELECTOR} select[name=session_channel] option').length > 0",
        timeout=30,
        msg="settings form never populated from GET /guild",
    )


async def _select_texts(foundry_gm, name):
    return await foundry_gm.page.evaluate(
        f"""() => Array.from(document.querySelectorAll(
            '{FORM_SELECTOR} select[name={name}] option'
        )).map(o => o.textContent.trim())"""
    )


async def test_settings_form_round_trip(foundry_gm, app_server, seeded_guild):
    page = foundry_gm.page
    prior_id_map = await page.evaluate(
        "() => game.settings.get('oronder', 'id_map')"
    )

    # A player-role user so the Foundry->Discord id mapping UI is exercised
    # (the form only lists users below role 3).
    player_user_id = await page.evaluate(
        """async () => {
            let user = game.users.find(u => u.name === 'E2E Player');
            if (!user) user = await User.create({name: 'E2E Player', role: 1});
            return user.id;
        }"""
    )
    assert player_user_id

    try:
        await _open_form(foundry_gm)

        # -- populated from the server ---------------------------------
        session_channels = await _select_texts(foundry_gm, "session_channel")
        for expected in ("# general", "# combat", "# downtime", "# scheduling"):
            assert expected in session_channels, session_channels
        # 'general' is sorted first by the form.
        assert session_channels[0] == "# general", session_channels

        assert (
            await page.evaluate(
                f"() => document.querySelector("
                f"'{FORM_SELECTOR} select[name=timezone]').value"
            )
            == "US/Eastern"
        )
        gm_roles = await _select_texts(foundry_gm, "gm_role")
        assert "Game Master" in gm_roles, gm_roles

        # Discord members offered for the player mapping come from the
        # fake guild.
        members = await _select_texts(foundry_gm, player_user_id)
        assert "FakeGM" in members and "FakePlayer" in members, members

        # -- edit fields through the DOM -------------------------------
        await page.fill(f"{FORM_SELECTOR} input[name=gm_xp]", str(NEW_GM_XP))
        await page.select_option(
            f"{FORM_SELECTOR} select[name=timezone]", NEW_TIMEZONE
        )
        await page.select_option(
            f"{FORM_SELECTOR} select[name={player_user_id}]",
            str(fake_discord.DEFAULT_PLAYER_USER_ID),
        )

        # Toggling 'Advanced Options' re-renders the whole form
        # (ApplicationV2 action) — in-progress edits must survive.
        await page.click(f"{FORM_SELECTOR} input[name=show_advanced]")
        await foundry_gm.wait_for(
            f"document.querySelector('{FORM_SELECTOR} select[name=combat_channel]')",
            timeout=15,
            msg="advanced section never rendered",
        )
        assert (
            await page.evaluate(
                f"() => document.querySelector("
                f"'{FORM_SELECTOR} input[name=gm_xp]').value"
            )
            == str(NEW_GM_XP)
        ), "in-progress edit lost across re-render"

        # combat tracking is seeded enabled, so the select is editable.
        await page.select_option(
            f"{FORM_SELECTOR} select[name=combat_channel]", NEW_COMBAT_CHANNEL_ID
        )

        # -- save ------------------------------------------------------
        await page.click(f"{FORM_SELECTOR} button[type=submit]")

        # closeOnSubmit: the window goes away once the handler finishes.
        await foundry_gm.wait_for(
            f"!document.querySelector('{FORM_SELECTOR}')",
            timeout=30,
            msg="settings form did not close after submit",
        )

        # -- server database picked up the POST /guild -----------------
        row = None
        for _ in range(30):
            row = GuildSettingsTable.lookup(seeded_guild.id)
            if row and row.gm_xp == NEW_GM_XP:
                break
            await asyncio.sleep(1)
        assert row, "guild settings row disappeared"
        assert row.gm_xp == NEW_GM_XP
        assert row.timezone == NEW_TIMEZONE
        assert str(row.combat_channel_id) == NEW_COMBAT_CHANNEL_ID
        # untouched fields kept their seeded values
        assert str(row.session_channel_id) == str(
            fake_discord.DEFAULT_CHANNELS["general"]
        )
        assert str(row.gm_role_id) == str(fake_discord.DEFAULT_GM_ROLE_ID)

        # -- id_map world setting updated ------------------------------
        id_map = await page.evaluate(
            "() => game.settings.get('oronder', 'id_map')"
        )
        assert id_map.get(player_user_id) == str(
            fake_discord.DEFAULT_PLAYER_USER_ID
        ), id_map
    finally:
        # Restore everything this test touched so reruns / later tests
        # see the canonical seeded state.
        seed.guild_settings(auth_token=seeded_guild.auth_token)
        await page.evaluate(
            """async ([id_map, user_id]) => {
                await game.settings.set('oronder', 'id_map', id_map);
                const user = game.users.get(user_id);
                if (user) await user.delete();
            }""",
            [prior_id_map, player_user_id],
        )
