"""The governed business date (glasshouse#44), against the real binary.

The model carries one official curve per (org, market, as-of date), so
a desk that registers a curve every day has several official curves at
once. Which one a valuation uses is the organisation's business date: a
fact in the ledger, rolled forward by an act, never back, never the
clock. A mark against another day's curve is refused by the ledger's own
gate; `value_trade` reads the date and picks that day's curve; a curve
correction re-marks only on the business date.

Self-provisioned module slate.
"""

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
import sqlalchemy as sa
from typer.testing import CliRunner

from glasshouse import cli
from glasshouse.api import business_day
from glasshouse.commit import (
    MODEL_FILE,
    Committed,
    GlasshouseClient,
    MorphologRequestError,
    Rejected,
    models,
)
from glasshouse.commit.morpholog_client.envelopes import AtomicCommitted
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import (
    MarkingError,
    business_date_of,
    correct_and_remark,
    register_curve_version,
    value_trade,
)
from glasshouse.compute.store import CurveStore
from glasshouse.compute.terms import terms_version_id
from tests.support import BINARY, DB, committed, login_role, needs_live_stack, provision, refused

pytestmark = [needs_live_stack, pytest.mark.usefixtures("cli_binary")]

BOOK, MARKET = "book-a", "de-power"
TRADE_DATE = dt.date(2026, 6, 1)
DAY_ONE = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
DAY_TWO = dt.datetime(2026, 7, 2, tzinfo=dt.UTC)


def _day_curve(day: dt.datetime, base: int = 80) -> HourlyCurve:
    """The curve published on `day`: prices for both delivery days, so a
    later day's curve still prices the trade (and prices it differently)."""
    shift = (day - DAY_ONE).days * 5
    return HourlyCurve(
        tuple((DAY_ONE + dt.timedelta(hours=h), Decimal(str(base + shift + h))) for h in range(48))
    )


@pytest.fixture(scope="module")
def stack() -> tuple[GlasshouseClient, CurveStore, sa.Engine]:
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    client.init()
    committed(client, models.EstablishLedgerRequest(login_role=login_role()), "bootstrap")
    return client, CurveStore(engine), engine


def _desk(client: GlasshouseClient, store: CurveStore, org: str) -> None:
    """An organisation of its own: one trade, and two official curves at
    once, one per date, as a desk registering a curve every day has from
    its second day. No business date yet."""
    for grant in (
        models.EstablishOrganisationRequest(org=org),
        models.DeclareMarketRequest(org=org, market=MARKET, currency="EUR"),
        models.GrantCaptureAuthorityRequest(principal="alice", org=org, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=org, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=org, book=BOOK),
        models.GrantDateAuthorityRequest(principal="ops", org=org),
    ):
        committed(client, grant, "bootstrap")
    committed(
        client,
        models.CaptureTradeRequest(
            org=org,
            book=BOOK,
            trade=f"{org}/DB-1",
            counterparty="cp",
            market=MARKET,
            direction="buy",
            version=terms_version_id(f"{org}/DB-1", 1),
            quantity=Decimal("5"),
            price=Decimal("82"),
            delivery_start=DAY_ONE + dt.timedelta(hours=8),
            delivery_end=DAY_ONE + dt.timedelta(hours=10),
            trade_date=TRADE_DATE,
        ),
        "alice",
    )
    for day, version in ((DAY_ONE, "jul1"), (DAY_TWO, "jul2")):
        registered = register_curve_version(
            client,
            store,
            actor="carol",
            org=org,
            market=MARKET,
            as_of=day.date(),
            version=f"{org}/{version}",
            curve=_day_curve(day),
        )
        assert isinstance(registered, Committed)


def _value(client: GlasshouseClient, store: CurveStore, org: str) -> object:
    return value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/DB-1")


