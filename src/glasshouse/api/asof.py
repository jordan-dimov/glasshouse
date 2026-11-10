"""As-of reads: the book as it stood at a past instant (glasshouse#41).

The projection tables hold the present. The past is answered by the same
pure folds that fill them: the audit tail is replayed in memory up to the
last transition placed in the audit order at or before the instant
(`projections.replay(until=)`, the machinery `glasshouse verify` already
uses), and the rows that replay yields are the rows the tables held then.
One read story, two moments: no as-of variant of any table and no second
computation of a position. What is written twice is the narrowing and
ordering of rows (SQL for the tables, the functions below for a
snapshot); a live test holds the two equal on a caught-up ledger.

The price is a replay of the whole tail per request, through one call of
the binary. That is right for a record of thousands of rows and wrong for
millions; the substrate's as-of reads over kept snapshots are where a
long record goes next, behind this same boundary.

What "as of t" means is the substrate's definition, kept exactly: the
state after every transition ordered at or before t, a prefix of the one
audit order that splits no transaction. Whether t can be answered yet is
the substrate's decision too. While a writer that may be ordered at or
before t is still open, the tail withholds its rows and the same question
would answer differently once it commits; the substrate refuses such an
instant, and so does this module (`NotSettledError`), by asking it first
through its own as-of read. An answer given is therefore final. A future
instant is refused before any of that, because it is not yet history.

The replay runs today's folds over the history. It rebuilds the book as
today's code reads that history, which is what the screens showed then
only while the folds have not changed their meaning since.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient, MorphologError
from glasshouse.projections.projector import replay
from glasshouse.projections.tables import blotter_trade, position_hour, trade_valuation


class NotYetHistoryError(ValueError):
    """The instant asked for is in the future."""


class NotSettledError(Exception):
    """The instant has passed but its history can still change: a writer
    that may be ordered at or before it has not finished. Not the
    caller's mistake, and not a failure: ask again shortly."""


# The smallest predicate of the model (one claim per ledger): the read
# that asks the substrate whether an instant is settled wants its verdict,
# not its rows.
_SETTLEMENT_PROBE = "MayEstablishOrganisations"

# How the binary words its refusal of an unsettled instant. It has no
# code of its own yet (asked upstream, contract section 34), so this
# phrase is the one thing matched; a rewording turns the refusal into an
# operational error (a 503), never into an answer, and the live test
# `test_an_instant_a_writer_may_still_precede_is_refused` goes red.
_NOT_SETTLED = "is not settled"


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


def as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """An instant in UTC. A value with no offset is defined as UTC (every
    time field is labelled UTC where it is shown), so nothing naive passes
    the HTTP boundary (law 9) and no filter compares a naive time with a
    stored instant."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def parse_instant(raw: str | None) -> dt.datetime | None:
    """An instant from a query string, in UTC (`as_utc`). Empty means
    none given. Raises `ValueError` on anything that is not an ISO 8601
    instant."""
    return as_utc(dt.datetime.fromisoformat(raw)) if raw else None


def canonical(at: dt.datetime) -> str:
    """The one spelling of an instant in links, forms and banners: UTC
    wall time with no offset, which `parse_instant` reads back as the
    same instant, however the caller first wrote it."""
    return at.astimezone(dt.UTC).replace(tzinfo=None).isoformat()


def settle(client: GlasshouseClient, at: dt.datetime) -> None:
    """Ask the substrate whether the state as of `at` is final, by its
    own as-of read. Returns when it is; raises `NotSettledError` when a
    writer may still be placed at or before it. Any other failure of the
    binary is operational and passes through."""
    try:
        client.claims_named(_SETTLEMENT_PROBE, as_of=at.isoformat())
    except MorphologError as refused:
        if _NOT_SETTLED in str(refused):
            raise NotSettledError(
                f"the book as of {canonical(at)} UTC is not final yet: a write placed "
                "before that moment is still in progress"
            ) from refused
        raise


def snapshot(
    client: GlasshouseClient, at: dt.datetime, *, now: dt.datetime | None = None
) -> Snapshot:
    """Replay the ledger up to `at` and hand back the rows the screens
    read. `now` is injectable for tests; an instant after it is refused,
    and so is one the substrate does not yet call settled. The order
    matters: settlement first, then the tail, so the prefix replayed is
    one that can no longer change."""
    if at > (now or dt.datetime.now(dt.UTC)):
        raise NotYetHistoryError(f"{at.isoformat()} is in the future; as-of reads answer the past")
    settle(client, at)
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
