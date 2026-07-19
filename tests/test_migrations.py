"""Alembic migration tests.

These use throwaway databases (oronder_mig_*) created on the same Postgres
server as the suite database, never the shared suite database itself.
"""

import os
import uuid

import psycopg2
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine.url import make_url


def _admin_dsn() -> str:
    return (
        make_url(os.environ["DATABASE_URL"])
        .set(database="postgres")
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def scratch_db_factory():
    """Create scratch databases on the test server; drop them on teardown."""
    conn = psycopg2.connect(_admin_dsn())
    conn.autocommit = True
    created: list[str] = []

    def create() -> str:
        name = f"oronder_mig_{uuid.uuid4().hex[:12]}"
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        return (
            make_url(os.environ["DATABASE_URL"])
            .set(database=name)
            .render_as_string(hide_password=False)
        )

    yield create

    for name in created:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    conn.close()


def _head_revision(config) -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(config).get_current_head()


def _stamped_revision(engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one()


def test_upgrade_head_matches_create_all(scratch_db_factory):
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    import database
    import database.db_utils  # noqa: F401  (registers every table module)

    upgraded_url = scratch_db_factory()
    create_all_url = scratch_db_factory()

    upgraded_engine = create_engine(upgraded_url)
    create_all_engine = create_engine(create_all_url)
    try:
        config = database._alembic_config(upgraded_url)
        with upgraded_engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()

        database.Base.metadata.create_all(create_all_engine)

        upgraded = inspect(upgraded_engine)
        created = inspect(create_all_engine)
        upgraded_tables = set(upgraded.get_table_names()) - {"alembic_version"}
        assert upgraded_tables == set(created.get_table_names())

        for table in sorted(upgraded_tables):

            def summary(inspector):
                return {
                    column["name"]: (str(column["type"]), column["nullable"])
                    for column in inspector.get_columns(table)
                }

            assert summary(upgraded) == summary(created), table

        # A fresh autogenerate against the upgraded database finds nothing.
        with upgraded_engine.connect() as connection:
            diffs = compare_metadata(
                MigrationContext.configure(connection), database.Base.metadata
            )
        assert diffs == []
    finally:
        upgraded_engine.dispose()
        create_all_engine.dispose()


def test_init_db_adopts_pre_alembic_schema(scratch_db_factory):
    import database
    import database.db_utils  # noqa: F401

    url = scratch_db_factory()
    engine = create_engine(url)
    try:
        # Simulate a deployment that predates alembic: tables, no version.
        database.Base.metadata.create_all(engine)
        assert "alembic_version" not in inspect(engine).get_table_names()

        database.init_db(bind=engine)

        head = _head_revision(database._alembic_config(url))
        assert _stamped_revision(engine) == head

        # Second call is a no-op.
        database.init_db(bind=engine)
        assert _stamped_revision(engine) == head
    finally:
        engine.dispose()


def test_init_db_on_empty_database(scratch_db_factory):
    import database
    import database.db_utils  # noqa: F401

    url = scratch_db_factory()
    engine = create_engine(url)
    try:
        database.init_db(bind=engine)

        tables = set(inspect(engine).get_table_names())
        expected = set(database.Base.metadata.tables) | {"alembic_version"}
        assert tables == expected
        assert _stamped_revision(engine) == _head_revision(
            database._alembic_config(url)
        )

        database.init_db(bind=engine)  # idempotent
        assert set(inspect(engine).get_table_names()) == expected
    finally:
        engine.dispose()
