"""The curve read endpoints: versions, markets, and the two-version
diff - the JSON twins of the Curves screen (UI law 4). Backed by the
pinned typed ledger surface via `glasshouse.api.curves`, never a
projection table (none exists for curves) and never raw JSONB.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from glasshouse.api import curves as curve_queries
from glasshouse.api.deps import ClientDep, StoreDep
from glasshouse.api.schemas import CurveDiff, CurveVersion

router = APIRouter(tags=["curves"])


@router.get("/markets")
def list_markets(org: str, client: ClientDep) -> list[str]:
    return curve_queries.list_markets(client, org=org)


@router.get("/curves")
def list_curves(
    org: str,
    market: str,
    client: ClientDep,
    store: StoreDep,
) -> list[CurveVersion]:
    return curve_queries.list_curve_versions(client, store, org=org, market=market)


@router.get("/curves/diff")
def diff_curves(
    org: str,
    market: str,
    base: str,
    compare: str,
    client: ClientDep,
    store: StoreDep,
) -> CurveDiff:
    try:
        return curve_queries.curve_diff(
            client, store, org=org, market=market, base=base, compare=compare
        )
    except curve_queries.UnknownCurveVersionError as unknown:
        raise HTTPException(status_code=404, detail=str(unknown)) from unknown
    except curve_queries.IncomparableCurvesError as incomparable:
        raise HTTPException(status_code=422, detail=str(incomparable)) from incomparable
    except curve_queries.CurveIntegrityError as broken:
        # An integrity break between ledger and app schema, never a
        # not-found: alarming on purpose.
        raise HTTPException(status_code=409, detail=str(broken)) from broken
