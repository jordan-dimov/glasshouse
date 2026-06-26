"""The official inspection model against the real binary: the committed
view script is what the binary generates (regenerate-and-diff drift gate,
the same discipline as the Python client), it applies cleanly over the
governed schema, its catalogue names the committed programme, and a view
reads a governed claim back as typed columns.

Same gating and provisioning contract as the other integration legs.
"""

import filecmp
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa

from glasshouse import cli
from glasshouse.commit import (
    MODEL_FILE,
    MODEL_HASH,
    VIEWS_FILE,
    VIEWS_SCHEMA,
    Committed,
    GlasshouseClient,
    apply_views,
    models,
    views_model_hash,
)
from glasshouse.compute.store import engine_url
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG, BOOK = "acme-energy", "spec-de"


@pytest.fixture(scope="module")
def applied() -> sa.Engine:
    """A provisioned ledger with one governed claim and the inspection
    model applied over it."""
    provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    assert isinstance(
        client.submit(
            models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
            actor="bootstrap",
        ),
        Committed,
    )
    engine = sa.create_engine(engine_url(DB))
    apply_views(engine)
    return engine


def test_the_committed_views_are_what_the_binary_generates(tmp_path: Path) -> None:
    # The drift gate: regenerate from the live binary and diff byte-exact
    # against the committed script (CI has no binary; this leg closes it).
    regenerated = tmp_path / "morpholog_views.sql"
    subprocess.run(
        [str(BINARY), "generate", "views", str(MODEL_FILE), "--out", str(regenerated)],
        check=True,
        capture_output=True,
    )
    assert filecmp.cmp(VIEWS_FILE, regenerated, shallow=False), "regenerate the views: they drifted"


def test_the_catalogue_names_the_committed_programme(applied: sa.Engine) -> None:
    # The same hash the binary and the Python client name: the SQL read
    # surface and the rules under it are one programme.
    assert views_model_hash(applied) == MODEL_HASH


def test_a_view_reads_a_governed_claim(applied: sa.Engine) -> None:
    # The point of law 4: governed state read as typed columns through
    # the binary's official mapping, never hand-rolled positional JSONB.
    with applied.connect() as connection:
        rows = connection.execute(
            sa.text(f'SELECT actor, org, book FROM "{VIEWS_SCHEMA}".may_capture_trade')
        ).all()
    assert tuple(tuple(row) for row in rows) == (("alice", ORG, BOOK),)


def test_applying_twice_is_idempotent(applied: sa.Engine) -> None:
    # CREATE OR REPLACE VIEW: re-application is a no-op, not a failure -
    # the deployment can run it unconditionally.
    apply_views(applied)
    assert views_model_hash(applied) == MODEL_HASH


def test_applying_does_not_leak_autocommit() -> None:
    # apply_views runs on a pooled connection; setting autocommit by hand
    # would leak the flag to the next checkout and silently break
    # transactions. Force reuse with a single-connection pool and prove a
    # later rollback still takes effect (it undoes the CREATE, so the
    # table is gone - to_regclass returns NULL rather than raising).
    engine = sa.create_engine(engine_url(DB), pool_size=1, max_overflow=0)
    try:
        apply_views(engine)
        with engine.connect() as connection:
            connection.execute(sa.text("CREATE TEMP TABLE _leak_probe (x int)"))
            connection.execute(sa.text("INSERT INTO _leak_probe VALUES (1)"))
            connection.rollback()
            survived = connection.execute(sa.text("SELECT to_regclass('_leak_probe')")).scalar_one()
        assert survived is None
    finally:
        engine.dispose()


def test_the_cli_applies_the_inspection_model(
    applied: sa.Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    # The operator seam (the deployment runs this after `morpholog init`).
    # No binary needed: apply-views only touches the database.
    with applied.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog_views CASCADE"))
    assert cli.main(["apply-views", "--database-url", DB]) == 0
    assert "inspection model" in capsys.readouterr().out
    assert views_model_hash(applied) == MODEL_HASH