def test_the_business_date_decides_which_curve_values_and_only_moves_forward(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    client, store, _ = stack
    org = "date-rolls"
    _desk(client, store, org)
    # No business date yet: nothing can be valued, by the flow or the rule.
    assert business_date_of(client, org=org) is None
    with pytest.raises(MarkingError, match="roll-business-date"):
        _value(client, store, org)
    no_date = models.AdmitValuationRequest(
        org=org,
        book=BOOK,
        trade=f"{org}/DB-1",
        curve_version=f"{org}/jul1",
        terms_version=f"{org}/DB-1/v1",
        mtm=Decimal("0"),
    )
    assert refused(client, no_date, "risk-engine").rule == "business_date_is_set"

    # Only the right's holder rolls it; the first roll may be any date.
    roll = models.RollBusinessDateRequest(org=org, date=DAY_ONE.date())
    assert refused(client, roll, "alice").rule == "may_roll_business_date"
    committed(client, roll, "ops")
    assert business_date_of(client, org=org) == DAY_ONE.date()

    # On day one the day-one curve values; the day-two curve is refused.
    first = _value(client, store, org)
    assert isinstance(first, Committed)
    against_tomorrow = models.AdmitValuationRequest(
        org=org,
        book=BOOK,
        trade=f"{org}/DB-1",
        curve_version=f"{org}/jul2",
        terms_version=f"{org}/DB-1/v1",
        mtm=Decimal("0"),
    )
    assert (
        refused(client, against_tomorrow, "risk-engine").rule
        == "valued_against_official_curve_and_terms_in_force"
    )

    # Rolled to day two, the day-two curve values; back is refused.
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_TWO.date()), "ops")
    second = _value(client, store, org)
    assert isinstance(second, Committed)
    marks = {
        v.curve_version for v in client.read(models.TradeValuedClaim) if v.trade == f"{org}/DB-1"
    }
    assert marks == {f"{org}/jul1", f"{org}/jul2"}
    back = models.RollBusinessDateRequest(org=org, date=DAY_ONE.date())
    assert refused(client, back, "ops").rule == "date_moves_forward"
    same = models.RollBusinessDateRequest(org=org, date=DAY_TWO.date())
    assert refused(client, same, "ops").rule == "date_moves_forward"


def test_a_correction_re_marks_only_on_the_business_date(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    client, store, _ = stack
    org = "date-corrects"
    _desk(client, store, org)
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_ONE.date()), "ops")
    assert isinstance(_value(client, store, org), Committed)
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_TWO.date()), "ops")
    assert isinstance(_value(client, store, org), Committed)
    # Correcting day one's prices, a day already rolled past: corrected,
    # and nothing re-marked; the business day's marks stay as they are.
    past = correct_and_remark(
        client,
        store,
        curve_actor="carol",
        valuation_actor="risk-engine",
        org=org,
        market=MARKET,
        as_of=DAY_ONE.date(),
        prior_version=f"{org}/jul1",
        new_version=f"{org}/jul1-fix",
        curve=_day_curve(DAY_ONE, base=81),
    )
    assert isinstance(past, AtomicCommitted)
    assert len(past.acts) == 1
    # Correcting the business day's prices re-marks the trade with it.
    today = correct_and_remark(
        client,
        store,
        curve_actor="carol",
        valuation_actor="risk-engine",
        org=org,
        market=MARKET,
        as_of=DAY_TWO.date(),
        prior_version=f"{org}/jul2",
        new_version=f"{org}/jul2-fix",
        curve=_day_curve(DAY_TWO, base=81),
    )
    assert isinstance(today, AtomicCommitted)
    assert len(today.acts) == 2
    assert isinstance(_value(client, store, org), Rejected)  # already marked there


