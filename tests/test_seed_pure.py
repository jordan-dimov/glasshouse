"""The seed command's deterministic legs: the fences refuse before any
connection exists (a dead database URL proves none was attempted), the
reset preflight refuses before any destructive statement, and the report
renders stably.
"""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from glasshouse import cli, seed
from glasshouse.commit import GlasshouseClient
from glasshouse.compute.store import engine_url
from glasshouse.seed import SeedError, SeedReport, refuse_unsafe_reset, reset_app_state
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


def test_reset_refuses_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "production")
    assert cli.main(["seed", "--reset", "--database-url", DEAD_DB]) == 1
    assert "production" in capsys.readouterr().err


def test_plain_seed_also_refuses_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The whole command is demo provisioning: a fictional portfolio has
    # no business in a production ledger, empty or not. The dead
    # database proves the refusal happened before any connection.
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "production")
    assert cli.main(["seed", "--database-url", DEAD_DB]) == 1
    assert "production" in capsys.readouterr().err


def test_a_missing_migration_bundle_refuses_before_any_drop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The preflight runs before the first destructive statement: with the
    # bundle absent, the refusal must arrive as SeedError - the dead
    # database would have raised an operational error instead had any
    # DROP been attempted.
    monkeypatch.setattr(seed, "_ALEMBIC_INI", tmp_path / "absent" / "alembic.ini")
    engine = sa.create_engine(engine_url(DEAD_DB))
    with pytest.raises(SeedError, match=r"alembic\.ini"):
        reset_app_state(engine, DEAD_DB)


def test_reset_refuses_a_hosted_database_in_dev(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "dev")
    hosted = "postgresql://demo:secret@db.example.com:5432/glasshouse"
    assert cli.main(["seed", "--reset", "--database-url", hosted]) == 1
    assert "local" in capsys.readouterr().err


def test_the_fence_rules() -> None:
    with pytest.raises(SeedError, match="production"):
        refuse_unsafe_reset(DEAD_DB, "production")
    with pytest.raises(SeedError, match="local"):
        refuse_unsafe_reset("postgresql://db.example.com/x", "dev")
    # A local database in dev and anything in demo (the nightly cron's
    # explicit opt-in) pass the fence.
    refuse_unsafe_reset("postgresql://localhost:5433/scratch", "dev")
    refuse_unsafe_reset("postgres:///morpholog_scratch", "dev")
    refuse_unsafe_reset("postgresql://db.example.com/x", "demo")


def test_the_report_renders_stably() -> None:
    report = SeedReport(org="acme-energy", books=2, trades=6, curves=2, amendments=1, valuations=13)
    assert report.render() == (
        "seeded acme-energy: 2 book(s), 6 trade(s), 2 curve version(s), 1 amendment(s), "
        "13 valuation(s), 0 position limit(s); verify: consistent"
    )
    anchored = SeedReport(
        org="acme-energy",
        books=2,
        trades=6,
        curves=2,
        amendments=1,
        valuations=13,
        limits=2,
        checkpoint_rows=38,
    )
    assert (
        "13 valuation(s), 2 position limit(s); checkpoint over 38 audit row(s); verify: consistent"
        in anchored.render()
    )


def _consistent(tmp_path: Path, transitions: int) -> GlasshouseClient:
    report = {
        "passes": True,
        "replay": {"status": "consistent", "transitions": transitions, "claims": 1},
        "tree": {"status": "intact", "checkpoints": 0, "tree_size": 0},
        "role_rebindings": {"status": "not_evaluated"},
    }
    binary = fake_binary(tmp_path, json.dumps(report))
    return GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))


def test_a_short_tail_is_refused_by_name_not_reported_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The ledger's replay says 32; the tail delivered none. The seed waits
    # out its patience, then refuses with both numbers and where to look,
    # instead of projecting nothing and calling it consistent.
    monkeypatch.setattr(seed, "rebuild", lambda _client, _engine: 0)
    engine = sa.create_engine(engine_url(DEAD_DB))
    try:
        with pytest.raises(SeedError, match="delivered 0 of 32 transition") as refused:
            seed.project_everything(_consistent(tmp_path, 32), engine, patience=0)
    finally:
        engine.dispose()
    assert "Potential horizon blockers" in str(refused.value)
    assert "pg_stat_activity" in str(refused.value)  # the dead database: a pointer, not a crash


def test_a_complete_tail_returns_what_it_projected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed, "rebuild", lambda _client, _engine: 32)
    assert (
        seed.project_everything(_consistent(tmp_path, 32), sa.create_engine(engine_url(DEAD_DB)))
        == 32
    )
