"""Mark-to-market for fixed-price physical power, pure.

The function here computes; admissibility is the ledger's job. The
result this produces is proposed back through `admit_valuation`, where
the rule model refuses it unless the curve version used is the one
officially in force - the compute zone never gets to decide that.

The arithmetic is deliberately boring: for each delivered UTC hour,
quantity x (curve price - strike), signed by direction, summed exactly
in Decimal. Delivery periods are UTC instants, so DST days need no
special case at this layer. A curve that does not cover every delivered
hour is an error, never a partial number: a figure either has full
provenance or it does not exist.

No rounding happens here. Display rounding is a presentation concern;
settlement rounding is a settlement-workflow concern (v1); the governed
figure keeps its exact value.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from glasshouse.compute.curves import HOUR, CurveError, HourlyCurve


class ValuationError(ValueError):
    """The inputs do not admit an honest valuation."""


# Direction is an opaque subject in the ledger; the compute zone is
# where it acquires arithmetic meaning.
DIRECTION_SIGN = {"buy": Decimal(1), "sell": Decimal(-1)}


def mark_to_market(
    *,
    direction: str,
    quantity_mw: Decimal,
    price: Decimal,
    delivery_start: dt.datetime,
    delivery_end: dt.datetime,
    curve: HourlyCurve,
) -> Decimal:
    """The MTM of one fixed-price trade against one hourly curve, in the
    curve's currency: sign x quantity x sum of (curve - strike) over
    every delivered hour."""
    if direction not in DIRECTION_SIGN:
        raise ValuationError(f"unknown direction {direction!r}; expected one of buy, sell")
    if delivery_start.tzinfo is None or delivery_end.tzinfo is None:
        raise ValuationError("delivery instants must be timezone-aware")
    if (delivery_start.minute, delivery_start.second, delivery_start.microsecond) != (0, 0, 0):
        raise ValuationError(f"delivery start is not hour-aligned: {delivery_start.isoformat()}")
    if (delivery_end.minute, delivery_end.second, delivery_end.microsecond) != (0, 0, 0):
        raise ValuationError(f"delivery end is not hour-aligned: {delivery_end.isoformat()}")
    if delivery_end <= delivery_start:
        raise ValuationError("delivery end must follow delivery start")

    total = Decimal(0)
    hour = delivery_start
    while hour < delivery_end:
        try:
            curve_price = curve.price_at(hour)
        except CurveError as exc:
            raise ValuationError(f"curve does not cover the delivery period: {exc}") from exc
        total += curve_price - price
        hour += HOUR
    return DIRECTION_SIGN[direction] * quantity_mw * total
