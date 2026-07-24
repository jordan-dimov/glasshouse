"""`glasshouse provision` against the real stack: from a bare database
to a serving one in one idempotent command - app schema at head,
governed schema initialised, views applied and sealed - proven by the
same three readiness verdicts the deployment hook asks. Second run
no-ops; the destructive seed still works on top.
"""

import pytest
import sqlalchemy as sa

from glasshouse import cli
from glasshouse.api import health
from glasshouse.api.deps import build_client, build_engine
from glasshouse.compute.store import engine_url
from glasshouse.compute.store import metadata as payload_metadata
from glasshouse.config import get_settings
from glasshouse.projections.tables import metadata as projection_metadata
from tests.support import BINARY, DB, needs_live_stack

pytestmark = needs_live_stack


def _bare_database() -> None:
    # A clean slate WITHOUT the migrate step provision itself owns.
    engine = sa.create_engine(engine_url(DB))
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog CASCADE"))
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog_views CASCADE"))
        payload_metadata.drop_all(connection)
        projection_metadata.drop_all(connection)
        connection.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    engine.dispose()


def test_provision_takes_a_bare_database_to_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    _bare_database()

    assert cli.main(["provision", "--database-url", DB]) == 0
    first = capsys.readouterr().out
    assert "governed schema initialised" in first
    assert "views applied" in first

    # The deployment hook's own three verdicts, all green.
    settings = get_settings()
    engine = build_engine(settings)
    try:
        verdicts = health.checks(settings, engine, build_client(settings))
    finally:
        engine.dispose()
    assert verdicts == {"morpholog": "ok", "database": "ok", "commit": "ok"}

    # Idempotent: a second run is a no-op that exits 0.
    assert cli.main(["provision", "--database-url", DB]) == 0
    assert "governed schema already-initialised" in capsys.readouterr().out

    # The destructive seed still works on top of a provisioned database.
    assert cli.main(["seed", "--reset", "--database-url", DB]) == 0
    assert "verify: consistent" in capsys.readouterr().out
