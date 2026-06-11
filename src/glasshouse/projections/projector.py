"""The projector: the audit tail in, projection rows out, exactly once.

The transition log arrives through the blessed tail (`inspect audit`
via the generated client - the surface this projector forced upstream
as morpholog#136): committed transitions in `(committed_at,
transition_id)` order, resumed losslessly with `--after`. Lossless is
the binary's guarantee, not ours: `committed_at` is the writer's
transaction START instant while visibility follows commit order, so a
naive cursor over the raw table can skip a slow writer's transition
forever - the tail computes the resume horizon before snapshotting, and
rows it withholds surface on the next call.

Each fetched page's effects and the cursor advance happen in one
app-schema transaction under an advisory lock, so application is
exactly-once by construction and a second `catch_up` applies nothing.
`rebuild` deletes every projection row and replays from zero - the
read-side law as a callable, and the seed of `glasshouse verify`.

The fold itself (`fold_transition`) is a pure function from a
transition's claims to row effects, so the projection logic is testable
without a database. It is total over the needle model on purpose: the
predicates it deliberately ignores are named, and anything outside that
- a retraction of a projected predicate, an unknown direction - raises
`ProjectionError`, because the model changing under the folds should
stop the projector, never quietly corrupt the read side.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from glasshouse.commit import GlasshouseClient, envelopes, models
from glasshouse.projections.tables import (
    blotter_trade,
    position_hour,
    projection_progress,
    trade_valuation,
)

HOUR = dt.timedelta(hours=1)
CURSOR = "needle"

# Predicates whose claims become projection rows. Retracting one of
# these is impossible under the current model (they are append only);
# seeing it happen means the model changed and the folds must too.
PROJECTED = frozenset({"TradeCaptured", "TradeTerms", "TradeValued"})

# Predicates the needle's projections deliberately ignore: authority is
# queried from governed state, and curve officialness/lineage is read
# through the inspection surface, not materialised here (yet).
IGNORED = frozenset(
    {
        "MayCaptureTrade",
        "MayRegisterCurve",
        "MayValueTrade",
        "CurveRegistered",
        "OfficialCurve",
        "CurveSupersedes",
    }
)

SIGNS = {"buy": Decimal(1), "sell": Decimal(-1)}


class ProjectionError(RuntimeError):
    """The log carries something the folds do not honestly cover."""


@dataclass(frozen=True)
class PositionDelta:
    org: str
    book: str
    market: str
    period_start: dt.datetime
    delta_mw: Decimal


@dataclass(frozen=True)
class Fold:
    """One transition's projection effects, as data."""

    blotter: tuple[models.TradeCapturedClaim, ...] = ()
    terms: dict[str, models.TradeTermsClaim] = field(default_factory=dict)
    positions: tuple[PositionDelta, ...] = ()
    valuations: tuple[models.TradeValuedClaim, ...] = ()


def _hours(start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    hours = []
    cursor = start
    while cursor < end:
        hours.append(cursor)
        cursor += HOUR
    return hours


def fold_transition(
    asserted: list[envelopes.ClaimInstance], retracted: list[envelopes.ClaimInstance]
) -> Fold:
    """The pure fold: claims in, row effects out, refusal on anything
    the folds do not cover."""
    for claim in retracted:
        if claim.predicate in PROJECTED:
            raise ProjectionError(
                f"the model retracted append-only {claim.predicate}; the folds must be revisited"
            )

    captured: list[models.TradeCapturedClaim] = []
    terms: dict[str, models.TradeTermsClaim] = {}
    valuations: list[models.TradeValuedClaim] = []
    for claim in asserted:
        match claim.predicate:
            case "TradeCaptured":
                captured.append(models.TradeCapturedClaim(*claim.args))
            case "TradeTerms":
                row = models.TradeTermsClaim(*claim.args)
                terms[row.trade] = row
            case "TradeValued":
                valuations.append(models.TradeValuedClaim(*claim.args))
            case name if name in IGNORED:
                pass
            case name:
                raise ProjectionError(f"no fold covers asserted predicate {name!r}")

    positions: list[PositionDelta] = []
    for trade in captured:
        if trade.trade not in terms:
            raise ProjectionError(f"TradeCaptured {trade.trade!r} arrived without TradeTerms")
        if trade.direction not in SIGNS:
            raise ProjectionError(f"no position sign for direction {trade.direction!r}")
        term = terms[trade.trade]
        sign = SIGNS[trade.direction]
        positions += [
            PositionDelta(trade.org, trade.book, trade.market, hour, sign * term.quantity)
            for hour in _hours(term.delivery_start, term.delivery_end)
        ]
    if set(terms) - {trade.trade for trade in captured}:
        raise ProjectionError("TradeTerms arrived without its TradeCaptured")

    return Fold(tuple(captured), terms, tuple(positions), tuple(valuations))


def _apply(
    connection: sa.Connection, fold: Fold, committed_at: dt.datetime, tid: str, actor: str
) -> None:
    for trade in fold.blotter:
        term = fold.terms[trade.trade]
        connection.execute(
            sa.insert(blotter_trade).values(
                org=trade.org,
                trade=trade.trade,
                book=trade.book,
                counterparty=trade.counterparty,
                market=trade.market,
                direction=trade.direction,
                quantity=term.quantity,
                price=term.price,
                delivery_start=term.delivery_start,
                delivery_end=term.delivery_end,
                captured_at=committed_at,
                transition_id=tid,
                actor=actor,
            )
        )
    if fold.positions:
        statement = pg_insert(position_hour).values(
            [
                {
                    "org": delta.org,
                    "book": delta.book,
                    "market": delta.market,
                    "period_start": delta.period_start,
                    "net_mw": delta.delta_mw,
                    "transition_id": tid,
                }
                for delta in fold.positions
            ]
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["org", "book", "market", "period_start"],
                set_={
                    "net_mw": position_hour.c.net_mw + statement.excluded.net_mw,
                    "transition_id": statement.excluded.transition_id,
                },
            )
        )
    for valuation in fold.valuations:
        connection.execute(
            sa.insert(trade_valuation).values(
                org=valuation.org,
                trade=valuation.trade,
                curve_version=valuation.curve_version,
                book=valuation.book,
                mtm=valuation.mtm,
                valued_at=committed_at,
                transition_id=tid,
                actor=actor,
            )
        )


