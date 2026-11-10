"""As-of reads: the book as it stood at a past instant (glasshouse#41).

The projection tables hold the present. The past is answered by the same
pure folds that fill them: the audit tail is replayed in memory up to the
last transition placed in the audit order at or before the instant
(`projections.replay(until=)`, the machinery `glasshouse verify` already
uses), and the rows that replay yields are the rows the tables held then.
One read story, two moments: there is no second set of queries to drift
from the first, and no as-of variant of any table.

The price is a replay of the whole tail per request, through one call of
the binary. That is right for a record of thousands of rows and wrong for
millions; the substrate's as-of reads over kept snapshots are where a
long record goes next, behind this same boundary.

What "as of t" means is the substrate's definition, kept exactly: the
state after every transition ordered at or before t, a prefix of the one
audit order that splits no transaction. The tail withholds a row while
an earlier writer is still in flight, so an instant within the last
moments can gain a row on the next read; an instant in the future is
refused, because its answer is not yet history.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient
from glasshouse.projections.projector import replay
from glasshouse.projections.tables import blotter_trade, position_hour, trade_valuation


class NotYetHistoryError(ValueError):
    """The instant asked for is in the future."""


@dataclass(frozen=True)
class Snapshot:
    """The projection rows as they stood at `at`, keyed as the tables
    are, and how many transitions of history lie behind them."""

    at: dt.datetime
    transitions: int
    trades: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    valuations: list[dict[str, Any]]


def _dicts(table: sa.Table, rows: set[tuple[object, ...]]) -> list[dict[str, Any]]:
    names = [column.name for column in table.c]
    return [dict(zip(names, row, strict=True)) for row in rows]


def parse_instant(raw: str | None) -> dt.datetime | None:
    """An as-of instant from a query string. A value with no offset is
    defined as UTC (the field is labelled UTC everywhere it is shown), so
    nothing naive passes this boundary (law 9). Empty means the present.
    Raises `ValueError` on anything that is not an ISO 8601 instant."""
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def snapshot(
    client: GlasshouseClient, at: dt.datetime, *, now: dt.datetime | None = None
) -> Snapshot:
    """Replay the ledger up to `at` and hand back the rows the screens
    read. `now` is injectable for tests; an instant after it is refused."""
    if at > (now or dt.datetime.now(dt.UTC)):
        raise NotYetHistoryError(f"{at.isoformat()} is in the future; as-of reads answer the past")
    replayed = replay(client, until=at)
    return Snapshot(
        at=at,
        transitions=replayed.transitions,
        trades=_dicts(blotter_trade, replayed.tables["blotter_trade"]),
        positions=_dicts(position_hour, replayed.tables["position_hour"]),
        valuations=_dicts(trade_valuation, replayed.tables["trade_valuation"]),
    )


def trades(
    past: Snapshot,
    *,
    org: str,
    book: str | None,
    market: str | None,
    limit: int | None,
    offset: int | None,
) -> list[dict[str, Any]]:
    rows = sorted(
        (
            row
            for row in past.trades
            if row["org"] == org
            and (book is None or row["book"] == book)
            and (market is None or row["market"] == market)
        ),
        key=lambda row: row["trade"],
    )
    rows = rows[offset or 0 :]
    return rows if limit is None else rows[:limit]


def positions(
    past: Snapshot,
    *,
    org: str,
    book: str | None,
    market: str | None,
    start: dt.datetime | None,
    end: dt.datetime | None,
) -> list[dict[str, Any]]:
    return sorted(
        (
            row
            for row in past.positions
            if row["org"] == org
            and (book is None or row["book"] == book)
            and (market is None or row["market"] == market)
            and (start is None or row["period_start"] >= start)
            and (end is None or row["period_start"] < end)
        ),
        key=lambda row: (row["book"], row["market"], row["period_start"]),
    )


def valuations(
    past: Snapshot,
    *,
    org: str,
    trade: str | None,
    book: str | None,
    market: str | None,
    latest: bool,
) -> list[dict[str, Any]]:
    """The marks admitted by then. `latest` keeps each trade's newest by
    the same order the live query ranks by (`valued_at`, then the
    transition), and the market narrowing goes through the trades of that
    moment, as the live one goes through the blotter."""
    rows = [row for row in past.valuations if row["org"] == org]
    if latest:
        newest: dict[str, dict[str, Any]] = {}
        for row in rows:
            held = newest.get(row["trade"])
            if held is None or (row["valued_at"], row["transition_id"]) > (
                held["valued_at"],
                held["transition_id"],
            ):
                newest[row["trade"]] = row
        rows = list(newest.values())
    in_market = (
        None
        if market is None
        else {row["trade"] for row in past.trades if row["org"] == org and row["market"] == market}
    )
    return sorted(
        (
            row
            for row in rows
            if (trade is None or row["trade"] == trade)
            and (book is None or row["book"] == book)
            and (in_market is None or row["trade"] in in_market)
        ),
        key=lambda row: (row["trade"], row["curve_version"], row["terms_version"]),
    )
