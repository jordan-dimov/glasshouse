"""Law 9, the one doctrine repeats most: delivery periods are UTC
instants, and DST days (23 or 25 hours on the continent, the same at our
hourly granularity in GB) fall out of timezone arithmetic and are never
special-cased.

Proven here at every layer that touches a delivery period, so a
regression at any one of them is loud: the curve payload, the
mark-to-market sum, and the position-hour fold. The dates are real: 2026
springs forward on 29 March and falls back on 25 October, both on the
continent (Europe/Berlin) and in GB (Europe/London).

The mechanism under test is that nothing here knows it is a DST day. The
window is a local calendar day expressed as UTC instants; the number of
delivered hours is whatever the offset arithmetic yields, and every layer
just iterates UTC hours.
"""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from glasshouse.commit import envelopes
from glasshouse.compute.curves import HOUR, HourlyCurve
from glasshouse.compute.valuation import mark_to_market
from glasshouse.projections import fold_transition

BERLIN = ZoneInfo("Europe/Berlin")
LONDON = ZoneInfo("Europe/London")

# (zone, local calendar day, the hours that day actually has)
DST_DAYS = [
    pytest.param(BERLIN, dt.date(2026, 3, 29), 23, id="berlin-spring-forward"),
    pytest.param(BERLIN, dt.date(2026, 10, 25), 25, id="berlin-fall-back"),
    pytest.param(BERLIN, dt.date(2026, 6, 1), 24, id="berlin-ordinary-day"),
    pytest.param(LONDON, dt.date(2026, 3, 29), 23, id="london-spring-forward"),
    pytest.param(LONDON, dt.date(2026, 10, 25), 25, id="london-fall-back"),
]


def utc_window(zone: ZoneInfo, day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """A local calendar day [00:00, next 00:00) as the two UTC instants
    that bound it. Whole-hour offsets keep both instants hour-aligned."""
    start = dt.datetime.combine(day, dt.time(), tzinfo=zone).astimezone(dt.UTC)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=zone).astimezone(dt.UTC)
    return start, end


@pytest.mark.parametrize(("zone", "day", "hours"), DST_DAYS)
def test_a_local_day_is_the_right_number_of_utc_hours(
    zone: ZoneInfo, day: dt.date, hours: int
) -> None:
    start, end = utc_window(zone, day)
    assert (end - start) / HOUR == hours


@pytest.mark.parametrize(("zone", "day", "hours"), DST_DAYS)
def test_a_curve_covering_the_day_has_one_period_per_real_hour(
    zone: ZoneInfo, day: dt.date, hours: int
) -> None:
    start, _end = utc_window(zone, day)
    curve = HourlyCurve(tuple((start + i * HOUR, Decimal("90")) for i in range(hours)))
    assert len(curve.periods) == hours
    # Round-trips through the canonical UTC form across the boundary.
    assert HourlyCurve(curve.periods).payload_hash() == curve.payload_hash()


@pytest.mark.parametrize(("zone", "day", "hours"), DST_DAYS)
def test_mtm_sums_over_exactly_the_delivered_hours(
    zone: ZoneInfo, day: dt.date, hours: int
) -> None:
    start, end = utc_window(zone, day)
    curve = HourlyCurve(tuple((start + i * HOUR, Decimal("100")) for i in range(hours)))
    value = mark_to_market(
        direction="buy",
        quantity_mw=Decimal("10"),
        price=Decimal("90"),
        delivery_start=start,
        delivery_end=end,
        curve=curve,
    )
    # 10 MW * (100 - 90) per delivered hour: the only thing that varies is
    # the hour count, which is the DST day's own length.
    assert value == Decimal("10") * Decimal("10") * hours


@pytest.mark.parametrize(("zone", "day", "hours"), DST_DAYS)
def test_the_fold_makes_one_position_hour_per_real_hour(
    zone: ZoneInfo, day: dt.date, hours: int
) -> None:
    start, end = utc_window(zone, day)
    captured = envelopes.ClaimInstance(
        "TradeCaptured", ["acme", "spec-de", "T-1", "stadtwerk-x", "de-power", "buy"]
    )
    terms = envelopes.ClaimInstance(
        "TradeTerms", ["acme", "T-1", Decimal("10"), Decimal("90"), start, end]
    )
    fold = fold_transition([captured, terms], [])
    assert len(fold.positions) == hours
    assert {delta.period_start for delta in fold.positions} == {
        start + i * HOUR for i in range(hours)
    }
