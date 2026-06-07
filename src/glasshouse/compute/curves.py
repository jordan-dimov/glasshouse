"""The curve payload: bulk price content, hash-anchored.

A curve version's identity (org, market, as-of, version) is a governed
claim; the prices themselves never enter the ledger. They live in the
app schema as a payload whose hash is admitted in the claim
(`CurveRegistered.payload_hash`), which is what lets `glasshouse verify`
re-hash stored content against the ledger.

The canonical form is pinned here because the hash is only as good as
the bytes it is over: chronological order, second-precision UTC
timestamps with a `Z` suffix, prices in plain decimal form (no
exponent), compact JSON. Anything that stores or re-hashes a payload
goes through `canonical_bytes`; there is no second serialisation.

v0 is hourly: contiguous, hour-aligned UTC periods. Quarter-hour and
block shapes arrive with the product pack work that needs them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

HOUR = dt.timedelta(hours=1)


class CurveError(ValueError):
    """The payload is not a well-formed hourly curve."""


@dataclass(frozen=True, slots=True)
class HourlyCurve:
    """Contiguous hourly prices: `periods[i]` is the price for the UTC
    hour starting at its timestamp."""

    periods: tuple[tuple[dt.datetime, Decimal], ...]

    def __post_init__(self) -> None:
        if not self.periods:
            raise CurveError("a curve has at least one period")
        previous = None
        for start, _price in self.periods:
            if start.tzinfo is None:
                raise CurveError(f"period start must be timezone-aware: {start!r}")
            if (start.minute, start.second, start.microsecond) != (0, 0, 0):
                raise CurveError(f"period start must be hour-aligned: {start.isoformat()}")
            if previous is not None and start != previous + HOUR:
                raise CurveError(
                    f"periods must be contiguous hourly steps: {previous.isoformat()} "
                    f"is not followed by {start.isoformat()}"
                )
            previous = start

    @property
    def start(self) -> dt.datetime:
        return self.periods[0][0]

    @property
    def end(self) -> dt.datetime:
        """Exclusive end: the instant after the last delivered hour."""
        return self.periods[-1][0] + HOUR

    def price_at(self, hour_start: dt.datetime) -> Decimal:
        """The price for the hour starting at `hour_start`; raises
        `CurveError` outside the curve's span (a valuation never
        silently extrapolates)."""
        if not self.start <= hour_start < self.end:
            raise CurveError(
                f"curve covers [{self.start.isoformat()}, {self.end.isoformat()}); "
                f"no price for {hour_start.isoformat()}"
            )
        index = int((hour_start - self.start) / HOUR)
        return self.periods[index][1]

    def canonical_bytes(self) -> bytes:
        """The pinned canonical serialisation the payload hash is over."""
        document = [
            [start.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), format(price, "f")]
            for start, price in self.periods
        ]
        return json.dumps(document, separators=(",", ":")).encode()

    def payload_hash(self) -> str:
        return f"sha256:{hashlib.sha256(self.canonical_bytes()).hexdigest()}"
