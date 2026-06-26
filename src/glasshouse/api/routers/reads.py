"""The read endpoints: blotter, positions, valuations.

Every read is a `select` over a projection table - the primary read
model (law 4), never raw positional JSONB on governed state. The org is
required on every endpoint: it is the tenancy boundary (law 6), so a read
is always scoped to one organisation, with the optional narrowings on top
of that. Each row carries its source `transition_id` (and, where stored,
the `actor`) straight through, so the evidence trail is visible in the
read rather than stripped at the edge.

A database that cannot answer is a 503, an honest "not ready", never a
500: the projection tables are a cache of the ledger, and their
unavailability is operational.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query

from glasshouse.api.deps import get_engine
from glasshouse.api.schemas import BlotterTrade, PositionHour, TradeValuation
from glasshouse.projections import blotter_trade, position_hour, trade_valuation

router = APIRouter(tags=["reads"])


def _rows(engine: sa.Engine, statement: sa.Select) -> list[dict]:  # type: ignore[type-arg]
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]
    except sa.exc.SQLAlchemyError as exc:
        # The read model is a cache of the ledger; its unavailability is a
        # readiness verdict, not an internal error.
        raise HTTPException(status_code=503, detail="database unavailable") from exc


@router.get("/trades")
def list_trades(
    org: str,
    book: str | None = None,
    engine: sa.Engine = Depends(get_engine),
) -> list[BlotterTrade]:
    statement = sa.select(blotter_trade).where(blotter_trade.c.org == org)
    if book is not None:
        statement = statement.where(blotter_trade.c.book == book)
    statement = statement.order_by(blotter_trade.c.trade)
    return [BlotterTrade.model_validate(row) for row in _rows(engine, statement)]


@router.get("/positions")
def list_positions(
    org: str,
    book: str | None = None,
    market: str | None = None,
    start: datetime | None = Query(default=None, description="period_start >= start (UTC)"),
    end: datetime | None = Query(default=None, description="period_start < end (UTC)"),
    engine: sa.Engine = Depends(get_engine),
) -> list[PositionHour]:
    statement = sa.select(position_hour).where(position_hour.c.org == org)
    if book is not None:
        statement = statement.where(position_hour.c.book == book)
    if market is not None:
        statement = statement.where(position_hour.c.market == market)
    if start is not None:
        statement = statement.where(position_hour.c.period_start >= start)
    if end is not None:
        statement = statement.where(position_hour.c.period_start < end)
    statement = statement.order_by(
        position_hour.c.book, position_hour.c.market, position_hour.c.period_start
    )
    return [PositionHour.model_validate(row) for row in _rows(engine, statement)]


@router.get("/valuations")
def list_valuations(
    org: str,
    trade: str | None = None,
    engine: sa.Engine = Depends(get_engine),
) -> list[TradeValuation]:
    statement = sa.select(trade_valuation).where(trade_valuation.c.org == org)
    if trade is not None:
        statement = statement.where(trade_valuation.c.trade == trade)
    statement = statement.order_by(trade_valuation.c.trade, trade_valuation.c.curve_version)
    return [TradeValuation.model_validate(row) for row in _rows(engine, statement)]
