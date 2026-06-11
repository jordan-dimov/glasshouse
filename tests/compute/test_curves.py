"""The curve payload: construction rules and the pinned canonical hash."""

import datetime as dt
from decimal import Decimal

import pytest

from glasshouse.compute.curves import CurveError, HourlyCurve

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def hourly(*prices: str, start: dt.datetime = T0) -> HourlyCurve:
    return HourlyCurve(
        tuple((start + dt.timedelta(hours=i), Decimal(p)) for i, p in enumerate(prices))
    )


def test_canonical_bytes_are_pinned() -> None:
    curve = hourly("90", "88.5")
    assert curve.canonical_bytes() == (
        b'[["2026-07-01T00:00:00Z","90"],["2026-07-01T01:00:00Z","88.5"]]'
    )


def test_payload_hash_is_deterministic_and_content_sensitive() -> None:
    assert hourly("90", "88.5").payload_hash() == hourly("90", "88.5").payload_hash()
    assert hourly("90", "88.5").payload_hash() != hourly("90", "88.6").payload_hash()
    assert hourly("90", "88.5").payload_hash().startswith("sha256:")


def test_non_utc_zones_normalise_into_the_canonical_form() -> None:
    # The same instants expressed in a non-UTC zone hash identically:
    # the canonical form is UTC, whatever arrives.
    berlin = dt.timezone(dt.timedelta(hours=2))
    shifted = hourly("90", "88.5", start=dt.datetime(2026, 7, 1, 2, tzinfo=berlin))
    assert shifted.payload_hash() == hourly("90", "88.5").payload_hash()


def test_price_at_covers_the_span_and_refuses_outside_it() -> None:
    curve = hourly("90", "88.5", "86")
    assert curve.price_at(T0) == Decimal("90")
    assert curve.price_at(T0 + dt.timedelta(hours=2)) == Decimal("86")
    with pytest.raises(CurveError, match="no price for"):
        curve.price_at(T0 + dt.timedelta(hours=3))
    with pytest.raises(CurveError, match="no price for"):
        curve.price_at(T0 - dt.timedelta(hours=1))


def test_construction_refuses_dishonest_shapes() -> None:
    with pytest.raises(CurveError, match="at least one"):
        HourlyCurve(())
    with pytest.raises(CurveError, match="timezone-aware"):
        HourlyCurve(((dt.datetime(2026, 7, 1), Decimal("90")),))  # noqa: DTZ001 - under test
    with pytest.raises(CurveError, match="hour-aligned"):
        HourlyCurve(((dt.datetime(2026, 7, 1, 0, 30, tzinfo=dt.UTC), Decimal("90")),))
    with pytest.raises(CurveError, match="contiguous"):
        HourlyCurve(
            (
                (T0, Decimal("90")),
                (T0 + dt.timedelta(hours=2), Decimal("88")),  # gap
            )
        )
