"""Self-bootstrapping Postgres for the test harness.

Order of preference:
1. ORONDER_TEST_DATABASE_URL if set and reachable.
2. An already-running Postgres on 127.0.0.1:55432.
3. Bootstrap a throwaway cluster with initdb/pg_ctl (found via PG_BINDIR or
   common install locations) listening on 127.0.0.1:55432.
"""

import getpass
import os
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_PORT = 55432
DEFAULT_URL = f"postgresql://postgres@127.0.0.1:{DEFAULT_PORT}/oronder_test"


def _reachable(url: str) -> bool:
    import psycopg2

    try:
        conn = psycopg2.connect(url, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


def _pg_bindir() -> Path | None:
    if os.environ.get("PG_BINDIR"):
        return Path(os.environ["PG_BINDIR"])
    for pattern in ["/usr/lib/postgresql/*/bin", "/usr/pgsql-*/bin"]:
        import glob

        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return Path(matches[0])
    if shutil.which("initdb"):
        return Path(shutil.which("initdb")).parent
    return None


def _run_as_available_user(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """initdb/postgres refuse to run as root; drop to the postgres user then."""
    if getpass.getuser() == "root":
        quoted = " ".join(f"'{c}'" for c in cmd)
        return subprocess.run(
            ["su", "postgres", "-c", quoted], capture_output=True, text=True, **kwargs
        )
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _bootstrap(port: int) -> str:
    bindir = _pg_bindir()
    if bindir is None:
        raise RuntimeError(
            "No reachable Postgres and no postgres binaries found. "
            "Set ORONDER_TEST_DATABASE_URL or install postgresql."
        )

    base = Path(os.environ.get("ORONDER_TEST_PG_DIR", "/tmp/oronder-test-pg"))
    datadir = base / "data"
    rundir = base / "run"
    logfile = base / "pg.log"
    base.mkdir(parents=True, exist_ok=True)
    rundir.mkdir(parents=True, exist_ok=True)

    if getpass.getuser() == "root":
        subprocess.run(
            ["chown", "-R", "postgres:postgres", str(base)], capture_output=True
        )

    if not (datadir / "PG_VERSION").exists():
        res = _run_as_available_user(
            [str(bindir / "initdb"), "-D", str(datadir), "-A", "trust", "-U", "postgres"]
        )
        if res.returncode != 0:
            raise RuntimeError(f"initdb failed: {res.stderr}")

    # stale lock file after an unclean container stop
    (datadir / "postmaster.pid").unlink(missing_ok=True)

    res = _run_as_available_user(
        [
            str(bindir / "pg_ctl"),
            "-D",
            str(datadir),
            "-o",
            f"-p {port} -k {rundir} -c listen_addresses=127.0.0.1",
            "-l",
            str(logfile),
            "start",
        ]
    )
    if res.returncode != 0 and "another server might be running" not in res.stderr:
        raise RuntimeError(f"pg_ctl start failed: {res.stderr}\n{res.stdout}")

    url = f"postgresql://postgres@127.0.0.1:{port}/postgres"
    for _ in range(30):
        if _reachable(url):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"postgres did not come up; see {logfile}")

    import psycopg2

    conn = psycopg2.connect(url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("select 1 from pg_database where datname = 'oronder_test'")
        if not cur.fetchone():
            cur.execute("create database oronder_test")
    conn.close()
    return f"postgresql://postgres@127.0.0.1:{port}/oronder_test"


def ensure_database_url() -> str:
    """Return a usable DATABASE_URL, bootstrapping a local cluster if needed."""
    explicit = os.environ.get("ORONDER_TEST_DATABASE_URL")
    if explicit:
        if _reachable(explicit):
            return explicit
        raise RuntimeError(f"ORONDER_TEST_DATABASE_URL not reachable: {explicit}")

    if _reachable(DEFAULT_URL):
        return DEFAULT_URL

    return _bootstrap(DEFAULT_PORT)
