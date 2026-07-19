# Test harness

The server can run **entirely without Discord** and the test suite can
optionally drive a **real Foundry VTT** instance end-to-end.

## Fake Discord mode

Set `FAKE_DISCORD=1` (or just leave `DISCORD_TOKEN` unset) and the app runs
with no Discord connection at all:

- `src/fake_discord/` builds real py-cord `Guild`/`Member`/`TextChannel`
  objects from seed data and swaps the HTTP layer for an in-memory recorder,
  so cogs/routers/socket code runs unmodified.
- A `/testing/*` REST surface (only mounted in fake mode) drives what a
  Discord user would do and inspects what the bot "sent":
  - `GET  /testing/health` — fake guilds + connected Foundry guilds
  - `GET  /testing/messages` — messages the bot posted (`?clear=true`)
  - `POST /testing/reset`
  - `POST /testing/roll` — run the `/roll` slash command flow
  - `POST /testing/attack` — run the `/attack` flow
  - `POST /testing/xp_sync` — push XP to the connected Foundry world

The Discord OAuth onboarding also works headlessly: `GET /init` skips the
discord.com code exchange in fake mode and mints a real auth token, so the
module's full popup/postMessage handshake can run without a Discord app
(covered by `tests/test_oauth_init.py` and the e2e popup test).

Run it standalone:

```sh
FAKE_DISCORD=1 \
DATABASE_URL=postgresql://postgres@127.0.0.1:55432/oronder_test \
API_URL=http://localhost:65435 \
PYTHONPATH=src uv run uvicorn main:app --port 65435
```

Custom guild layout: point `FAKE_DISCORD_SEED` at a JSON file (see
`DEFAULT_SEED` in `src/fake_discord/__init__.py`).

## Running the tests

```sh
uv sync
uv run pytest            # server-only tests (no Foundry needed)
```

Postgres: the harness first tries `ORONDER_TEST_DATABASE_URL`, then a
server on `127.0.0.1:55432`, then **bootstraps a throwaway cluster itself**
via `initdb`/`pg_ctl` (`tests/harness/pg.py`).

The suite starts the full app (FastAPI + socket.io + fake Discord) on
`127.0.0.1:65435` once per session. A simulated Foundry client
(`sim_foundry` fixture) connects over socket.io and answers roll requests,
so Discord→Foundry round-trips are covered without Foundry.

## End-to-end with real Foundry

`tests/e2e/` boots a real, headless Foundry VTT and logs in as Gamemaster
with headless Chromium — the browser session is what runs the Oronder
module, which then connects to the test server exactly like production.

One-time setup:
1. Unzip the FoundryVTT Node build somewhere (`FOUNDRY_APP_DIR`), have a
   data dir (`FOUNDRY_DATA_DIR`) with an activated license
   (`Config/license.json` with a signature).
2. Install systems `dnd5e`, `pf2e`, `CoC7` and module `lib-wrapper`
   (POST to `/setup` with `{"action": "installPackage", ...}` works
   headlessly).
3. Create worlds `test-dnd5e`, `test-pf2e`, `test-coc7`.
4. Build the module (`npm run build-dev` in the Oronder repo) and copy it
   to `Data/modules/oronder`.
5. Node >= 24 (`FOUNDRY_NODE`).

The harness (`tests/harness/foundry.py`) then does everything else per
run: seeds world settings offline (enables the module + writes the
`oronder.auth` token into the world's LevelDB via
`tests/harness/seed_settings.mjs`), starts Foundry on **port 65434** (the
module's dev-mode origin), joins as GM, and waits for the module's socket
to reach the test server.

```sh
uv run pytest tests/e2e                            # dnd5e world
uv run pytest tests/e2e --foundry-world=test-pf2e  # pathfinder 2e
uv run pytest tests/e2e --foundry-world=test-coc7  # call of cthulhu
```

Ports: Foundry `65434`, server `65435` — fixed by the module's dev-mode
switch in `constants.mjs`.

## Game systems

See `SYSTEMS.md` for the per-system wire contracts (actor uploads and roll
requests) shared with the Foundry module.
