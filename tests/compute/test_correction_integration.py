"""A curve correction and its re-marks as one decision (`transact`),
against the real binary: every act commits together, or a refusal at
any act writes nothing and gives the version id back.

Self-provisioned module slate; each test builds its own organisation's
desk, so they pass alone and in any order.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.commit.morpholog_client.envelopes import AtomicCommitted, AtomicRejected
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import correct_and_remark, register_curve_version
from glasshouse.compute.store import CurveStore, StoreError
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

BOOK, MARKET = "book-a", "de-power"
AS_OF = dt.date(2026, 7, 1)
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


def _desk(client: GlasshouseClient, store: CurveStore, org: str) -> None:
    """Grants, a buy and a sell on the market, and v1 official."""
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
            quantity=Decimal("10"),
            price=Decimal("80"),
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=2),
        )
        assert isinstance(client.submit(capture, actor="alice"), Committed)
    registered = register_curve_version(
        client,
        store,
        actor="carol",
        org=org,
        market=MARKET,
        as_of=AS_OF,
        version=f"{org}/v1",
        curve=_curve("81", "82"),
    )
    assert isinstance(registered, Committed)


def _official(client: GlasshouseClient, org: str) -> str:
    [pointer] = [o for o in client.read(models.OfficialCurveClaim) if o.org == org]
    return pointer.version


def _correct(
    client: GlasshouseClient, store: CurveStore, org: str, *, valuation_actor: str
) -> AtomicCommitted | AtomicRejected:
    return correct_and_remark(
        client,
        store,
        curve_actor="carol",
        valuation_actor=valuation_actor,
        org=org,
        market=MARKET,
        as_of=AS_OF,
        prior_version=f"{org}/v1",
        new_version=f"{org}/v2",
        curve=_curve("84", "85"),
    )


def test_a_correction_and_its_remarks_commit_as_one_decision(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    client, store = stack
    org = "atomic-ok"
    _desk(client, store, org)

    outcome = _correct(client, store, org, valuation_actor="risk-engine")

    assert isinstance(outcome, AtomicCommitted)
    assert [act.row for act in outcome.acts] == [1, 2, 3]
    assert _official(client, org) == f"{org}/v2"
    # 10 MW over two hours against 84/85 at a strike of 80: +90 bought,
    # -90 sold - marked against the correction the same decision staged.
    marks = {
        v.trade: v.mtm
        for v in client.read(models.TradeValuedClaim)
        if v.org == org and v.curve_version == f"{org}/v2"
    }
    assert marks == {f"{org}/T-1": Decimal("90"), f"{org}/T-2": Decimal("-90")}


def test_a_refused_act_writes_nothing_and_gives_the_version_back(
    stack: tuple[GlasshouseClient, CurveStore],
) -> None:
    client, store = stack
    org = "atomic-refused"
    _desk(client, store, org)

    # The correction itself is lawful; the first re-mark is not (no
    # valuation authority). The whole decision is refused at act 2.
    refused = _correct(client, store, org, valuation_actor="mallory")

    assert isinstance(refused, AtomicRejected)
    assert refused.act == 2
    assert _official(client, org) == f"{org}/v1"  # the correction did not land either
    assert not [v for v in client.read(models.TradeValuedClaim) if v.curve_version == f"{org}/v2"]
    with pytest.raises(StoreError):
        store.load(org=org, version=f"{org}/v2")

    # The version id was given back: the legitimate correction reuses it.
    assert isinstance(_correct(client, store, org, valuation_actor="risk-engine"), AtomicCommitted)
