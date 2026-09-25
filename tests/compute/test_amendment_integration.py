"""Trade amendment against the real binary: a new terms version
supersedes the old, the ledger names which version a mark may be struck
under on a given business date (bitemporally: the terms in force on the
curve's date, not the newest), lineage is a chain, and the correction
flow re-marks each trade under the terms in force.

Self-provisioned module slate; each test builds its own organisation's
desk, so they pass alone and in any order.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, Rejected, models
from glasshouse.commit.morpholog_client.envelopes import AtomicCommitted, GateRejection
from glasshouse.compute.amendment import AmendmentError, amend_trade
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import (
    MarkingError,
    correct_and_remark,
    register_curve_version,
    value_trade,
)
from glasshouse.compute.store import CurveStore
from glasshouse.compute.terms import terms_version_id
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

BOOK, MARKET = "book-a", "de-power"
TRADE_DATE = dt.date(2026, 6, 1)
BETWEEN = dt.date(2026, 6, 5)  # after the trade date, before the amendment
AS_OF = dt.date(2026, 6, 8)  # the curve's business date, and the amendment's
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def _curve(*prices: str) -> HourlyCurve:
    return HourlyCurve(
        tuple((T0 + dt.timedelta(hours=i), Decimal(p)) for i, p in enumerate(prices))
    )


@pytest.fixture(scope="module")
def stack() -> tuple[GlasshouseClient, CurveStore]:
    engine: sa.Engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    client.init()
    return client, CurveStore(engine)


def _desk(
    client: GlasshouseClient, store: CurveStore, org: str, *, trade_date: dt.date = TRADE_DATE
) -> None:
    """Grants, a buy and a sell on the market (10 MW over two hours at
    80), and an official curve for AS_OF."""
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="alice", org=org, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=org, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=org, book=BOOK),
    ):
        assert isinstance(client.submit(grant, actor="bootstrap"), Committed)
    for trade, direction in (("T-1", "buy"), ("T-2", "sell")):
        capture = models.CaptureTradeRequest(
            org=org,
            book=BOOK,
            trade=f"{org}/{trade}",
            counterparty="cp",
            market=MARKET,
            direction=direction,
            version=terms_version_id(f"{org}/{trade}", 1),
            quantity=Decimal("10"),
            price=Decimal("80"),
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=2),
            trade_date=trade_date,
        )
        assert isinstance(client.submit(capture, actor="alice"), Committed)
    assert isinstance(
        register_curve_version(
            client,
            store,
            actor="carol",
            org=org,
            market=MARKET,
            as_of=AS_OF,
            version=f"{org}/crv-v1",
            curve=_curve("84", "85"),
        ),
        Committed,
    )


def _amend(client: GlasshouseClient, org: str, quantity: str, effective: dt.date) -> object:
    return amend_trade(
        client,
        actor="alice",
        org=org,
        trade=f"{org}/T-1",
        quantity=Decimal(quantity),
        price=Decimal("80"),
        delivery_start=T0,
        delivery_end=T0 + dt.timedelta(hours=2),
        effective_from=effective,
    )


def _mark(client: GlasshouseClient, org: str, curve: str, terms: str) -> object:
    return client.submit(
        models.AdmitValuationRequest(
            org=org,
            book=BOOK,
            trade=f"{org}/T-1",
            curve_version=curve,
            terms_version=terms,
            mtm=Decimal("0"),
        ),
        actor="risk-engine",
        explain_on_reject=True,
    )


def test_an_amendment_supersedes_and_the_mark_follows_the_terms_in_force(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    client, store = stack
    org = "amend-ok"
    _desk(client, store, org)

    assert isinstance(_amend(client, org, "20", AS_OF), Committed)
    # The flow derived the lineage from the tip and minted the next id.
    versions = sorted(
        (t for t in client.read(models.TradeTermsClaim) if t.trade == f"{org}/T-1"),
        key=lambda t: t.effective_from,
    )
    assert [(t.version, t.quantity) for t in versions] == [
        (f"{org}/T-1/v1", Decimal("10")),
        (f"{org}/T-1/v2", Decimal("20")),
    ]
    (lineage,) = [
        s for s in client.read(models.TradeTermsSupersedesClaim) if s.new_version == f"{org}/T-1/v2"
    ]
    assert lineage.prior_version == f"{org}/T-1/v1"

    # The compute flow values under the version in force on the curve's
    # date: 20 MW over two hours against 84/85 at a strike of 80 = 180.
    valued = value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/T-1")
    assert isinstance(valued, Committed)
    (mark,) = [v for v in client.read(models.TradeValuedClaim) if v.trade == f"{org}/T-1"]
    assert (mark.terms_version, mark.mtm) == (f"{org}/T-1/v2", Decimal("180"))

    # A hand-built mark naming the superseded version is refused at the
    # selector conjunct of the gate - the ledger decides, this code only
    # predicted.
    stale = _mark(client, org, f"{org}/crv-v1", f"{org}/T-1/v1")
    assert isinstance(stale, Rejected)
    assert stale.explanation is not None
    assert isinstance(stale.explanation.rejection, GateRejection)


def test_lineage_is_a_chain_and_dates_strictly_increase(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    client, store = stack
    org = "amend-chain"
    _desk(client, store, org)
    assert isinstance(_amend(client, org, "20", AS_OF), Committed)

    # Effective on (or before) the prior's date: refused at the gate.
    assert isinstance(_amend(client, org, "30", AS_OF), Rejected)
    assert isinstance(_amend(client, org, "30", TRADE_DATE), Rejected)
    # A fork off the superseded version, by hand: refused at the no-fork gate.
    fork = client.submit(
        models.AmendTradeRequest(
            org=org,
            trade=f"{org}/T-1",
            prior_version=f"{org}/T-1/v1",
            new_version=f"{org}/T-1/v3",
            quantity=Decimal("30"),
            price=Decimal("80"),
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=2),
            effective_from=AS_OF + dt.timedelta(days=1),
        ),
        actor="alice",
    )
    assert isinstance(fork, Rejected)
    # The flow finds the tip on its own and extends the chain.
    assert isinstance(_amend(client, org, "30", AS_OF + dt.timedelta(days=1)), Committed)
    tip = max(
        (t for t in client.read(models.TradeTermsClaim) if t.trade == f"{org}/T-1"),
        key=lambda t: t.effective_from,
    )
    assert (tip.version, tip.quantity) == (f"{org}/T-1/v3", Decimal("30"))

    with pytest.raises(AmendmentError, match="capture it first"):
        _amend(client, "no-such-org", "5", AS_OF)


def test_the_ledger_is_bitemporal_about_terms(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    # A curve for a business date BETWEEN the trade date and the
    # amendment: on that date the first version governs, on the later
    # date the amendment does, and the ledger admits exactly those pairs.
    client, store = stack
    org = "amend-bitemporal"
    _desk(client, store, org)
    assert isinstance(_amend(client, org, "20", AS_OF), Committed)
    assert isinstance(
        register_curve_version(
            client,
            store,
            actor="carol",
            org=org,
            market=MARKET,
            as_of=BETWEEN,
            version=f"{org}/crv-between",
            curve=_curve("84", "85"),
        ),
        Committed,
    )
    assert isinstance(_mark(client, org, f"{org}/crv-between", f"{org}/T-1/v1"), Committed)
    assert isinstance(_mark(client, org, f"{org}/crv-between", f"{org}/T-1/v2"), Rejected)
    assert isinstance(_mark(client, org, f"{org}/crv-v1", f"{org}/T-1/v2"), Committed)
    assert isinstance(_mark(client, org, f"{org}/crv-v1", f"{org}/T-1/v1"), Rejected)
    # Two official business dates stand: the compute flow still refuses
    # to choose between them by name (glasshouse#44), unchanged.
    with pytest.raises(MarkingError, match="glasshouse#44"):
        value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/T-1")


def test_a_trade_struck_after_the_curve_date_has_no_terms_in_force(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    client, store = stack
    org = "amend-future"
    _desk(client, store, org, trade_date=AS_OF + dt.timedelta(days=2))
    with pytest.raises(MarkingError, match=r"no terms of trade .* are in force on 2026-06-08"):
        value_trade(client, store, actor="risk-engine", org=org, book=BOOK, trade=f"{org}/T-1")


def test_a_correction_re_marks_each_trade_under_its_terms_in_force(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    client, store = stack
    org = "amend-correct"
    _desk(client, store, org)
    assert isinstance(_amend(client, org, "20", AS_OF), Committed)

    outcome = correct_and_remark(
        client,
        store,
        curve_actor="carol",
        valuation_actor="risk-engine",
        org=org,
        market=MARKET,
        as_of=AS_OF,
        prior_version=f"{org}/crv-v1",
        new_version=f"{org}/crv-v2",
        curve=_curve("86", "87"),
    )
    assert isinstance(outcome, AtomicCommitted)
    marks = {
        (v.trade, v.terms_version): v.mtm
        for v in client.read(models.TradeValuedClaim)
        if v.org == org and v.curve_version == f"{org}/crv-v2"
    }
    # T-1 under its amended 20 MW (20 * (6 + 7) = 260), T-2 under its
    # first version (-10 * 13 = -130): each trade's own in-force terms.
    assert marks == {
        (f"{org}/T-1", f"{org}/T-1/v2"): Decimal("260"),
        (f"{org}/T-2", f"{org}/T-2/v1"): Decimal("-130"),
    }
