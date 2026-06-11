"""FastAPI application factory.

The factory wires the API boundary's two dependencies - the pooled
engine and the commit-zone client - from settings at construction time
(see `deps.py`). `/readyz` answers the deployment hook's real question
in three independent verdicts: is the binary present and speaking, does
the database answer, and do the two agree through a governed read.
"""

import shutil
import subprocess

import sqlalchemy as sa
from fastapi import FastAPI, Response

from glasshouse import __version__
from glasshouse.api.deps import build_client, build_engine
from glasshouse.commit import MorphologError
from glasshouse.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Glasshouse",
        summary="The open ETRM core for European power.",
        version=__version__,
    )
    app.state.engine = build_engine(settings)
    app.state.client = build_client(settings)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, str]:
        checks: dict[str, str] = {}

        binary = shutil.which(settings.morpholog_bin)
        if binary is None:
            checks["morpholog"] = "missing"
        else:
            try:
                result = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
                )
                checks["morpholog"] = "ok" if result.returncode == 0 else "error"
            except (OSError, subprocess.TimeoutExpired):
                # A binary that hangs or cannot execute is a readiness
                # verdict, not a 500.
                checks["morpholog"] = "error"

        try:
            with app.state.engine.connect() as connection:
                connection.execute(sa.text("select 1"))
            checks["database"] = "ok"
        except sa.exc.SQLAlchemyError:
            checks["database"] = "error"

        # The commit layer: binary, database and provisioned schema
        # agreeing through one cheap governed read (an unknown
        # predicate is a true zero on a provisioned ledger, and an
        # operational error on anything less).
        try:
            app.state.client.claims("ReadinessProbe")
            checks["commit"] = "ok"
        except (MorphologError, OSError, subprocess.TimeoutExpired):
            checks["commit"] = "error"

        if any(verdict != "ok" for verdict in checks.values()):
            response.status_code = 503
        return checks

    return app


app = create_app()
