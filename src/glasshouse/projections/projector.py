"""The projector: morpholog.audit in, projection rows out, exactly once.

The transition log is tailed in its causal order, `(committed_at,
transition_id)`. Each transition's effects and the cursor advance happen
in one app-schema transaction, so application is exactly-once by
construction and a second `catch_up` applies nothing. `rebuild` deletes
every projection row and replays from zero - the read-side law as a
callable, and the seed of `glasshouse verify`.

The fold itself (`fold_transition`) is a pure function from a
transition's claims to row effects, so the projection logic is testable
without a database. It is total over the needle model on purpose: the
predicates it deliberately ignores are named, and anything outside that
- a retraction of a projected predicate, an unknown direction - raises
`ProjectionError`, because the model changing under the folds should
stop the projector, never quietly corrupt the read side.

Note: the audit table's shape is not yet a pinned upstream surface; this
projector is the worked example forcing that contract (the ask is
recorded in docs/morpholog-integration-contract.md section 12).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from glasshouse.commit import envelopes, models
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


def _apply(connection: sa.Connection, fold: Fold, committed_at: dt.datetime, tid: str) -> None:
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
            )
        )


_NEXT_TRANSITION = sa.text(
    """
    SELECT transition_id, asserted_claims, retracted_claims, committed_at
    FROM morpholog.audit
    WHERE CAST(:c AS timestamptz) IS NULL
       OR (committed_at, transition_id) > (CAST(:c AS timestamptz), CAST(:t AS uuid))
    ORDER BY committed_at, transition_id
    LIMIT 1
    """
)


def catch_up(engine: sa.Engine) -> int:
    """Apply every transition after the cursor, one app-schema
    transaction per transition. Returns the number applied."""
    applied = 0
    while True:
        with engine.begin() as connection:
            cursor = connection.execute(
                sa.select(
                    projection_progress.c.committed_at, projection_progress.c.transition_id
                ).where(projection_progress.c.name == CURSOR)
            ).one_or_none()
            row = connection.execute(
                _NEXT_TRANSITION,
                {"c": cursor[0] if cursor else None, "t": cursor[1] if cursor else None},
            ).one_or_none()
            if row is None:
                return applied
            fold = fold_transition(
                [envelopes.ClaimInstance.from_json(claim) for claim in row.asserted_claims],
                [envelopes.ClaimInstance.from_json(claim) for claim in row.retracted_claims],
            )
            tid = str(row.transition_id)
            _apply(connection, fold, row.committed_at, tid)
            advance = pg_insert(projection_progress).values(
                name=CURSOR, committed_at=row.committed_at, transition_id=tid
            )
            connection.execute(
                advance.on_conflict_do_update(
                    index_elements=["name"],
                    set_={"committed_at": row.committed_at, "transition_id": tid},
                )
            )
            applied += 1


def rebuild(engine: sa.Engine) -> int:
    """The read-side law as a callable: delete every projection row and
    replay the log from zero. Returns the number of transitions applied."""
    with engine.begin() as connection:
        for table in (blotter_trade, position_hour, trade_valuation, projection_progress):
            connection.execute(sa.delete(table))
    return catch_up(engine)
