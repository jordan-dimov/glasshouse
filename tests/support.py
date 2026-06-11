"""Shared gating and provisioning for the live integration tests: a
morpholog binary and a disposable database, or a clean skip (CI has
neither).

    GLASSHOUSE_MORPHOLOG_REPO    default ~/dev/morpholog (for the binary)
    GLASSHOUSE_TEST_DATABASE_URL default postgres:///morpholog_scratch

The database is disposable by contract: `provision` drops the morpholog
schema and every app-schema table, then migrates the app schema to head,
so each integration module starts from zero whatever ran before it (the
modules share one scratch database).
"""

import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from glasshouse.compute.store import engine_url
from glasshouse.compute.store import metadata as payload_metadata
from glasshouse.projections.tables import metadata as projection_metadata

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("GLASSHOUSE_MORPHOLOG_REPO", "~/dev/morpholog")).expanduser()
DB = os.environ.get("GLASSHOUSE_TEST_DATABASE_URL", "postgres:///morpholog_scratch")
BINARY = REPO / "target" / "release" / "morpholog"


def provision(database_url: str = DB) -> sa.Engine:
    """A clean slate for both legs: drop the governed schema and every
    app-schema table, then migrate the app schema to head (so the
    migrations are part of what the integration tests prove)."""
    engine = sa.create_engine(engine_url(database_url))
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog CASCADE"))
        payload_metadata.drop_all(connection)
        projection_metadata.drop_all(connection)
        connection.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", engine_url(database_url))
    command.upgrade(config, "head")
    return engine


def _database_reachable() -> bool:
    try:
        ok = subprocess.run(
            ["psql", DB, "-qc", "select 1"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ok.returncode == 0


needs_live_stack = pytest.mark.skipif(
    not (BINARY.exists() and _database_reachable()),
    reason=f"needs a morpholog binary at {BINARY} and a database at {DB}",
)
