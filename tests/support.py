"""Shared gating for the live integration tests: a morpholog binary and
a disposable database, or a clean skip (CI has neither).

    GLASSHOUSE_MORPHOLOG_REPO    default ~/dev/morpholog (for the binary)
    GLASSHOUSE_TEST_DATABASE_URL default postgres:///morpholog_scratch

The database is disposable by contract: integration fixtures drop and
re-provision the morpholog schema, and the run path commits.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(os.environ.get("GLASSHOUSE_MORPHOLOG_REPO", "~/dev/morpholog")).expanduser()
DB = os.environ.get("GLASSHOUSE_TEST_DATABASE_URL", "postgres:///morpholog_scratch")
BINARY = REPO / "target" / "release" / "morpholog"


def _database_reachable() -> bool:
    try:
        ok = subprocess.run(["psql", DB, "-qc", "select 1"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ok.returncode == 0


needs_live_stack = pytest.mark.skipif(
    not (BINARY.exists() and _database_reachable()),
    reason=f"needs a morpholog binary at {BINARY} and a database at {DB}",
)