def catch_up(client: GlasshouseClient, engine: sa.Engine) -> int:
    """Apply every transition after the cursor, one app-schema
    transaction per fetched page. Returns the number of transitions
    applied. Safe to run concurrently: the advisory lock serialises
    projectors, and the page is fetched under the authoritative cursor
    inside the locked transaction."""
    applied = 0
    while True:
        with engine.begin() as connection:
            # One writer per page: the transaction-scoped advisory lock
            # serialises concurrent projectors, and reading the cursor
            # after acquiring it (then fetching against that cursor)
            # makes double-application impossible rather than unlikely.
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtext('glasshouse.projector'))")
            )
            cursor = connection.execute(
                sa.select(projection_progress.c.transition_id).where(
                    projection_progress.c.name == CURSOR
                )
            ).scalar_one_or_none()
            page = client.audit(after=cursor)
            if not page:
                return applied
            for row in page:
                fold = fold_transition(row.asserted_claims, row.retracted_claims)
                _apply(connection, fold, row.committed_at, row.transition_id, row.actor)
            last = page[-1]
            advance = pg_insert(projection_progress).values(
                name=CURSOR, committed_at=last.committed_at, transition_id=last.transition_id
            )
            connection.execute(
                advance.on_conflict_do_update(
                    index_elements=["name"],
                    set_={
                        "committed_at": last.committed_at,
                        "transition_id": last.transition_id,
                    },
                )
            )
            applied += len(page)


def accumulate(
    client: GlasshouseClient, up_to: str | None = None
) -> dict[str, set[tuple[object, ...]]]:
    """Replay the tail through the pure folds into in-memory row sets
    matching the projection tables' column order - the non-destructive
    half of `glasshouse verify`'s projection leg. Reads the blessed
    tail and writes nothing.

    With `up_to` (a transition id), folding stops after that
    transition: the caller is verifying tables that claim to reflect
    the log exactly up to their cursor, and anything beyond it is the
    projector's lag, not divergence. A `up_to` the tail does not
    contain raises `ProjectionError` - a cursor naming an unknown
    transition is corruption, never lag (committed-row visibility is
    monotonic, so a previously applied transition cannot vanish from a
    later snapshot)."""
    blotter: dict[tuple[str, str], tuple[object, ...]] = {}
    positions: dict[tuple[str, str, str, dt.datetime], tuple[Decimal, str]] = {}
    valuations: dict[tuple[str, str, str], tuple[object, ...]] = {}
    cursor: tuple[object, ...] | None = None
    reached_up_to = up_to is None
    for row in client.audit():
        if up_to is not None and reached_up_to:
            break
        fold = fold_transition(row.asserted_claims, row.retracted_claims)
        for trade in fold.blotter:
            term = fold.terms[trade.trade]
            blotter[(trade.org, trade.trade)] = (
                trade.org,
                trade.trade,
                trade.book,
                trade.counterparty,
                trade.market,
                trade.direction,
                term.quantity,
                term.price,
                term.delivery_start,
                term.delivery_end,
                row.committed_at,
                row.transition_id,
                row.actor,
            )
        for delta in fold.positions:
            key = (delta.org, delta.book, delta.market, delta.period_start)
            net = positions[key][0] if key in positions else Decimal(0)
            positions[key] = (net + delta.delta_mw, row.transition_id)
        for valuation in fold.valuations:
            valuations[(valuation.org, valuation.trade, valuation.curve_version)] = (
                valuation.org,
                valuation.trade,
                valuation.curve_version,
                valuation.book,
                valuation.mtm,
                row.committed_at,
                row.transition_id,
                row.actor,
            )
        cursor = (CURSOR, row.committed_at, row.transition_id)
        if row.transition_id == up_to:
            reached_up_to = True
    if not reached_up_to:
        raise ProjectionError(
            f"the projection cursor names transition {up_to!r}, which the audit tail "
            "does not contain - the cursor does not describe this ledger"
        )
    return {
        "blotter_trade": set(blotter.values()),
        "position_hour": {(*key, net, tid) for key, (net, tid) in positions.items()},
        "trade_valuation": set(valuations.values()),
        "projection_progress": {cursor} if cursor else set(),
    }


def rebuild(client: GlasshouseClient, engine: sa.Engine) -> int:
    """The read-side law as a callable: delete every projection row and
    replay the tail from zero. Returns the number of transitions applied."""
    with engine.begin() as connection:
        for table in (blotter_trade, position_hour, trade_valuation, projection_progress):
            connection.execute(sa.delete(table))
    return catch_up(client, engine)
