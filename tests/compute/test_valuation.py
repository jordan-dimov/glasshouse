"""MTM properties: exact Decimal arithmetic, no silent partials, and
the algebra a valuation must satisfy whatever the numbers are."""

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.valuation import ValuationError, mark_to_market

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

prices = st.decimals(
    min_value=Decimal("-500"), max_value=Decimal("3000"), allow_nan=False, places=2
)
quantities = st.decimals(
    min_value=Decimal("0.1"), max_value=Decimal("500"), allow_nan=False, places=1
)


def curve_of(price_list: list[Decimal], start: dt.datetime = T0) -> HourlyCurve:
    return HourlyCurve(tuple((start + dt.timedelta(hours=i), p) for i, p in enumerate(price_list)))


def test_a_worked_example_by_hand() -> None:
    # 10 MW bought at 86.25 over three hours priced 90 / 88 / 86.25:
    # 10 * (3.75 + 1.75 + 0) = 55.00
    value = mark_to_market(
        direction="buy",
        quantity_mw=Decimal("10"),
        price=Decimal("86.25"),
        delivery_start=T0,
        delivery_end=T0 + dt.timedelta(hours=3),
        curve=curve_of([Decimal("90"), Decimal("88"), Decimal("86.25")]),
    )
    assert value == Decimal("55.00")


def test_partial_curve_coverage_is_an_error_not_a_number() -> None:
    with pytest.raises(ValuationError, match="does not cover"):
        mark_to_market(
            direction="buy",
            quantity_mw=Decimal("10"),
            price=Decimal("86.25"),
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=4),
            curve=curve_of([Decimal("90"), Decimal("88"), Decimal("86.25")]),  # 3h only
        )


def test_unknown_direction_refused() -> None:
    with pytest.raises(ValuationError, match="unknown direction"):
        mark_to_market(
            direction="straddle",
            quantity_mw=Decimal("1"),
            price=Decimal("1"),
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=1),
            curve=curve_of([Decimal("1")]),
        )


@given(strike=prices, quantity=quantities, hours=st.integers(min_value=1, max_value=72))
def test_curve_equal_to_strike_marks_to_zero(
    strike: Decimal, quantity: Decimal, hours: int
) -> None:
    curve = curve_of([strike] * hours)
    value = mark_to_market(
        direction="buy",
        quantity_mw=quantity,
        price=strike,
        delivery_start=T0,
        delivery_end=T0 + dt.timedelta(hours=hours),
        curve=curve,
    )
    assert value == 0


@given(
    strike=prices,
    curve_prices=st.lists(prices, min_size=1, max_size=72),
    quantity=quantities,
)
def test_buy_is_exactly_minus_sell(
    strike: Decimal, curve_prices: list[Decimal], quantity: Decimal
) -> None:
    curve = curve_of(curve_prices)

    def value(direction: str) -> Decimal:
        return mark_to_market(
            direction=direction,
            quantity_mw=quantity,
            price=strike,
            delivery_start=T0,
            delivery_end=T0 + dt.timedelta(hours=len(curve_prices)),
            curve=curve,
        )

    assert value("buy") == -value("sell")


@given(
    strike=prices,
    curve_prices=st.lists(prices, min_size=2, max_size=72),
    quantity=quantities,
    data=st.data(),
)
def test_valuation_is_additive_over_a_delivery_split(
    strike: Decimal, curve_prices: list[Decimal], quantity: Decimal, data: st.DataObject
) -> None:
    # Valuing [start, end) equals valuing [start, m) plus [m, end) for
    # any hour-aligned split point m: a portfolio number never depends
    # on how the books slice the period.
    curve = curve_of(curve_prices)
    split = data.draw(st.integers(min_value=1, max_value=len(curve_prices) - 1))
    end = T0 + dt.timedelta(hours=len(curve_prices))
    middle = T0 + dt.timedelta(hours=split)

    def value(a: dt.datetime, b: dt.datetime) -> Decimal:
        return mark_to_market(
            direction="buy",
            quantity_mw=quantity,
            price=strike,
            delivery_start=a,
            delivery_end=b,
            curve=curve,
        )

    assert value(T0, end) == value(T0, middle) + value(middle, end)
