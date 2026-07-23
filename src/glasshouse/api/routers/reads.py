"""The read endpoints: blotter, positions, valuations, orgs, overview.

Thin parameter-parsers over `glasshouse.api.queries`, the one query
layer the JSON API and the server-rendered UI share (UI law 4). Every
read is a `select` over a projection table - the primary read model
(law 4), never raw positional JSONB on governed state. The org is
required on every scoped endpoint: it is the tenancy boundary (law 6),
so a read is always scoped to one organisation, with the optional
narrowings on top of that. Each row carries its source `transition_id`
(and, where stored, the `actor`) straight through, so the evidence trail
is visible in the read rather than stripped at the edge.

A database that cannot answer surfaces as `ReadUnavailable` from the
query layer, mapped app-wide to a 503 - an honest "not ready", never a
500: the projection tables are a cache of the ledger, and their
unavailability is operational.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from glasshouse.api import queries
from glasshouse.api.deps import get_engine
from glasshouse.api.schemas import BlotterTrade, OverviewSummary, PositionHour, TradeValuation

router = APIRouter(tags=["reads"])


@router.get("/orgs")
def list_orgs(engine: sa.Engine = Depends(get_engine)) -> list[str]:
    """Organisations currently represented in the projection read model
    (not a registry - an org absent here is still a valid scope with no
    projected activity yet)."""
    return queries.list_orgs(engine)


@router.get("/overview")
def overview(org: str, engine: sa.Engine = Depends(get_engine)) -> OverviewSummary:
    return queries.overview(engine, org=org)


@router.get("/trades")
def list_trades(
    org: str,
    book: str | None = None,
    market: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int | None = Query(default=None, ge=0),
    engine: sa.Engine = Depends(get_engine),
) -> list[BlotterTrade]:
    return queries.list_trades(
        engine, org=org, book=book, market=market, limit=limit, offset=offset
    )


@router.get("/positions")
def list_positions(
    org: str,
    book: str | None = None,
    market: str | None = None,
    start: datetime | None = Query(default=None, description="period_start >= start (UTC)"),
    end: datetime | None = Query(default=None, description="period_start < end (UTC)"),
    engine: sa.Engine = Depends(get_engine),
) -> list[PositionHour]:
    return queries.list_positions(engine, org=org, book=book, market=market, start=start, end=end)


@router.get("/valuations")
def list_valuations(
    org: str,
    trade: str | None = None,
    latest: bool = Query(
        default=False, description="only the current (newest) mark per trade, never history"
    ),
    engine: sa.Engine = Depends(get_engine),
) -> list[TradeValuation]:
    return queries.list_valuations(engine, org=org, trade=trade, latest=latest)
