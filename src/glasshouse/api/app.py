"""FastAPI application factory.

The factory wires the API boundary's two dependencies - the pooled
engine and the commit-zone client - from settings over a lifespan, so
the engine pool is built on startup and disposed on shutdown rather than
leaked at import. Logging is configured for the running process at the
same point. `/readyz` answers the deployment hook's real question in
three independent verdicts: is the binary present and speaking, does the
database answer, and do the two agree through a governed read.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from glasshouse import __version__
from glasshouse.api import health
from glasshouse.api.deps import build_client, build_engine
from glasshouse.api.queries import ReadUnavailableError
from glasshouse.api.routers import explain, reads
from glasshouse.config import get_settings
from glasshouse.logging import configure_logging, get_logger
from glasshouse.web import STATIC_DIR
from glasshouse.web import routes as web
from glasshouse.web.routes import unavailable_page


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings)
        log = get_logger("glasshouse.api")
        engine = build_engine(settings)
        # The engine is built before the try, then disposed in the finally
        # whatever happens after: a client build that failed would
        # otherwise leak the pool on a half-completed startup.
        try:
            app.state.engine = engine
            app.state.client = build_client(settings)
            log.info("api.startup", environment=settings.environment, version=__version__)
            yield
        finally:
            engine.dispose()
            log.info("api.shutdown")

    app = FastAPI(
        title="Glasshouse",
        summary="The open ETRM core for European power.",
        version=__version__,
        lifespan=lifespan,
    )

    app.include_router(reads.router)
    app.include_router(explain.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(ReadUnavailableError)
    async def read_unavailable(request: Request, _exc: ReadUnavailableError) -> Response:
        # One verdict for every edge the shared query layer serves: the
        # Control Room gets the HTML face, everything else the JSON body
        # the pure tests pin. Do not improve the wording.
        path = request.url.path
        if path == "/ui" or path.startswith("/ui/"):
            return unavailable_page(request)
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, str]:
        verdicts = health.checks(settings, app.state.engine, app.state.client)
        if any(verdict != "ok" for verdict in verdicts.values()):
            response.status_code = 503
        return verdicts

    return app


app = create_app()
