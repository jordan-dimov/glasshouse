"""Construction and dependencies for the API boundary.

The app factory wires one engine (pooled, lazy - no connection until
first use) and one client (a stateless subprocess wrapper) from settings
over the app's lifespan: built on startup, the engine pool disposed on
shutdown (see `app.py`). They are parked on `app.state`; routers take
them through the `Annotated` dependency aliases below (one word per
parameter, the accessor named once). Tests get fresh
objects per `create_app()` call - entered through `TestClient` as a
context manager so the lifespan runs - honouring whatever environment
they have just monkeypatched, no module-level singletons to reset.
"""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Query, Request

from glasshouse.api import asof
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.config import Settings
from glasshouse.projections.runner import RunningProjector


def build_engine(settings: Settings) -> sa.Engine:
    # connect_timeout keeps a dead database a fast, honest verdict
    # (readiness checks included) instead of a hang.
    return sa.create_engine(engine_url(settings.database_url), connect_args={"connect_timeout": 5})


def build_client(settings: Settings) -> GlasshouseClient:
    # Bounded at the API boundary: a hung binary must become a fast
    # verdict, never a stuck request. The CLI's imports run unbounded.
    return GlasshouseClient(
        str(MODEL_FILE),
        settings.database_url,
        binary=settings.morpholog_bin,
        timeout_seconds=settings.morpholog_timeout_seconds,
    )


def get_engine(request: Request) -> sa.Engine:
    engine: sa.Engine = request.app.state.engine
    return engine


def get_client(request: Request) -> GlasshouseClient:
    client: GlasshouseClient = request.app.state.client
    return client


def get_store(request: Request) -> CurveStore:
    store: CurveStore = request.app.state.store
    return store


def get_projector(request: Request) -> RunningProjector | None:
    """None in every run mode that does not project in this process (the
    worker profile, and dev): the health checks then say nothing about a
    projector rather than inventing a verdict for another service."""
    projector: RunningProjector | None = request.app.state.projector
    return projector


# The aliases routers spell dependencies with: `engine: EngineDep` reads
# as a parameter, and the accessor is named in exactly one place. PEP 695
# aliases: FastAPI resolves a `TypeAliasType` at runtime since 0.128.2.
type EngineDep = Annotated[sa.Engine, Depends(get_engine)]
type ClientDep = Annotated[GlasshouseClient, Depends(get_client)]
type StoreDep = Annotated[CurveStore, Depends(get_store)]
type ProjectorDep = Annotated[RunningProjector | None, Depends(get_projector)]


def get_past(
    client: ClientDep,
    as_of: Annotated[
        str | None,
        Query(
            description="answer as the book stood at this past instant (ISO 8601; no offset "
            "means UTC). Omitted: the present."
        ),
    ] = None,
) -> asof.Snapshot | None:
    """The as-of mode of a read, resolved once per request: None for the
    present, otherwise the replayed snapshot every query of this request
    answers from. A malformed or future instant is the caller's error
    (422). An instant whose history can still change is refused as a 409
    with `Retry-After`: the request is sound and will be answerable."""
    try:
        at = asof.parse_instant(as_of)
        return None if at is None else asof.snapshot(client, at)
    except ValueError as refused:
        raise HTTPException(status_code=422, detail=f"as_of: {refused}") from refused
    except asof.NotSettledError as unsettled:
        raise HTTPException(
            status_code=409, detail=f"as_of: {unsettled}", headers={"Retry-After": "1"}
        ) from unsettled


type PastDep = Annotated[asof.Snapshot | None, Depends(get_past)]
