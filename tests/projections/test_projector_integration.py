"""The projector against the real ledger: the Monday-morning flow, then
catch-up, the killer query's tables, exactly-once application, and the
read-side law proven by rebuilding from zero and comparing.

Same gates and provisioning contract as the other integration legs; the
app schema is migrated by Alembic in the fixture, so revision 0002 is
part of what this test proves."""

import datetime as dt
import threading
from decimal import Decimal

import pytest
import sqlalchemy as sa

from glasshouse import cli
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import correct_curve_version, register_curve_version, value_trade
from glasshouse.compute.store import CurveStore
from glasshouse.projections import (
    blotter_trade,
    catch_up,
    position_hour,
    projection_progress,
    rebuild,
    trade_valuation,
)
from tests.support import BINARY, DB, needs_live_stack, provision

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
AS_OF = dt.date(2026, 6, 8)
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

TABLES = (blotter_trade, position_hour, trade_valuation, projection_progress)


pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    return provision()


@pytest.fixture(scope="module")
def morpholog(engine: sa.Engine) -> GlasshouseClient:
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    return client


def _rows(engine: sa.Engine) -> dict[str, list[tuple[object, ...]]]:
    snapshot = {}
    with engine.connect() as connection:
        for table in TABLES:
            rows = connection.execute(sa.select(table).order_by(*table.primary_key.columns))
            snapshot[table.name] = [tuple(row) for row in rows]
    return snapshot


def test_the_projector_keeps_up_with_the_monday_morning_loop(
    morpholog: GlasshouseClient, engine: sa.Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    store = CurveStore(engine)
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=BOOK),
    ):
        assert isinstance(morpholog.submit(grant, actor="bootstrap"), Committed)
    captured = morpholog.submit(
        models.CaptureTradeRequest(
            org=ORG,
            book=BOOK,
            trade="T-001",
            counterparty="stadtwerk-x",
            market=MARKET,
            direction="buy",
            quantity=Decimal("10"),
            price=Decimal("86.25"),
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=3),
        ),
        actor="alice",
    )
    assert isinstance(captured, Committed)
    v1 = HourlyCurve(
        tuple(
            (T0 + dt.timedelta(hours=i), p)
            for i, p in enumerate(map(Decimal, ["90", "88", "86.25"]))
        )
    )
    assert isinstance(
        register_curve_version(
            morpholog,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=AS_OF,
            version="crv-v1",
            curve=v1,
        ),
        Committed,
    )
    assert isinstance(
        value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001"),
        Committed,
    )

    # 3 grants + capture + registration + valuation, exactly once.
    assert catch_up(engine) == 6
    assert catch_up(engine) == 0

    rows = _rows(engine)
    (blotter,) = rows["blotter_trade"]
    assert blotter[:3] == (ORG, "T-001", BOOK)
    assert str(captured.transition_id) in blotter

    positions = rows["position_hour"]
    assert [(row[3], row[4]) for row in positions] == [
        (T0 + dt.timedelta(hours=h), Decimal("10")) for h in range(3)
    ]

    (valuation,) = rows["trade_valuation"]
    assert (valuation[2], valuation[4]) == ("crv-v1", Decimal("55.00"))

    # Correct the curve and re-mark: both valuations stand, each pinned
    # to its curve version; positions are untouched.
    assert isinstance(
        correct_curve_version(
            morpholog,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=AS_OF,
            prior_version="crv-v1",
            new_version="crv-v2",
            curve=HourlyCurve(tuple((start, price + 1) for start, price in v1.periods)),
        ),
        Committed,
    )
    assert isinstance(
        value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001"),
        Committed,
    )
    assert catch_up(engine) == 2

    rows = _rows(engine)
    marks = {row[2]: row[4] for row in rows["trade_valuation"]}
    assert marks == {"crv-v1": Decimal("55.00"), "crv-v2": Decimal("85.00")}
    assert len(rows["position_hour"]) == 3

    # The read-side law: wipe and replay from zero lands byte-for-byte
    # on the same read state (the seed of `glasshouse verify`).
    before = _rows(engine)
    assert rebuild(engine) == 8
    assert _rows(engine) == before

    # The worker's one-shot through the CLI seam.
    assert cli.main(["project", "--database-url", DB]) == 0
    assert "applied 0 transition(s)" in capsys.readouterr().out


def test_concurrent_projectors_serialise_and_apply_exactly_once(engine: sa.Engine) -> None:
    # Two workers racing over the same log: the advisory lock plus the
    # cursor-in-transaction make application exactly-once, not merely
    # PK-protected. Wipe and let them race over the full history.
    before = _rows(engine)
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(sa.delete(table))

    applied: list[int] = []
    workers = [threading.Thread(target=lambda: applied.append(catch_up(engine))) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert sum(applied) == 8
    assert _rows(engine) == before
