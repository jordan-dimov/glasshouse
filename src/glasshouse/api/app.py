"""FastAPI application factory.

The factory wires the API boundary's two dependencies - the pooled
engine and the commit-zone client - from settings over a lifespan, so
the engine pool is built on startup and disposed on shutdown rather than
leaked at import. Logging is configured for the running process at the
same point. `/readyz` answers the deployment hook's real question through
`health.checks` - the same call the Overview screen's health tile
renders, so the probe and the screen can never disagree.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from glasshouse import __version__
from glasshouse.api import health
from glasshouse.api.auth import DEMO_USERNAME, DemoAuthMiddleware
from glasshouse.api.deps import build_client, build_engine
from glasshouse.api.queries import ReadUnavailableError
from glasshouse.api.routers import audit, curves, explain, reads
from glasshouse.commit import MorphologError
from glasshouse.compute.store import CurveStore
from glasshouse.config import get_settings
from glasshouse.logging import configure_logging, get_logger
from glasshouse.projections.runner import RunningProjector, start_projector_thread
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
        projector: RunningProjector | None = None
        try:
            app.state.engine = engine
            app.state.client = build_client(settings)
            app.state.store = CurveStore(engine)
            app.state.projector = None
            if settings.environment == "demo":
                # The DESIGN section 13 demo profile: one process, the
                # background-thread projector. Dev composes its own run
                # mode; production runs the separate worker. It rides
                # app.state so the health checks can answer for its
                # PROGRESS, not merely its liveness.
                projector = start_projector_thread(app.state.client, engine)
                app.state.projector = projector
            log.info("api.startup", environment=settings.environment, version=__version__)
            yield
        finally:
            if projector is not None:
                thread, stop = projector.thread, projector.stop
                stop.set()
                # Joined BEFORE the engine is disposed: the thread reads
                # through this pool. A join that times out is loud, and
                # gets one longer chance - disposing under a live thread
                # would break the lifecycle guarantee silently.
                thread.join(timeout=5)
                if thread.is_alive():
                    log.error("api.projector_join_timed_out", waited_seconds=5)
                    thread.join(timeout=30)
                    if thread.is_alive():
                        log.error("api.projector_still_alive_at_dispose", waited_seconds=35)
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
    app.include_router(curves.router)
    app.include_router(audit.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    if settings.demo_password is not None:
        # The shared demo login: one gate in front of everything except
        # the deployment probes. Added last = outermost, so it answers
        # before routing and the 503 faces below see only authenticated
        # traffic.
        app.add_middleware(
            DemoAuthMiddleware, username=DEMO_USERNAME, password=settings.demo_password
        )

    @app.exception_handler(ReadUnavailableError)
    async def read_unavailable(request: Request, _exc: ReadUnavailableError) -> Response:
        # One verdict for every edge the shared query layer serves: the
        # Control Room gets the HTML face, everything else the JSON body
        # the pure tests pin. Do not improve the wording.
        path = request.url.path
        if path == "/ui" or path.startswith("/ui/"):
            return unavailable_page(request)
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.exception_handler(MorphologError)
    async def commit_layer_unavailable(request: Request, _exc: MorphologError) -> Response:
        # The ledger query layer's twin of the handler above: a binary
        # that cannot answer is the same operational verdict as a dead
        # database, never a 500. Routers that handle MorphologError
        # themselves (the explain endpoint's 502) are untouched - this
        # only catches what nothing else did. Body pinned by pure tests.
        path = request.url.path
        if path == "/ui" or path.startswith("/ui/"):
            return unavailable_page(request)
        return JSONResponse({"detail": "commit layer unavailable"}, status_code=503)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, str]:
        # The projector verdict comes from the shared checks (which the
        # Overview tile renders too), never from this route.
        verdicts = health.checks(settings, app.state.engine, app.state.client, app.state.projector)
        if any(verdict != "ok" for verdict in verdicts.values()):
            response.status_code = 503
        return verdicts

    return app


app = create_app()
