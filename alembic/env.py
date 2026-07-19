"""Alembic environment.

Must work from any CWD (the container runs from /app, developers run from the
repo root), so sys.path is derived from this file's location.  The
application's ``database`` package (which owns the SQLAlchemy metadata) lives
at ``<repo>/src/database`` in the repo layout and at ``/app/database`` in the
container layout, with this directory as a sibling of ``src`` / ``database``
respectively.
"""

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine

from alembic import context

config = context.config

# Only configure logging when invoked via the alembic CLI (an ini file is
# present).  When run programmatically from init_db() the application has
# already configured logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Make the application's packages importable regardless of CWD.
_here = Path(__file__).resolve().parent
for _candidate in (_here.parent / "src", _here.parent):
    if (_candidate / "database" / "__init__.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break


def _database_url() -> str:
    """URL precedence: programmatic attribute, DATABASE_URL env, alembic.ini."""
    url = (
        config.attributes.get("database_url")
        or os.environ.get("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if not url:
        raise RuntimeError(
            "No database URL: set DATABASE_URL, pass config.attributes"
            "['database_url'], or set sqlalchemy.url in alembic.ini"
        )
    return url


_url = _database_url()

# database/__init__.py reads DATABASE_URL at import time; satisfy it when the
# URL came from alembic.ini instead of the environment.
os.environ.setdefault("DATABASE_URL", _url)

import database  # noqa: E402
import database.actor_table  # noqa: E402,F401
import database.game_master_table  # noqa: E402,F401
import database.guild_settings_table  # noqa: E402,F401
import database.missions  # noqa: E402,F401

target_metadata = database.Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection.

    init_db() (and the migration tests) pass an existing Connection through
    config.attributes['connection']; the alembic CLI path creates its own
    engine from the resolved URL.
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        _run_with_connection(connection)
    else:
        engine = create_engine(_url)
        try:
            with engine.connect() as conn:
                _run_with_connection(conn)
        finally:
            engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
