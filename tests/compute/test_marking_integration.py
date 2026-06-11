"""The Monday-morning loop against the real binary and a real database:
provision, grant, capture, register a curve with its payload anchored,
mark the trade, correct the curve, re-mark - and refuse to compute from
a payload that disagrees with the ledger.

Skips cleanly unless a morpholog binary and a disposable database are
reachable (same gates as the adapter integration test). The app schema
is migrated by Alembic in the fixture, so the migration itself is part
of what this test proves.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, Rejected
from glasshouse.commit.morpholog_client.models import (
    CaptureTradeRequest,
    GrantCaptureAuthorityRequest,
    GrantCurveAuthorityRequest,
    GrantValuationAuthorityRequest,
    TradeValuedClaim,
)
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import (
    MarkingError,
    correct_curve_version,
    register_curve_version,
    value_trade,
)
from glasshouse.compute.store import CurveStore, StoreError
from tests.support import BINARY, DB, needs_live_stack, provision

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
AS_OF = dt.date(2026, 6, 8)
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


pytestmark = needs_live_stack


def curve_of(*prices: str) -> HourlyCurve:
    return HourlyCurve(
        tuple((T0 + dt.timedelta(hours=i), Decimal(p)) for i, p in enumerate(prices))
    )


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    return provision()


@pytest.fixture(scope="module")
def morpholog(engine: sa.Engine) -> GlasshouseClient:
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    return client


@pytest.fixture(scope="module")
def store(engine: sa.Engine) -> CurveStore:
    return CurveStore(engine)


def test_the_monday_morning_loop(morpholog: GlasshouseClient, store: CurveStore) -> None:
    for grant in (
        GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
        GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=BOOK),
    ):
        assert isinstance(morpholog.submit(grant, actor="bootstrap"), Committed)

    assert isinstance(
        morpholog.submit(
            CaptureTradeRequest(
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
        ),
        Committed,
    )

    # Register the curve: payload stored and hash-anchored in one flow.
    v1 = curve_of("90", "88", "86.25")
    registered = register_curve_version(
        morpholog,
        store,
        actor="carol",
        org=ORG,
        market=MARKET,
        as_of=AS_OF,
        version="crv-v1",
        curve=v1,
    )
    assert isinstance(registered, Committed)
    assert store.load(org=ORG, version="crv-v1").payload_hash() == v1.payload_hash()
    with pytest.raises(StoreError, match="immutable"):
        store.save(org=ORG, version="crv-v1", curve=v1)

    # Mark the trade: 10 * (3.75 + 1.75 + 0) = 55.00.
    marked = value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001")
    assert isinstance(marked, Committed)
    (valuation,) = morpholog.read(TradeValuedClaim)
    assert valuation.curve_version == "crv-v1"
    assert valuation.mtm == Decimal("55.00")

    # Marking again against the same official curve has nothing new to
    # say: a lawful rejection, decided by the ledger, not by this code.
    again = value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001")
    assert isinstance(again, Rejected)

    # Correct the curve (every hour +1.00) and re-mark: 55 + 30 = 85.
    corrected = correct_curve_version(
        morpholog,
        store,
        actor="carol",
        org=ORG,
        market=MARKET,
        as_of=AS_OF,
        prior_version="crv-v1",
        new_version="crv-v2",
        curve=curve_of("91", "89", "87.25"),
    )
    assert isinstance(corrected, Committed)
    remarked = value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001")
    assert isinstance(remarked, Committed)

    # Both marks stand, each pinned to the exact curve version it used.
    marks = {v.curve_version: v.mtm for v in morpholog.read(TradeValuedClaim)}
    assert marks == {"crv-v1": Decimal("55.00"), "crv-v2": Decimal("85.00")}


def test_a_tampered_payload_is_refused_not_computed_from(
    morpholog: GlasshouseClient, store: CurveStore
) -> None:
    # The verify story in miniature: alter one stored price behind the
    # ledger's back, and the marking flow refuses to produce a number.
    with store.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE curve_payload_period SET price = price + 1 "
                "WHERE curve_version = 'crv-v2' AND org = :org "
                "AND period_start = (SELECT min(period_start) FROM curve_payload_period "
                "WHERE curve_version = 'crv-v2' AND org = :org)"
            ),
            {"org": ORG},
        )
    with pytest.raises(MarkingError, match="does not match its admitted hash"):
        value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001")
