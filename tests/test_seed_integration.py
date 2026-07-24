"""`glasshouse seed` against the real stack: the reset path provisions,
seeds the Monday-morning dataset as governed traffic, verifies all six
legs before reporting, refuses a ledger with history, refuses to
overlap itself, and repeats cleanly (the nightly-cron semantics).

Same gating and provisioning contract as the other integration legs.
"""

import pytest
import sqlalchemy as sa

from glasshouse import cli
from glasshouse.compute.store import engine_url
from glasshouse.projections.tables import blotter_trade, position_hour, trade_valuation
from glasshouse.seed import SEED_LOCK_KEY
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack


@pytest.fixture(autouse=True)
def live_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)


def test_seed_reset_seeds_verifies_and_repeats(capsys: pytest.CaptureFixture[str]) -> None:
    provision()  # a clean slate whatever ran before this module

    assert cli.main(["seed", "--reset", "--database-url", DB]) == 0
    out = capsys.readouterr().out
    assert "seeded acme-energy: 2 book(s), 6 trade(s)" in out
    assert "verify: consistent" in out  # printed only after all six legs passed

    engine = sa.create_engine(engine_url(DB))
    with engine.connect() as connection:
        trades = connection.execute(sa.select(blotter_trade)).all()
        assert len(trades) == 6
        assert {t.book for t in trades} == {"spec-de", "hedge-de"}
        assert {t.counterparty for t in trades} == {"stadtwerk-x", "nordkraft"}
        # Every screen has a negative to show: a net-short delivery hour
        # and a mark struck above the curve.
        assert connection.execute(
            sa.select(sa.func.count()).where(position_hour.c.net_mw < 0)
        ).scalar()
        assert connection.execute(
            sa.select(sa.func.count()).where(trade_valuation.c.mtm < 0)
        ).scalar()
        marks = connection.execute(sa.select(sa.func.count()).select_from(trade_valuation))
        assert marks.scalar() == 6

    # Plain seed is idempotent by refusal: any ledger history refuses.
    assert cli.main(["seed", "--database-url", DB]) == 1
    assert "already has transitions" in capsys.readouterr().err
    with engine.connect() as connection:
        count = connection.execute(sa.select(sa.func.count()).select_from(blotter_trade))
        assert count.scalar() == 6  # the refusal changed nothing

    # The nightly-cron semantics: a second reset succeeds from the top.
    assert cli.main(["seed", "--reset", "--database-url", DB]) == 0
    assert "verify: consistent" in capsys.readouterr().out


def test_seed_refuses_to_overlap_itself(capsys: pytest.CaptureFixture[str]) -> None:
    engine = sa.create_engine(engine_url(DB))
    with engine.connect() as guard:
        guard.execute(sa.text("select pg_advisory_lock(:key)"), {"key": SEED_LOCK_KEY})
        try:
            assert cli.main(["seed", "--reset", "--database-url", DB]) == 1
            assert "already running" in capsys.readouterr().err
        finally:
            guard.execute(sa.text("select pg_advisory_unlock(:key)"), {"key": SEED_LOCK_KEY})
