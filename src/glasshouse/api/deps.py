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
from fastapi import Depends, Request

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
        writer_roles=settings.audit_writer_roles,
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
# as a parameter, and the accessor is named in exactly one place.
EngineDep = Annotated[sa.Engine, Depends(get_engine)]
ClientDep = Annotated[GlasshouseClient, Depends(get_client)]
StoreDep = Annotated[CurveStore, Depends(get_store)]
ProjectorDep = Annotated[RunningProjector | None, Depends(get_projector)]
