"""What the organisation's trades are worth on its business date.

A trade's latest mark is not necessarily today's: after the business
date rolls, a trade valued only on an earlier day still has that day's
mark as its newest. Which marks are today's is the ledger's to say, in
its derived `ValuationOnBusinessDate`: one row per trade with an official
curve on the business date, naming that curve, the terms in force, and
whether a mark against those two exists. The totals here add up only
those marks, so yesterday's number is never presented as today's.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from glasshouse.api.schemas import OverviewSummary, ValuationSummary
from glasshouse.commit import GlasshouseClient, models
from glasshouse.compute.marking import business_date_of


@dataclass(frozen=True)
class BusinessDay:
    """The business date (None before the first roll) and each trade's
    valuation standing on it."""

    date: dt.date | None
    rows: list[models.ValuationOnBusinessDateClaim] = field(default_factory=list)

    def current(self) -> set[tuple[str, str, str]]:
        """(trade, curve version, terms version) of every mark struck for
        the business date: the marks that are today's."""
        return {(r.trade, r.curve_version, r.terms_version) for r in self.rows if r.valued}

    def totals(self) -> list[ValuationSummary]:
        """One total per currency of the marks struck for the business
        date; never a sum across currencies, never yesterday's marks."""
        count: dict[str, int] = defaultdict(int)
        total: dict[str, Decimal] = defaultdict(Decimal)
        for row in self.rows:
            if row.valued:
                count[row.currency] += 1
                total[row.currency] += row.mtm
        return [
            ValuationSummary(currency=c, trade_count=count[c], valued_at=None, total_mtm=total[c])
            for c in sorted(count)
        ]

    def unvalued(self) -> int:
        """Trades with an official curve on the business date but no mark
        for it yet."""
        return sum(1 for row in self.rows if not row.valued)


def read(client: GlasshouseClient, *, org: str) -> BusinessDay:
    """The organisation's business day, from the ledger: two keyed reads."""
    date = business_date_of(client, org=org)
    if date is None:
        return BusinessDay(date=None)
    rows = client.derived_named("ValuationOnBusinessDate", where={"org": org})
    return BusinessDay(
        date=date, rows=[models.ValuationOnBusinessDateClaim.from_named(r.args) for r in rows]
    )


def onto(summary: OverviewSummary, client: GlasshouseClient) -> OverviewSummary:
    """The overview with its valuations made the business date's: the
    date, the marks struck for it per currency, and how many trades still
    want one. Raises if the ledger cannot answer; the caller decides what
    to show then, and never falls back to the latest marks."""
    day = read(client, org=summary.org)
    return summary.model_copy(
        update={
            "business_date": day.date,
            "valuations": day.totals(),
            "not_valued_for_business_date": day.unvalued(),
        }
    )
