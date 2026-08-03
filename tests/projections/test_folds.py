"""The pure fold: claims in, row effects out, refusal on anything the
folds do not honestly cover. No database anywhere in this module."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.commit import envelopes
from glasshouse.projections import ProjectionError, fold_transition

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

SIGN = {"buy": Decimal(1), "sell": Decimal(-1)}


def _ts(moment: dt.datetime) -> str:
    """As the named tail spells an instant: RFC 3339, zone-less UTC."""
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# The named tail carries BARE wire values - decimals and instants as
# strings, decoded by the generated `from_named`. These fixtures spell
# them the way the binary does, so a fold that only works on
# already-typed values cannot pass here.
def captured(trade: str = "T-1", direction: str = "buy") -> envelopes.NamedClaim:
    return envelopes.NamedClaim(
        "TradeCaptured",
        {
            "org": "acme",
            "book": "spec-de",
            "trade": trade,
            "counterparty": "stadtwerk-x",
            "market": "de-power",
            "direction": direction,
        },
    )


def terms(trade: str = "T-1", quantity: str = "10", hours: int = 3) -> envelopes.NamedClaim:
    return envelopes.NamedClaim(
        "TradeTerms",
        {
            "org": "acme",
            "trade": trade,
            "quantity": quantity,
            "price": "86.25",
            "delivery_start": _ts(T0),
            "delivery_end": _ts(T0 + dt.timedelta(hours=hours)),
        },
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
            envelopes.NamedClaim(
                "TradeValued",
                {
                    "org": "acme",
                    "book": "spec-de",
                    "trade": "T-1",
                    "curve_version": "crv-v1",
                    "mtm": "55.00",
                },
            )
        ],
        [],
    )
    (valuation,) = fold.valuations
    assert (valuation.curve_version, valuation.mtm) == ("crv-v1", Decimal("55.00"))


def test_the_deliberately_ignored_predicates_fold_to_nothing() -> None:
    fold = fold_transition(
        [
            envelopes.NamedClaim(
                "MayCaptureTrade", {"actor": "alice", "org": "acme", "book": "spec-de"}
            ),
            envelopes.NamedClaim(
                "CurveRegistered",
                {
                    "org": "acme",
                    "market": "de-power",
                    "as_of": "2026-06-08",
                    "version": "crv-v2",
                    "payload_hash": "sha256:bb",
                },
            ),
            envelopes.NamedClaim(
                "CurveSupersedes", {"new_version": "crv-v2", "prior_version": "crv-v1"}
            ),
            envelopes.NamedClaim(
                "OfficialCurve",
                {
                    "org": "acme",
                    "market": "de-power",
                    "as_of": "2026-06-08",
                    "version": "crv-v2",
                },
            ),
        ],
        # correct_curve retracts the official pointer: a no-op here.
        [
            envelopes.NamedClaim(
                "OfficialCurve",
                {
                    "org": "acme",
                    "market": "de-power",
                    "as_of": "2026-06-08",
                    "version": "crv-v1",
                },
            )
        ],
    )
    assert fold == fold_transition([], [])


def test_refusals_are_loud() -> None:
    with pytest.raises(ProjectionError, match="append-only TradeValued"):
        fold_transition([], [envelopes.NamedClaim("TradeValued", {})])
    with pytest.raises(ProjectionError, match="no fold covers"):
        fold_transition([envelopes.NamedClaim("BrandNewPredicate", {})], [])
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


def _claims(specs: list[tuple[str, str, Decimal, int]]) -> list[envelopes.NamedClaim]:
    asserted: list[envelopes.NamedClaim] = []
    for trade, direction, quantity, hours in specs:
        asserted.append(
            envelopes.NamedClaim(
                "TradeCaptured",
                {
                    "org": "acme",
                    "book": "spec-de",
                    "trade": trade,
                    "counterparty": "cp",
                    "market": "de-power",
                    "direction": direction,
                },
            )
        )
        asserted.append(
            envelopes.NamedClaim(
                "TradeTerms",
                {
                    "org": "acme",
                    "trade": trade,
                    "quantity": str(quantity),
                    "price": "50",
                    "delivery_start": _ts(T0),
                    "delivery_end": _ts(T0 + dt.timedelta(hours=hours)),
                },
            )
        )
    return asserted


@given(trade_books())
def test_the_fold_conserves_trades_and_signed_hours(
    specs: list[tuple[str, str, Decimal, int]],
) -> None:
    # The read-side law as algebra: one blotter row per capture, one
    # position-hour per delivered hour, and the net MW correct FOR EACH
    # hour - not merely in total, which a fold that filed the right MW
    # under the wrong hour would also satisfy.
    fold = fold_transition(_claims(specs), [])
    assert len(fold.blotter) == len(specs)
    assert {trade.trade for trade in fold.blotter} == {spec[0] for spec in specs}
    assert len(fold.positions) == sum(hours for *_, hours in specs)

    actual: dict[dt.datetime, Decimal] = defaultdict(lambda: Decimal(0))
    for delta in fold.positions:
        actual[delta.period_start] += delta.delta_mw
    expected: dict[dt.datetime, Decimal] = defaultdict(lambda: Decimal(0))
    for _trade, direction, quantity, hours in specs:
        for hour in range(hours):
            expected[T0 + dt.timedelta(hours=hour)] += SIGN[direction] * quantity
    assert actual == expected


def test_the_wire_shape_decodes_into_the_fold() -> None:
    # As the NAMED audit tail carries it: args keyed by declared field,
    # values bare (the unit rides on the declaration, not the value), and
    # from_json decoding nothing - the typing is the generated model's
    # job. Decoded here through the real envelope rather than a
    # hand-built NamedClaim, so the fold is exercised against the shape
    # the binary actually emits.
    wire_terms = {
        "predicate": "TradeTerms",
        "args": {
            "org": "acme",
            "trade": "T-1",
            "quantity": "10",
            "price": "86.25",
            "delivery_start": "2026-07-01T00:00:00Z",
            "delivery_end": "2026-07-01T03:00:00Z",
        },
    }
    fold = fold_transition(
        [captured(), envelopes.NamedClaim.from_json(wire_terms)],
        [],
    )
    assert fold.positions[0].delta_mw == Decimal("10")
    assert fold.positions[0].period_start == T0
