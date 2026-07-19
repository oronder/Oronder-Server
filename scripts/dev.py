#!/usr/bin/env python3
"""Run the Oronder server out of the box: no Discord app, no configuration.

- Bootstraps (or reuses) a local Postgres via tests/harness/pg.py
- Enables fake-Discord mode unless a DISCORD_TOKEN is set
- Serves on http://localhost:65435 — the port the Oronder Foundry module
  targets when Foundry itself is served from localhost:65434 (dev mode)

Usage: uv run python scripts/dev.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def main():
    os.environ.setdefault("LOG_LEVEL", "INFO")
    os.environ.setdefault("API_URL", "http://localhost:65435")
    if not os.environ.get("DISCORD_TOKEN"):
        os.environ.setdefault("FAKE_DISCORD", "1")
        print("* no DISCORD_TOKEN — running with the fake Discord layer")
        print("  (drive it via the /testing/* endpoints, see TESTING.md)")

    if not os.environ.get("DATABASE_URL"):
        from harness import pg

        os.environ["DATABASE_URL"] = pg.ensure_database_url()
    print(f"* database: {os.environ['DATABASE_URL']}")

    import uvicorn

    port = int(os.environ.get("UVICORN_PORT", "65435"))
    print(f"* serving on http://127.0.0.1:{port}")
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
