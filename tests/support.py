"""Shared gating and provisioning for the live integration tests: a
morpholog binary and a disposable database, or a clean skip in local and
pure-test runs (CI's integration leg builds both and sets
GLASSHOUSE_REQUIRE_LIVE, so there the skip becomes a failure).

    GLASSHOUSE_MORPHOLOG_BIN     the binary itself (what CI installs from
                                 scripts/install-morpholog.sh, and what a
                                 developer machine gets from the same
                                 script - no Rust toolchain needed)
    GLASSHOUSE_MORPHOLOG_REPO    a source checkout to take
                                 target/release/morpholog from, when
                                 running against a local build instead
                                 (default ~/dev/morpholog)
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

import json
import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command
from glasshouse.commit.morpholog_client import MODEL_HASH, MORPHOLOG_VERSION, PROGRAM
from glasshouse.compute.store import engine_url
from glasshouse.compute.store import metadata as payload_metadata
from glasshouse.projections.tables import metadata as projection_metadata

ROOT = Path(__file__).resolve().parents[1]
REPO = Path(os.environ.get("GLASSHOUSE_MORPHOLOG_REPO", "~/dev/morpholog")).expanduser()
DB = os.environ.get("GLASSHOUSE_TEST_DATABASE_URL", "postgres:///morpholog_scratch")
# An explicit binary wins: the release channel is the blessed way to get
# one, and a source checkout is now the special case, not the default.
BINARY = (
    Path(os.environ["GLASSHOUSE_MORPHOLOG_BIN"])
    if os.environ.get("GLASSHOUSE_MORPHOLOG_BIN")
    else REPO / "target" / "release" / "morpholog"
)


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


def stamps(**overrides: object) -> dict[str, object]:
    """The `hash` report the pinned binary gives for the committed
    programme: what a GlasshouseClient's first-use check reads. One
    spelling, so a field upstream adds to the report is one edit here."""
    return {
        "program": PROGRAM,
        "hash": MODEL_HASH,
        "morpholog_version": MORPHOLOG_VERSION,
        **overrides,
    }


def fake_binary(
    tmp_path: Path,
    stdout: str,
    *,
    stderr: str = "",
    exit_code: int = 0,
    hash_report: dict[str, object] | None = None,
) -> Path:
    """A stand-in morpholog for pure tests: records its argv
    (argv.txt) and any piped stdin (stdin.txt), plays back a canned
    reply. The stdin capture is guarded so invocations without piped
    input do not block on a terminal.

    `hash` is answered separately, because every GlasshouseClient asks
    it once before its first call (the generated first-use check, on in
    our constructor): with `stamps()` by default, so the check passes
    and the canned reply serves the call under test; `hash_report`
    substitutes another report to exercise the check itself. argv.txt
    holds the LAST invocation, which is the call under test."""
    script = tmp_path / "fake-morpholog"
    (tmp_path / "stdout.txt").write_text(stdout)
    (tmp_path / "stderr.txt").write_text(stderr)
    (tmp_path / "hash.txt").write_text(json.dumps(stamps() if hash_report is None else hash_report))
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/argv.txt"\n'
        f'[ -t 0 ] || cat - > "{tmp_path}/stdin.txt"\n'
        f'if [ "$1" = hash ]; then cat "{tmp_path}/hash.txt"; exit 0; fi\n'
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
