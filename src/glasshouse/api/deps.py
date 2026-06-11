"""Construction and dependencies for the API boundary.

The app factory builds one engine (pooled, lazy - no connection until
first use) and one client (a stateless subprocess wrapper) from
settings at construction time and parks them on `app.state`; routers
take them through the `Depends`-compatible accessors below. Tests get
fresh objects per `create_app()` call, honouring whatever environment
they have just monkeypatched - no module-level singletons to reset.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import Request

from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import engine_url
from glasshouse.config import Settings


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
