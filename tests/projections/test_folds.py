"""The pure fold: claims in, row effects out, refusal on anything the
folds do not honestly cover. No database anywhere in this module."""

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.commit import envelopes
from glasshouse.projections import ProjectionError, fold_transition

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

SIGN = {"buy": Decimal(1), "sell": Decimal(-1)}


def captured(trade: str = "T-1", direction: str = "buy") -> envelopes.ClaimInstance:
    return envelopes.ClaimInstance(
        "TradeCaptured", ["acme", "spec-de", trade, "stadtwerk-x", "de-power", direction]
    )


def terms(trade: str = "T-1", quantity: str = "10", hours: int = 3) -> envelopes.ClaimInstance:
    return envelopes.ClaimInstance(
        "TradeTerms",
        ["acme", trade, Decimal(quantity), Decimal("86.25"), T0, T0 + dt.timedelta(hours=hours)],
    )


def test_a_capture_becomes_one_blotter_row_and_hourly_deltas() -> None:
    fold = fold_transition([captured(), terms()], [])
    assert [trade.trade for trade in fold.blotter] == ["T-1"]
    assert len(fold.positions) == 3
    assert {delta.period_start for delta in fold.positions} == {
        T0 + dt.timedelta(hours=h) for h in range(3)
    }
    assert all(delta.delta_mw == Decimal("10") for delta in fold.positions)
    assert not fold.valuations


def test_buy_and_sell_net_to_zero() -> None:
    buy = fold_transition([captured("T-1", "buy"), terms("T-1")], [])
    sell = fold_transition([captured("T-2", "sell"), terms("T-2")], [])
    by_hour = [b.delta_mw + s.delta_mw for b, s in zip(buy.positions, sell.positions, strict=True)]
    assert by_hour == [Decimal(0)] * 3


def test_a_valuation_becomes_one_row() -> None:
    fold = fold_transition(
        [
            envelopes.ClaimInstance(
                "TradeValued", ["acme", "spec-de", "T-1", "crv-v1", Decimal("55.00")]
            )
        ],
        [],
    )
    (valuation,) = fold.valuations
    assert (valuation.curve_version, valuation.mtm) == ("crv-v1", Decimal("55.00"))


def test_the_deliberately_ignored_predicates_fold_to_nothing() -> None:
    fold = fold_transition(
        [
            envelopes.ClaimInstance("MayCaptureTrade", ["alice", "acme", "spec-de"]),
            envelopes.ClaimInstance(
                "CurveRegistered",
                ["acme", "de-power", dt.date(2026, 6, 8), "crv-v2", "sha256:bb"],
            ),
            envelopes.ClaimInstance("CurveSupersedes", ["crv-v2", "crv-v1"]),
            envelopes.ClaimInstance(
                "OfficialCurve", ["acme", "de-power", dt.date(2026, 6, 8), "crv-v2"]
            ),
        ],
        # correct_curve retracts the official pointer: a no-op here.
        [
            envelopes.ClaimInstance(
                "OfficialCurve", ["acme", "de-power", dt.date(2026, 6, 8), "crv-v1"]
            )
        ],
    )
    assert fold == fold_transition([], [])


def test_refusals_are_loud() -> None:
    with pytest.raises(ProjectionError, match="append-only TradeValued"):
        fold_transition([], [envelopes.ClaimInstance("TradeValued", [])])
    with pytest.raises(ProjectionError, match="no fold covers"):
        fold_transition([envelopes.ClaimInstance("BrandNewPredicate", [])], [])
    with pytest.raises(ProjectionError, match="without TradeTerms"):
        fold_transition([captured()], [])
    with pytest.raises(ProjectionError, match="without its TradeCaptured"):
        fold_transition([terms()], [])
    with pytest.raises(ProjectionError, match="no position sign"):
        fold_transition([captured(direction="long"), terms()], [])


trade_ids = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-", min_size=1, max_size=8)
trade_quantities = st.decimals(
    min_value=Decimal("0.1"), max_value=Decimal("1000"), allow_nan=False, places=1
)


@st.composite
def trade_books(draw: st.DrawFn) -> list[tuple[str, str, Decimal, int]]:
    """A book of trades with distinct ids: (trade, direction, quantity,
    delivery hours), zero or more."""
    ids = draw(st.lists(trade_ids, max_size=6, unique=True))
    return [
        (
            tid,
            draw(st.sampled_from(["buy", "sell"])),
            draw(trade_quantities),
            draw(st.integers(min_value=1, max_value=48)),
        )
        for tid in ids
    ]


def _claims(specs: list[tuple[str, str, Decimal, int]]) -> list[envelopes.ClaimInstance]:
    asserted: list[envelopes.ClaimInstance] = []
    for trade, direction, quantity, hours in specs:
        asserted.append(
            envelopes.ClaimInstance(
                "TradeCaptured", ["acme", "spec-de", trade, "cp", "de-power", direction]
            )
        )
        asserted.append(
            envelopes.ClaimInstance(
                "TradeTerms",
                ["acme", trade, quantity, Decimal("50"), T0, T0 + dt.timedelta(hours=hours)],
            )
        )
    return asserted


@given(trade_books())
def test_the_fold_conserves_trades_and_signed_hours(
    specs: list[tuple[str, str, Decimal, int]],
) -> None:
    # The read-side law as algebra: one blotter row per capture, one
    # position-hour per delivered hour, and the net MW is exactly the sum
    # of each trade's signed quantity over its hours - whatever the book.
    fold = fold_transition(_claims(specs), [])
    assert len(fold.blotter) == len(specs)
    assert {trade.trade for trade in fold.blotter} == {spec[0] for spec in specs}
    assert len(fold.positions) == sum(hours for *_, hours in specs)
    assert sum(delta.delta_mw for delta in fold.positions) == sum(
        SIGN[direction] * quantity * hours for _, direction, quantity, hours in specs
    )


def test_the_wire_shape_decodes_into_the_fold() -> None:
    # As the audit log carries it: tagged args, decoded by the same
    # codecs the commit zone uses.
    wire_terms = {
        "predicate": "TradeTerms",
        "args": [
            {"type": "subject", "value": "acme"},
            {"type": "subject", "value": "T-1"},
            {"type": "quantity", "value": {"amount": "10", "unit": "MW"}},
            {"type": "decimal", "value": "86.25"},
            {"type": "timestamp", "value": "2026-07-01T00:00:00Z"},
            {"type": "timestamp", "value": "2026-07-01T03:00:00Z"},
        ],
    }
    fold = fold_transition(
        [captured(), envelopes.ClaimInstance.from_json(wire_terms)],
        [],
    )
    assert fold.positions[0].delta_mw == Decimal("10")
    assert fold.positions[0].period_start == T0
