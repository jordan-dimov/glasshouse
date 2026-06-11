"""FastAPI application factory."""

import shutil
import subprocess

from fastapi import FastAPI, Response

from glasshouse import __version__
from glasshouse.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(
        title="Glasshouse",
        summary="The open ETRM core for European power.",
        version=__version__,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz(response: Response) -> dict[str, str]:
        settings = get_settings()
        checks: dict[str, str] = {}

        binary = shutil.which(settings.morpholog_bin)
        if binary is None:
            checks["morpholog"] = "missing"
        else:
            try:
                result = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, timeout=10
                )
                checks["morpholog"] = "ok" if result.returncode == 0 else "error"
            except (OSError, subprocess.TimeoutExpired):
                # A binary that hangs or cannot execute is a readiness
                # verdict, not a 500.
                checks["morpholog"] = "error"

        # Database connectivity and a commit-layer round-trip join this
        # check when the API boundary wires a GlasshouseClient (the API
        # milestone; the commit zone itself is built).
        if any(v != "ok" for v in checks.values()):
            response.status_code = 503
        return checks

    return app


app = create_app()
