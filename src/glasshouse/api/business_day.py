"""What the organisation's trades are worth on its business date.

A trade's latest mark is not necessarily today's: after the business
date rolls, a trade valued only on an earlier day still has that day's
mark as its newest. Which marks are today's is the ledger's to say, in
two derived claims: `ValuationOnBusinessDate`, one row per trade with an
official curve and terms in force on the day (naming them, and whether a
mark against them exists), and `ValuationGap`, one row per trade that
cannot be valued that day and why. Between them every captured trade is
accounted for, so a total here is never "complete" by leaving out a
trade whose curve or terms are missing. The totals themselves are a third
derived claim, `BusinessDayTotal`, so the ledger adds them up, not this
module, and the same figure can be stated as an audit receipt
(`glasshouse.receipts`).

The date and both derived reads are taken as of one instant, so the date
and the rows always describe the same moment, whatever rolls meanwhile.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field

from glasshouse.api import asof
from glasshouse.api.schemas import OverviewSummary, ValuationSummary
from glasshouse.commit import GlasshouseClient, MorphologError, models

# A present instant may be refused as not settled while a write that
# started before it is still in flight; it settles in milliseconds.
_ATTEMPTS = 5
_PAUSE_SECONDS = 0.2


class InconsistentReadError(RuntimeError):
    """The rows did not describe the business date read beside them. The
    as-of read rules this out; the check stands so that a regression in
    it shows as an error, never as a mixed summary."""


@dataclass(frozen=True)
class BusinessDay:
    """The business date (None before the first roll), each trade's
    valuation standing on it, the trades that cannot be valued on it, and
    the ledger's totals of the marks struck for it."""

    date: dt.date | None
    rows: list[models.ValuationOnBusinessDateClaim] = field(default_factory=list)
    gaps: list[models.ValuationGapClaim] = field(default_factory=list)
    sums: list[models.BusinessDayTotalClaim] = field(default_factory=list)

    def current(self) -> set[tuple[str, str, str]]:
        """(trade, curve version, terms version) of every mark struck for
        the business date: the marks that are today's."""
        return {(r.trade, r.curve_version, r.terms_version) for r in self.rows if r.valued}

    def totals(self) -> list[ValuationSummary]:
        """One total per currency of the marks struck for the business
        date, as the ledger adds them up; never a sum across currencies,
        never yesterday's marks."""
        return [
            ValuationSummary(currency=s.currency, trade_count=int(s.valued), total_mtm=s.total)
            for s in sorted(self.sums, key=lambda s: s.currency)
        ]

    def awaiting(self) -> int:
        """Trades whose curve and terms for the day exist, with no mark yet."""
        return sum(1 for row in self.rows if not row.valued)

    def missing(self, reason: str) -> int:
        """Trades that cannot be valued for the day, for one reason."""
        return sum(1 for gap in self.gaps if gap.reason == reason)


def _read_at(client: GlasshouseClient, org: str, at: str) -> BusinessDay:
    where = {"org": org}
    dates = [
        models.BusinessDateClaim.from_named(row.args).date
        for row in client.claims_named("BusinessDate", as_of=at, where=where)
    ]
    if not dates:
        return BusinessDay(date=None)
    (date,) = dates
    rows = [
        models.ValuationOnBusinessDateClaim.from_named(r.args)
        for r in client.derived_named("ValuationOnBusinessDate", as_of=at, where=where)
    ]
    gaps = [
        models.ValuationGapClaim.from_named(r.args)
        for r in client.derived_named("ValuationGap", as_of=at, where=where)
    ]
    sums = [
        models.BusinessDayTotalClaim.from_named(r.args)
        for r in client.derived_named("BusinessDayTotal", as_of=at, where=where)
    ]
    days = [r.business_date for r in rows] + [g.business_date for g in gaps]
    if any(day != date for day in days + [s.business_date for s in sums]):
        raise InconsistentReadError(f"rows for another day beside the business date {date}")
    return BusinessDay(date=date, rows=rows, gaps=gaps, sums=sums)


def read(client: GlasshouseClient, *, org: str) -> BusinessDay:
    """The organisation's business day from the ledger, every read taken
    as of one instant: the date and its rows are one moment's."""
    for attempt in range(_ATTEMPTS):
        at = dt.datetime.now(dt.UTC).isoformat()
        try:
            return _read_at(client, org, at)
        except MorphologError as refused:
            if not asof.unsettled(refused) or attempt == _ATTEMPTS - 1:
                raise
            time.sleep(_PAUSE_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


def onto(summary: OverviewSummary, client: GlasshouseClient) -> OverviewSummary:
    """The overview with its valuations made the business date's: the
    date, the marks struck for it per currency, and every trade without
    one, by reason. Raises if the ledger cannot answer; the caller decides
    what to show then, and never falls back to the latest marks."""
    day = read(client, org=summary.org)
    return summary.model_copy(
        update={
            "business_date": day.date,
            "valuations": day.totals(),
            "awaiting_valuation": day.awaiting(),
            "missing_curve": day.missing("missing_curve"),
            "no_terms_in_force": day.missing("no_terms_in_force"),
        }
    )