def test_the_cli_rolls_the_business_date(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    client, store, _ = stack
    org = "date-cli"
    _desk(client, store, org)
    later = DAY_TWO.date() + dt.timedelta(days=5)
    common = ["--org", org, "--actor", "ops", "--database-url", DB]
    rolled = CliRunner().invoke(
        cli.app, ["roll-business-date", *common, "--date", later.isoformat()]
    )
    assert rolled.exit_code == 0, rolled.output
    assert f"business date for {org}: {later}" in rolled.output
    assert business_date_of(client, org=org) == later
    badly = CliRunner().invoke(cli.app, ["roll-business-date", *common, "--date", "tomorrow"])
    assert badly.exit_code == 2
    assert "ISO date" in badly.output


def _second_trade(client: GlasshouseClient, org: str) -> None:
    committed(
        client,
        models.CaptureTradeRequest(
            org=org,
            book=BOOK,
            trade=f"{org}/DB-2",
            counterparty="cp",
            market=MARKET,
            direction="sell",
            version=terms_version_id(f"{org}/DB-2", 1),
            quantity=Decimal("3"),
            price=Decimal("90"),
            delivery_start=DAY_ONE + dt.timedelta(hours=12),
            delivery_end=DAY_ONE + dt.timedelta(hours=14),
            trade_date=TRADE_DATE,
        ),
        "alice",
    )


def test_after_a_roll_yesterdays_values_are_not_todays(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    client, store, _ = stack
    org = "date-stale"
    _desk(client, store, org)
    _second_trade(client, org)
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_ONE.date()), "ops")
    for trade in ("DB-1", "DB-2"):
        valued = value_trade(
            client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/{trade}"
        )
        assert isinstance(valued, Committed)
    monday = business_day.read(client, org=org)
    assert (monday.date, monday.unvalued(), len(monday.current())) == (DAY_ONE.date(), 0, 2)
    (monday_total,) = monday.totals()

    # Rolled, nothing revalued: no value is today's, and none is counted.
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_TWO.date()), "ops")
    tuesday = business_day.read(client, org=org)
    assert (tuesday.date, tuesday.totals(), tuesday.unvalued()) == (DAY_TWO.date(), [], 2)
    assert tuesday.current() == set()

    # One of two revalued: the total is that one alone, never a mix of
    # Monday's and Tuesday's marks.
    value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/DB-1")
    partly = business_day.read(client, org=org)
    (total,) = partly.totals()
    assert (total.trade_count, partly.unvalued()) == (1, 1)
    (row,) = [r for r in partly.rows if r.valued]
    assert (row.trade, total.total_mtm) == (f"{org}/DB-1", row.mtm)
    assert monday_total.trade_count == 2


def test_correcting_a_past_day_moves_neither_today_nor_that_days_record(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    client, store, _ = stack
    org = "date-restate"
    _desk(client, store, org)
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_ONE.date()), "ops")
    value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/DB-1")
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_TWO.date()), "ops")
    value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/DB-1")
    before = business_day.read(client, org=org).totals()
    marks_before = {
        (v.curve_version, v.mtm) for v in client.read(models.TradeValuedClaim) if v.org == org
    }
    corrected = correct_and_remark(
        client,
        store,
        curve_actor="carol",
        valuation_actor="risk-engine",
        org=org,
        market=MARKET,
        as_of=DAY_ONE.date(),
        prior_version=f"{org}/jul1",
        new_version=f"{org}/jul1-fix",
        curve=_day_curve(DAY_ONE, base=95),
    )
    assert isinstance(corrected, AtomicCommitted)
    assert len(corrected.acts) == 1  # the correction alone: nothing re-marked
    # Today's total is untouched, and Monday's mark as struck is still on
    # record. Restating Monday under the corrected prices is not built.
    assert business_day.read(client, org=org).totals() == before
    marks_after = {
        (v.curve_version, v.mtm) for v in client.read(models.TradeValuedClaim) if v.org == org
    }
    assert marks_after == marks_before


def test_a_roll_racing_a_mark_never_admits_a_mark_for_a_day_already_left(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    # The curve is chosen in Python, the decision is the ledger's. A mark
    # against day one racing the roll to day two either commits first (on
    # day one, lawfully) or is refused; it never lands after the roll.
    client, store, _ = stack
    org = "date-race"
    _desk(client, store, org)
    committed(client, models.RollBusinessDateRequest(org=org, date=DAY_ONE.date()), "ops")
    mark = models.AdmitValuationRequest(
        org=org,
        book=BOOK,
        trade=f"{org}/DB-1",
        curve_version=f"{org}/jul1",
        terms_version=f"{org}/DB-1/v1",
        mtm=Decimal("1"),
    )
    roll = models.RollBusinessDateRequest(org=org, date=DAY_TWO.date())

    def submit(job: tuple[object, str]) -> object:
        request, actor = job
        own = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
        for _ in range(5):
            try:
                return own.submit(request, actor=actor)  # type: ignore[arg-type]
            except MorphologRequestError as raced:
                if not raced.retriable:
                    raise
        raise AssertionError("still racing after five attempts")

    with ThreadPoolExecutor(max_workers=2) as pool:
        marked, rolled = pool.map(submit, [(mark, "risk-engine"), (roll, "ops")])
    assert isinstance(rolled, Committed)
    if isinstance(marked, Committed):
        order = [row.transition_id for row in client.audit_named(mentions=org)]
        assert order.index(marked.transition_id) < order.index(rolled.transition_id)
    else:
        assert isinstance(marked, Rejected)
        assert marked.rule == "valued_against_official_curve_and_terms_in_force"
