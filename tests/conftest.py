"""Test harness bootstrap.

Environment (fake Discord, database) must be configured before any src
module is imported, because several modules read env vars and create the
SQLAlchemy engine at import time. pytest_configure runs before collection,
which is early enough.
"""

import asyncio
import os

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--foundry-world",
        action="store",
        default="test-dnd5e",
        help="Foundry world id for e2e tests (test-dnd5e, test-pf2e, test-coc7)",
    )


def pytest_configure(config):
    os.environ.setdefault("FAKE_DISCORD", "1")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ.setdefault("API_URL", "http://localhost:65435")

    from harness import pg

    os.environ["DATABASE_URL"] = pg.ensure_database_url()


SERVER_HOST = "127.0.0.1"
SERVER_PORT = 65435
SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
FOUNDRY_ORIGIN = "http://localhost:65434"


@pytest.fixture(scope="session")
async def app_server():
    """The full app (FastAPI + socket.io + fake Discord) on 127.0.0.1:65435."""
    import uvicorn

    config = uvicorn.Config(
        "main:app", host=SERVER_HOST, port=SERVER_PORT, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    import httpx

    async with httpx.AsyncClient() as client:
        for _ in range(120):
            try:
                r = await client.get(f"{SERVER_URL}/testing/health", timeout=2)
                if r.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        else:
            server.should_exit = True
            await task
            raise RuntimeError("app server did not become healthy")

    yield SERVER_URL

    server.should_exit = True
    await task


@pytest.fixture(scope="session")
def seeded_guild(request):
    """Guild settings row matching the default fake-Discord guild."""
    # app_server not required, but ensure env/db is up via pytest_configure
    from harness import seed

    return seed.guild_settings()


@pytest.fixture
async def api(app_server):
    import httpx

    async with httpx.AsyncClient(
        base_url=app_server, timeout=10, headers={"Origin": FOUNDRY_ORIGIN}
    ) as client:
        yield client


@pytest.fixture
async def sim_foundry(app_server, seeded_guild):
    """A simulated Foundry client connected over socket.io.

    Answers 'roll' requests like the Foundry module would and records
    everything the server pushes to it.
    """
    import socketio

    client = socketio.AsyncClient()
    received = {"roll": [], "xp": [], "session": [], "item_desc": []}
    roll_response = {"res": "18 = 1d20 (15) + 3", "ephemeral": False}

    @client.on("roll")
    async def on_roll(data):
        received["roll"].append(data)
        return roll_response

    @client.on("xp")
    async def on_xp(data):
        received["xp"].append(data)

    @client.on("session")
    async def on_session(data):
        received["session"].append(data)

    @client.on("item_desc")
    async def on_item_desc(data):
        received["item_desc"].append(data)
        return "A fine piece of equipment."

    await client.connect(
        app_server,
        transports=["websocket"],
        auth={"Authorization": seeded_guild.auth_token},
        headers={"Origin": FOUNDRY_ORIGIN},
    )

    client.received = received
    client.roll_response = roll_response
    yield client

    await client.disconnect()
