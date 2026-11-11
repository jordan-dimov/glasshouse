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

A database that cannot answer surfaces as `ReadUnavailableError` from the
query layer, mapped app-wide to a 503 - an honest "not ready", never a
500: the projection tables are a cache of the ledger, and their
unavailability is operational.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from glasshouse.api import asof, queries
from glasshouse.api.deps import AS_OF_ERRORS, UNAVAILABLE, EngineDep, PastDep
from glasshouse.api.schemas import (
    BlotterTrade,
    BookScope,
    OverviewSummary,
    PositionHour,
    Problem,
    TradeTermsVersion,
    TradeValuation,
)

router = APIRouter(tags=["reads"], responses=UNAVAILABLE)

# The three filters most reads share, declared once (a query parameter
# model): the organisation, and a book or market to narrow to.
type ScopeDep = Annotated[BookScope, Query()]


@router.get("/orgs")
def list_orgs(engine: EngineDep) -> list[str]:
    """Organisations currently represented in the projection read model
    (not a registry - an org absent here is still a valid scope with no
    projected activity yet)."""
    return queries.list_orgs(engine)


@router.get("/overview")
def overview(org: str, engine: EngineDep) -> OverviewSummary:
    return queries.overview(engine, org=org)


@router.get("/trades", responses=AS_OF_ERRORS)
def list_trades(
    scope: ScopeDep,
    engine: EngineDep,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
    past: PastDep = None,
) -> list[BlotterTrade]:
    return queries.list_trades(
        engine,
        org=scope.org,
        book=scope.book,
        market=scope.market,
        limit=limit,
        offset=offset,
        past=past,
    )


@router.get(
    "/trades/{trade:path}/terms",
    responses={404: {"model": Problem, "description": "No such trade in the organisation."}},
)
def list_trade_terms(trade: str, org: str, engine: EngineDep) -> list[TradeTermsVersion]:
    """The amendment trail of one trade: every terms version as
    admitted, with its lineage and which one is current. A trade id is
    an opaque subject and may carry slashes (`desk/T-1`), so the path
    converter takes the whole segment run."""
    try:
        return queries.list_trade_terms(engine, org=org, trade=trade)
    except queries.UnknownTradeError as unknown:
        raise HTTPException(status_code=404, detail=str(unknown)) from unknown


@router.get("/positions", responses=AS_OF_ERRORS)
def list_positions(
    scope: ScopeDep,
    engine: EngineDep,
    start: Annotated[
        datetime | None, Query(description="period_start >= start (no offset means UTC)")
    ] = None,
    end: Annotated[
        datetime | None, Query(description="period_start < end (no offset means UTC)")
    ] = None,
    past: PastDep = None,
) -> list[PositionHour]:
    # A window bound with no offset is UTC, here and not in the database
    # session: the live and the as-of path must read it as one instant.
    return queries.list_positions(
        engine,
        org=scope.org,
        book=scope.book,
        market=scope.market,
        start=asof.as_utc(start),
        end=asof.as_utc(end),
        past=past,
    )


@router.get("/valuations", responses=AS_OF_ERRORS)
def list_valuations(
    scope: ScopeDep,
    engine: EngineDep,
    trade: str | None = None,
    latest: Annotated[
        bool,
        Query(
            description="only each trade's newest admitted mark, never history "
            "(the newest mark may still be against a superseded curve)"
        ),
    ] = False,
    past: PastDep = None,
) -> list[TradeValuation]:
    return queries.list_valuations(
        engine,
        org=scope.org,
        trade=trade,
        book=scope.book,
        market=scope.market,
        latest=latest,
        past=past,
    )
