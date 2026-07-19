"""Drive a real, headless Foundry VTT instance for end-to-end tests.

Requirements (all overridable via env):
- FOUNDRY_APP_DIR    unpacked FoundryVTT Node build (contains main.js)
- FOUNDRY_DATA_DIR   Foundry user data dir (Config/ with a signed license,
                     Data/systems/{dnd5e,pf2e,CoC7}, Data/modules/oronder,
                     Data/worlds/test-*)
- FOUNDRY_NODE       node >= 24 binary
- Foundry must listen on localhost:65434 — the Oronder module's dev-mode
  switch keys off that exact origin and then talks to localhost:65435.

The harness seeds world settings offline (module enablement + auth token),
boots Foundry with a chosen world, and logs in as Gamemaster with headless
Chromium (Playwright), which is what actually runs the module code.
"""

import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

FOUNDRY_PORT = 65434
FOUNDRY_URL = f"http://localhost:{FOUNDRY_PORT}"

APP_DIR = Path(os.environ.get("FOUNDRY_APP_DIR", "/home/user/foundry-dist/foundry"))
DATA_DIR = Path(os.environ.get("FOUNDRY_DATA_DIR", "/home/user/foundry-data"))
NODE = os.environ.get(
    "FOUNDRY_NODE",
    next(
        (
            p
            for p in [
                "/home/user/foundry-dist/node-v24.13.0-linux-x64/bin/node",
                shutil.which("node") or "node",
            ]
            if p and Path(p).exists()
        ),
        "node",
    ),
)

SEED_SCRIPT = Path(__file__).parent / "seed_settings.mjs"

# Prefer a pre-provisioned Chromium (e.g. /opt/pw-browsers/chromium) over
# letting Playwright insist on its own pinned download.
CHROMIUM_PATH = os.environ.get(
    "FOUNDRY_TEST_CHROMIUM",
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else "",
)


def available() -> bool:
    return (APP_DIR / "main.js").exists() and (DATA_DIR / "Config").exists()


class FoundryHarness:
    def __init__(self, world_id: str):
        self.world_id = world_id
        self.process: subprocess.Popen | None = None
        self.log_path = DATA_DIR / f"foundry-{world_id}.log"

    # -- offline world seeding ----------------------------------------
    def seed_settings(self, settings: dict):
        """Write world settings while Foundry is stopped."""
        db_path = DATA_DIR / "Data" / "worlds" / self.world_id / "data" / "settings"
        res = subprocess.run(
            [NODE, str(SEED_SCRIPT), str(APP_DIR), str(db_path), json.dumps(settings)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if res.returncode != 0:
            raise RuntimeError(f"seed_settings failed: {res.stderr}")

    def enable_module(self, auth_token: str, id_map: dict | None = None):
        settings = {
            "core.moduleConfiguration": {"oronder": True, "lib-wrapper": True},
            "oronder.auth": auth_token,
            "oronder.combat_enabled": True,
            "oronder.combat_health_estimate": 0,
        }
        if id_map:
            settings["oronder.id_map"] = id_map
        self.seed_settings(settings)

    # -- process management -------------------------------------------
    def start(self, timeout: float = 180.0):
        assert self.process is None
        try:
            with urllib.request.urlopen(f"{FOUNDRY_URL}/", timeout=2):
                pass
            raise RuntimeError(
                f"something is already listening on {FOUNDRY_URL} — "
                "stop the other Foundry instance first"
            )
        except (urllib.error.URLError, OSError):
            pass
        self.process = subprocess.Popen(
            [
                NODE,
                "main.js",
                f"--dataPath={DATA_DIR}",
                f"--port={FOUNDRY_PORT}",
                "--headless",
                f"--world={self.world_id}",
            ],
            cwd=APP_DIR,
            stdout=open(self.log_path, "w"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(
                    f"Foundry exited early; see {self.log_path}:\n"
                    + self.log_path.read_text()[-2000:]
                )
            try:
                with urllib.request.urlopen(f"{FOUNDRY_URL}/join", timeout=3) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2)
        raise RuntimeError(f"Foundry not up after {timeout}s; see {self.log_path}")

    def stop(self):
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=10)
        self.process = None
        # LevelDB releases its lock on clean shutdown; give it a beat.
        time.sleep(1)


class GamemasterPage:
    """A logged-in GM browser session — this is what runs the module."""

    def __init__(self, page):
        self.page = page

    @classmethod
    async def login(cls, playwright, timeout: float = 120.0) -> "GamemasterPage":
        browser = await playwright.chromium.launch(
            executable_path=CHROMIUM_PATH or None
        )
        page = await browser.new_page()
        cls._browser = browser
        await page.goto(f"{FOUNDRY_URL}/join", wait_until="load")

        # The join view is client-rendered; wait for its Game stub, then
        # POST /join directly — more stable than driving the form UI.
        deadline = asyncio.get_event_loop().time() + 60
        while not await page.evaluate("() => !!window.game?.users?.size"):
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("join page never initialized")
            await asyncio.sleep(1)

        result = await page.evaluate(
            """async () => {
                const users = game.users.contents.map(
                    u => ({id: u.id, name: u.name, role: u.role}));
                const gm = users.find(u => u.role === 4) || users[0];
                if (!gm) return {error: 'no users in world'};
                const res = await fetch('join', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({action: 'join', userid: gm.id, password: ''})
                });
                return {status: res.status, gm, body: await res.json().catch(() => null)};
            }"""
        )
        if result.get("error") or result.get("status") != 200:
            raise RuntimeError(f"GM join failed: {result}")

        await page.goto(f"{FOUNDRY_URL}/game", wait_until="domcontentloaded")
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            ready = await page.evaluate(
                "() => !!(window.game && game.ready === true)"
            )
            if ready:
                break
            if asyncio.get_event_loop().time() > deadline:
                raise RuntimeError("game.ready never became true")
            await asyncio.sleep(1)
        return cls(page)

    async def close(self):
        await self._browser.close()

    async def eval(self, js: str):
        """Run an async JS snippet in the game context."""
        return await self.page.evaluate(js)

    async def wait_for(self, js_condition: str, timeout: float = 60.0, msg: str = ""):
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            if await self.page.evaluate(f"() => !!({js_condition})"):
                return
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(msg or f"condition never true: {js_condition}")
            await asyncio.sleep(0.5)

    async def module_active(self) -> bool:
        return await self.page.evaluate(
            "() => !!game.modules.get('oronder')?.active"
        )

    async def system_id(self) -> str:
        return await self.page.evaluate("() => game.system.id")

    async def set_id_map(self, foundry_user_name_to_discord_id: dict):
        """Map Foundry users to Discord ids via the module's id_map setting."""
        return await self.page.evaluate(
            """async (mapping) => {
                const id_map = {};
                for (const [name, discord_id] of Object.entries(mapping)) {
                    const user = game.users.find(u => u.name === name);
                    if (user) id_map[user.id] = discord_id;
                }
                await game.settings.set('oronder', 'id_map', id_map);
                return id_map;
            }""",
            foundry_user_name_to_discord_id,
        )
