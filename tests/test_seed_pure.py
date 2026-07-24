"""The seed command's deterministic legs: the fences refuse before any
connection exists (a dead database URL proves none was attempted), the
reset preflight refuses before any destructive statement, and the report
renders stably.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa

from glasshouse import cli, seed
from glasshouse.compute.store import engine_url
from glasshouse.seed import SeedError, SeedReport, refuse_unsafe_reset, reset_app_state

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
    report = SeedReport(org="acme-energy", books=2, trades=6, curves=1, valuations=6)
    assert report.render() == (
        "seeded acme-energy: 2 book(s), 6 trade(s), 1 curve version(s), "
        "6 valuation(s); verify: consistent"
    )
