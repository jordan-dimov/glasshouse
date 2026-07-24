"""The Control Room routes: three read-only screens over the shared
query layer.

Every handler is a thin composition: parse parameters, call the same
`glasshouse.api.queries` functions the JSON API serves (UI law 4), hand
the typed rows to a template. The org is an explicit query parameter on
every screen, mirroring the JSON API - no session, no cookie, no
auto-selection; a screen asked for without one goes to the picker. The
blotter's filter and pager are the one HTMX use (they swap the results
fragment in place); everything degrades to ordinary GET navigation with
JavaScript off.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from glasshouse.api import curves as curve_queries
from glasshouse.api import health, queries
from glasshouse.api.curves import UnknownCurveVersionError
from glasshouse.api.deps import get_client, get_engine, get_store
from glasshouse.api.schemas import CurveVersion
from glasshouse.commit import GlasshouseClient
from glasshouse.compute.store import CurveStore
from glasshouse.config import get_settings
from glasshouse.web.templating import templates

router = APIRouter(include_in_schema=False)

PAGE_SIZE = 50


def unavailable_page(request: Request) -> Response:
    """The HTML face of `ReadUnavailableError`: the app-level handler
    renders this for `/ui` paths. Database-free by construction."""
    return templates.TemplateResponse(request, "error.html", {}, status_code=503)


def _utc_instant(raw: str | None) -> dt.datetime | None:
    """A `datetime-local` value carries no offset; the filter fields are
    labelled UTC, so the instant is *defined* as UTC here and nothing
    naive passes this boundary (law 9). An aware value (a hand-crafted
    query string) is converted, not refused."""
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _chrome(engine: sa.Engine, org: str, active: str) -> dict[str, Any]:
    # The selector always includes the requested org, so a directly
    # addressed organisation with no projected activity stays visible
    # (tenancy remains explicit even on an empty screen).
    return {
        "org": org,
        "org_options": sorted({*queries.list_orgs(engine), org}),
        "active": active,
    }


@router.get("/")
def root() -> Response:
    return RedirectResponse("/ui")


@router.get("/ui")
def home(
    request: Request,
    org: str | None = None,
    engine: sa.Engine = Depends(get_engine),
    client: GlasshouseClient = Depends(get_client),
) -> Response:
    if not org:
        # The organisation picker is this same route without an org.
        return templates.TemplateResponse(request, "orgs.html", {"orgs": queries.list_orgs(engine)})
    context = _chrome(engine, org, "overview") | {
        "summary": queries.overview(engine, org=org),
        "health": health.checks(get_settings(), engine, client),
    }
    return templates.TemplateResponse(request, "overview.html", context)


@router.get("/ui/blotter")
def blotter(
    request: Request,
    org: str | None = None,
    book: str | None = None,
    market: str | None = None,
    offset: int = Query(default=0, ge=0),
    engine: sa.Engine = Depends(get_engine),
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    book, market = book or None, market or None
    # Ask for one row beyond the page to learn whether a next page
    # exists; the 51st row is never rendered.
    rows = queries.list_trades(
        engine, org=org, book=book, market=market, limit=PAGE_SIZE + 1, offset=offset
    )
    context: dict[str, Any] = {
        "org": org,
        "book": book,
        "market": market,
        "trades": rows[:PAGE_SIZE],
        "has_more": len(rows) > PAGE_SIZE,
        "offset": offset,
        "prev_offset": max(0, offset - PAGE_SIZE),
        "next_offset": offset + PAGE_SIZE,
    }
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/blotter_table.html", context)
    return templates.TemplateResponse(
        request, "blotter.html", _chrome(engine, org, "blotter") | context
    )


def _chains(versions: list[CurveVersion]) -> list[list[str]]:
    # Each supersession chain rendered newest-first: start from every
    # head (a version nothing supersedes) that has a lineage, and walk
    # the supersedes pointers back.
    by_version = {v.version: v for v in versions}
    chains = []
    for head in versions:
        if head.superseded_by is not None or head.supersedes is None:
            continue
        chain = [head.version]
        cursor = head
        while cursor.supersedes is not None and cursor.supersedes in by_version:
            chain.append(cursor.supersedes)
            cursor = by_version[cursor.supersedes]
        chains.append(chain)
    return chains


@router.get("/ui/curves")
def curves(
    request: Request,
    org: str | None = None,
    market: str | None = None,
    base: str | None = None,
    compare: str | None = None,
    engine: sa.Engine = Depends(get_engine),
    client: GlasshouseClient = Depends(get_client),
    store: CurveStore = Depends(get_store),
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    # Chrome first: a dead database is one verdict (the projection 503)
    # before any binary work.
    context = _chrome(engine, org, "curves")
    markets = curve_queries.list_markets(client, org=org)
    market = market or (markets[0] if markets else None)
    versions = (
        curve_queries.list_curve_versions(client, store, org=org, market=market) if market else []
    )
    diff = None
    base, compare = base or None, compare or None
    if market and base and compare:
        try:
            diff = curve_queries.curve_diff(
                client, store, org=org, market=market, base=base, compare=compare
            )
        except UnknownCurveVersionError as unknown:
            context |= {"title": "Unknown curve version", "message": str(unknown)}
            return templates.TemplateResponse(request, "error.html", context, status_code=404)
    context |= {
        "markets": markets,
        "market": market,
        "versions": versions,
        "chains": _chains(versions),
        "base": base,
        "compare": compare,
        "diff": diff,
    }
    return templates.TemplateResponse(request, "curves.html", context)


@router.get("/ui/positions")
def positions(
    request: Request,
    org: str | None = None,
    book: str | None = None,
    market: str | None = None,
    start: str | None = None,
    end: str | None = None,
    engine: sa.Engine = Depends(get_engine),
) -> Response:
    if not org:
        return RedirectResponse("/ui", status_code=303)
    book, market = book or None, market or None
    try:
        start_at, end_at = _utc_instant(start), _utc_instant(end)
    except ValueError:
        # Database-free on purpose (no chrome query): a malformed filter
        # is a 422 whatever state the read model is in.
        context: dict[str, Any] = {
            "org": org,
            "active": "positions",
            "title": "Check the time window",
            "message": "The From and To filters must look like 2026-07-01T00:00 "
            "and are read as UTC instants.",
        }
        return templates.TemplateResponse(request, "error.html", context, status_code=422)
    context = _chrome(engine, org, "positions") | {
        "book": book,
        "market": market,
        "start": start or "",
        "end": end or "",
        "positions": queries.list_positions(
            engine, org=org, book=book, market=market, start=start_at, end=end_at
        ),
        # The one filter bar governs both sections: the marks narrow by
        # the same book and market as the positions above them.
        "valuations": queries.list_valuations(
            engine, org=org, book=book, market=market, latest=True
        ),
    }
    return templates.TemplateResponse(request, "positions.html", context)
