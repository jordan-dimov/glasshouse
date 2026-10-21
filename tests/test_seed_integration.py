"""`glasshouse seed` against the real stack: the reset path provisions,
seeds the Monday-morning dataset as governed traffic, verifies all six
legs before reporting, refuses a ledger with history, refuses to
overlap itself, and repeats cleanly (the nightly-cron semantics).

Same gating and provisioning contract as the other integration legs.
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa

from glasshouse import cli
from glasshouse.compute.store import engine_url
from glasshouse.projections.tables import (
    blotter_trade,
    position_hour,
    trade_terms_version,
    trade_valuation,
)
from glasshouse.seed import SEED_LOCK_KEY, SeedError, run_seed
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
    # The close is checkpointed covering its last transition, so the
    # seeded ledger's history tree has a head to prove (one checkpoint,
    # over every audit row the story wrote).
    assert "2 position limit(s); checkpoint over 38 audit row(s); verify" in out

    engine = sa.create_engine(engine_url(DB))
    with engine.connect() as connection:
        checkpoints = connection.execute(
            sa.text("select count(*), max(tree_size) from morpholog.audit_checkpoints")
        ).one()
        assert tuple(checkpoints) == (1, 38)
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
        # Two marks per trade (v1 and the corrected v2) plus T-003's
        # re-mark under its amended terms.
        assert marks.scalar() == 13
        amended = connection.execute(
            sa.select(blotter_trade).where(blotter_trade.c.trade == "T-003")
        ).one()
        assert (amended.quantity, amended.terms_version, amended.amendment_count) == (
            Decimal("8"),
            "T-003/v2",
            1,
        )
        versions = connection.execute(sa.select(sa.func.count()).select_from(trade_terms_version))
        assert versions.scalar() == 7  # six first versions and one amendment

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


def test_a_tail_held_short_fails_the_seed_by_name() -> None:
    # The audit tail withholds every row younger than the oldest open
    # transaction in the database (the binary's slow-writer horizon:
    # withheld, never lost). A TimescaleDB background worker in a fresh
    # database has held one across a whole seed, which projected nothing
    # and still reported "verify: consistent". A transaction held open
    # here plays that worker: the seed must refuse, naming it.
    provision()
    holder_engine = sa.create_engine(
        engine_url(DB), connect_args={"application_name": "held-open-by-test"}
    )
    try:
        with holder_engine.connect() as holder:
            holder.execute(sa.text("SELECT 1"))  # autobegin: a transaction is now open
            with pytest.raises(SeedError, match=r"delivered 0 of \d+ transition") as refused:
                run_seed(DB, reset=True, patience=0)
            assert "held-open-by-test" in str(refused.value)
    finally:
        holder_engine.dispose()
