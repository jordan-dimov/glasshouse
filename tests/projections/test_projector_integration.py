"""The projector against the real ledger: the Monday-morning flow, then
catch-up, the killer query's tables, exactly-once application, and the
read-side law proven by rebuilding from zero and comparing.

The committed history is a module fixture and every test enters through
`rebuild`/`catch_up`, which are deterministic from any prior read-side
state - so each test passes alone, in any order, under any selection.

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
    ProjectionError,
    accumulate,
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


pytestmark = [needs_live_stack, pytest.mark.usefixtures("cli_binary")]


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    return provision()


@pytest.fixture(scope="module")
def morpholog(engine: sa.Engine) -> GlasshouseClient:
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    return client


@pytest.fixture(scope="module")
def history(morpholog: GlasshouseClient, engine: sa.Engine) -> tuple[int, str]:
    """The Monday-morning flow, committed once for the module: grants,
    capture, registration, valuation, correction, re-valuation. Returns
    (transition count, the capture's transition id). Deliberately does
    not project anything: tests own their read-side entry."""
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
    # Correct the curve and re-mark: both valuations will stand, each
    # pinned to its curve version.
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
    return 8, captured.transition_id


def _rows(engine: sa.Engine) -> dict[str, list[tuple[object, ...]]]:
    snapshot = {}
    with engine.connect() as connection:
        for table in TABLES:
            rows = connection.execute(sa.select(table).order_by(*table.primary_key.columns))
            snapshot[table.name] = [tuple(row) for row in rows]
    return snapshot


def test_the_projector_replays_the_monday_morning_loop(
    history: tuple[int, str],
    morpholog: GlasshouseClient,
    engine: sa.Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transitions, capture_tid = history
    # The deterministic entry from any prior read-side state: replay
    # from zero, then prove there is nothing left (exactly once).
    assert rebuild(morpholog, engine) == transitions
    assert catch_up(morpholog, engine) == 0

    rows = _rows(engine)
    (blotter,) = rows["blotter_trade"]
    assert blotter[:3] == (ORG, "T-001", BOOK)
    assert capture_tid in blotter
    assert blotter[-1] == "alice"  # the evidence trail: who captured it

    positions = rows["position_hour"]
    assert [(row[3], row[4]) for row in positions] == [
        (T0 + dt.timedelta(hours=h), Decimal("10")) for h in range(3)
    ]

    # Both marks stand after the correction, each pinned to its version.
    marks = {row[2]: row[4] for row in rows["trade_valuation"]}
    assert marks == {"crv-v1": Decimal("55.00"), "crv-v2": Decimal("85.00")}
    assert {row[-1] for row in rows["trade_valuation"]} == {"risk-engine"}

    # The in-memory replay (verify's projection leg) lands on exactly
    # the rows the SQL applier produced - memory and SQL agree.
    assert accumulate(morpholog) == {name: set(table_rows) for name, table_rows in rows.items()}

    # The read-side law: wipe and replay from zero lands byte-for-byte
    # on the same read state (the seed of `glasshouse verify`).
    assert rebuild(morpholog, engine) == transitions
    assert _rows(engine) == rows

    # The worker's one-shot through the CLI seam.
    assert cli.main(["project", "--database-url", DB]) == 0
    assert "applied 0 transition(s)" in capsys.readouterr().out


def test_concurrent_projectors_serialise_and_apply_exactly_once(
    history: tuple[int, str], morpholog: GlasshouseClient, engine: sa.Engine
) -> None:
    # Two workers racing over the same log: the advisory lock plus the
    # cursor-in-transaction make application exactly-once, not merely
    # PK-protected. Bring the read side current, wipe, and let them
    # race over the full history; both must return cleanly.
    transitions, _ = history
    catch_up(morpholog, engine)
    before = _rows(engine)
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(sa.delete(table))

    applied: list[int] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            applied.append(catch_up(morpholog, engine))
        except BaseException as failure:
            errors.append(failure)

    workers = [threading.Thread(target=worker) for _ in range(2)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(timeout=30)

    assert not [thread for thread in workers if thread.is_alive()]
    assert errors == []
    assert len(applied) == 2
    assert sum(applied) == transitions
    assert _rows(engine) == before


def test_accumulate_stops_at_the_cursor_and_refuses_an_unknown_one(
    history: tuple[int, str], morpholog: GlasshouseClient, engine: sa.Engine
) -> None:
    # Up to the first transition (a grant), the expected read side is
    # empty tables with the cursor at that transition - the projector's
    # invariant at that point in history.
    first = morpholog.audit()[0]
    partial = accumulate(morpholog, up_to=first.transition_id)
    assert partial["blotter_trade"] == set()
    assert partial["position_hour"] == set()
    assert partial["trade_valuation"] == set()
    ((name, _, tid),) = partial["projection_progress"]
    assert (name, tid) == ("needle", first.transition_id)

    # A cursor naming a transition the tail does not contain is
    # corruption, never lag.
    with pytest.raises(ProjectionError, match="does not describe this ledger"):
        accumulate(morpholog, up_to="0197-no-such-transition")
