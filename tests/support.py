"""Shared gating and provisioning for the live integration tests: a
morpholog binary and a disposable database, or a clean skip in local and
pure-test runs (CI's integration leg builds both and sets
GLASSHOUSE_REQUIRE_LIVE, so there the skip becomes a failure).

    GLASSHOUSE_MORPHOLOG_REPO    default ~/dev/morpholog (for the binary)
    GLASSHOUSE_TEST_DATABASE_URL default postgres:///morpholog_scratch
    GLASSHOUSE_REQUIRE_LIVE      when set, an absent stack is a failure,
                                 not a skip (CI sets this; it turns a
                                 green run that proved nothing into a
                                 loud one)

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
        # The official inspection model (law 4) is the binary's schema
        # too: drop it alongside the governed one so a stale view surface
        # never leaks between integration modules.
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog_views CASCADE"))
        payload_metadata.drop_all(connection)
        projection_metadata.drop_all(connection)
        connection.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", engine_url(database_url))
    command.upgrade(config, "head")
    return engine


def fake_binary(tmp_path: Path, stdout: str, *, stderr: str = "", exit_code: int = 0) -> Path:
    """A stand-in morpholog for pure tests: records its argv
    (argv.txt) and any piped stdin (stdin.txt), plays back a canned
    reply. The stdin capture is guarded so invocations without piped
    input do not block on a terminal."""
    script = tmp_path / "fake-morpholog"
    (tmp_path / "stdout.txt").write_text(stdout)
    (tmp_path / "stderr.txt").write_text(stderr)
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/argv.txt"\n'
        f'[ -t 0 ] || cat - > "{tmp_path}/stdin.txt"\n'
        f'cat "{tmp_path}/stdout.txt"\n'
        f'cat "{tmp_path}/stderr.txt" >&2\n'
        f"exit {exit_code}\n"
    )
    script.chmod(0o755)
    return script


def _database_reachable() -> bool:
    try:
        ok = subprocess.run(
            ["psql", DB, "-qc", "select 1"], capture_output=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ok.returncode == 0


_binary_present = BINARY.exists()
_require_live = bool(os.environ.get("GLASSHOUSE_REQUIRE_LIVE"))
# Probe the database only when the live stack is plausibly in use (the
# binary is present) or explicitly demanded: a pure local run with no
# binary must not spawn psql, let alone wait out its timeout.
_database_ok = _database_reachable() if (_binary_present or _require_live) else False
_live = _binary_present and _database_ok

if _require_live and not _live:
    # The opt-in for anyone who means to exercise the live legs (CI, a
    # release check): refuse to let them skip unnoticed.
    raise RuntimeError(
        "GLASSHOUSE_REQUIRE_LIVE is set but the live stack is incomplete - "
        f"binary at {BINARY} {'present' if _binary_present else 'MISSING'}, "
        f"database at {DB} {'reachable' if _database_ok else 'UNREACHABLE'}."
    )

needs_live_stack = pytest.mark.skipif(
    not _live,
    reason=f"needs a morpholog binary at {BINARY} and a database at {DB}",
)
