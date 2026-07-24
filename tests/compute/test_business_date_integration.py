"""The business-date boundary (glasshouse#44), pinned: the model
lawfully carries one official curve per (org, market, as-of date), so
two dates can hold official curves at once - and `value_trade`, which
has no business date to select with yet, must refuse that state by
name, never guess or crash obscurely.

Self-provisioned module slate.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import MarkingError, register_curve_version, value_trade
from glasshouse.compute.store import CurveStore
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG, BOOK, MARKET = "date-bound", "book-a", "de-power"


def _day_curve(day: dt.datetime) -> HourlyCurve:
    return HourlyCurve(
        tuple((day + dt.timedelta(hours=h), Decimal(str(80 + h))) for h in range(24))
    )


@pytest.fixture(scope="module")
def stack() -> tuple[GlasshouseClient, CurveStore, sa.Engine]:
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    client.init()
    store = CurveStore(engine)
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=BOOK),
    ):
        assert isinstance(client.submit(grant, actor="bootstrap"), Committed)
    return client, store, engine


def test_two_official_dates_are_a_named_refusal_not_a_crash(
    stack: tuple[GlasshouseClient, CurveStore, sa.Engine],
) -> None:
    client, store, _ = stack
    day_one = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    day_two = dt.datetime(2026, 7, 2, tzinfo=dt.UTC)

    assert isinstance(
        client.submit(
            models.CaptureTradeRequest(
                org=ORG,
                book=BOOK,
                trade="DB-1",
                counterparty="cp",
                market=MARKET,
                direction="buy",
                quantity=Decimal("5"),
                price=Decimal("82"),
                delivery_start=day_one + dt.timedelta(hours=8),
                delivery_end=day_one + dt.timedelta(hours=10),
            ),
            actor="alice",
        ),
        Committed,
    )
    assert isinstance(
        register_curve_version(
            client,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=day_one.date(),
            version="db-crv-jul1",
            curve=_day_curve(day_one),
        ),
        Committed,
    )
    # One official date: valuation works.
    assert isinstance(
        value_trade(client, store, actor="risk-engine", org=ORG, book=BOOK, trade="DB-1"),
        Committed,
    )

    # A second date's curve is LAWFUL (one official per as-of date) -
    # exactly what the Imports workbench makes easy.
    assert isinstance(
        register_curve_version(
            client,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=day_two.date(),
            version="db-crv-jul2",
            curve=_day_curve(day_two),
        ),
        Committed,
    )
    assert isinstance(
        client.submit(
            models.CaptureTradeRequest(
                org=ORG,
                book=BOOK,
                trade="DB-2",
                counterparty="cp",
                market=MARKET,
                direction="buy",
                quantity=Decimal("5"),
                price=Decimal("82"),
                delivery_start=day_one + dt.timedelta(hours=8),
                delivery_end=day_one + dt.timedelta(hours=10),
            ),
            actor="alice",
        ),
        Committed,
    )
    # Two official dates: the compute path has no business date to
    # choose with, and says so by name.
    with pytest.raises(MarkingError, match=r"glasshouse#44") as refusal:
        value_trade(client, store, actor="risk-engine", org=ORG, book=BOOK, trade="DB-2")
    assert "2026-07-01" in str(refusal.value)
    assert "2026-07-02" in str(refusal.value)
