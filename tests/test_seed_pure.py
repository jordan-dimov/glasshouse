"""The seed command's deterministic legs: the destructive fence refuses
before any connection exists (a dead database URL proves none was
attempted), and the report renders stably.
"""

import pytest

from glasshouse import cli
from glasshouse.seed import SeedError, SeedReport, refuse_unsafe_reset

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


def test_reset_refuses_in_production(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "production")
    assert cli.main(["seed", "--reset", "--database-url", DEAD_DB]) == 1
    assert "production" in capsys.readouterr().err


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
