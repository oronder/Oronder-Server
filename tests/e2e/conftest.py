"""Fixtures for end-to-end tests against a real Foundry VTT instance."""

import asyncio

import pytest

from harness import foundry as foundry_harness

def pytest_collection_modifyitems(config, items):
    for item in items:
        if "e2e" in str(item.path):
            item.add_marker(pytest.mark.foundry)
            if not foundry_harness.available():
                item.add_marker(
                    pytest.mark.skip(
                        reason="Foundry VTT not available "
                        "(set FOUNDRY_APP_DIR/FOUNDRY_DATA_DIR)"
                    )
                )

    # Run e2e last: the real Foundry module connects via a session-scoped
    # fixture and stays connected for the rest of the session, so if e2e ran
    # first it would receive the rolls meant for sim_foundry clients in the
    # harness tests (the server routes each guild's rolls to its first
    # connected socket).
    e2e_items = [i for i in items if "e2e" in str(i.path)]
    if e2e_items:
        items[:] = [i for i in items if i not in e2e_items] + e2e_items


@pytest.fixture(scope="session")
def foundry_world(request):
    """Which world to run e2e against. Overridable: --foundry-world=test-pf2e"""
    return request.config.getoption("--foundry-world")


@pytest.fixture(scope="session")
async def foundry_gm(app_server, seeded_guild, foundry_world):
    """A running Foundry world with the Oronder module connected to the
    local test server, and a logged-in GM page driving it."""
    from playwright.async_api import async_playwright

    harness = foundry_harness.FoundryHarness(foundry_world)
    try:
        harness.enable_module(seeded_guild.auth_token)
        harness.start()

        async with async_playwright() as p:
            gm = await foundry_harness.GamemasterPage.login(p)
            assert await gm.module_active(), (
                "oronder module is not active in this world"
            )

            # The module opens its socket at 'ready'; wait for the server
            # to see it.
            import httpx

            async with httpx.AsyncClient() as client:
                for _ in range(60):
                    r = await client.get(f"{app_server}/testing/health")
                    if seeded_guild.id in r.json()["connected_foundry_guilds"]:
                        break
                    await asyncio.sleep(1)
                else:
                    raise RuntimeError(
                        "Foundry module never connected to test server"
                    )

            try:
                yield gm
            finally:
                await gm.close()
    finally:
        harness.stop()
