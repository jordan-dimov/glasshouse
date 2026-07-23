"""The readiness checks, extracted so `/readyz` and the Overview screen
render one public verdict (UI law 4: the health tile is a rendering of
the same query the deployment hook asks).

Three independent verdicts: is the binary present and speaking, does the
database answer, and do the two agree through a governed read. Each
failure is a verdict string, never an exception - a hanging binary or a
dead database is a readiness answer, not a 500.
"""

from __future__ import annotations

import shutil
import subprocess

import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient, MorphologError
from glasshouse.config import Settings


def checks(settings: Settings, engine: sa.Engine, client: GlasshouseClient) -> dict[str, str]:
    verdicts: dict[str, str] = {}

    binary = shutil.which(settings.morpholog_bin)
    if binary is None:
        verdicts["morpholog"] = "missing"
    else:
        try:
            result = subprocess.run(
                [binary, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
            verdicts["morpholog"] = "ok" if result.returncode == 0 else "error"
        except (OSError, subprocess.TimeoutExpired):
            # A binary that hangs or cannot execute is a readiness
            # verdict, not a 500.
            verdicts["morpholog"] = "error"

    try:
        with engine.connect() as connection:
            connection.execute(sa.text("select 1"))
        verdicts["database"] = "ok"
    except sa.exc.SQLAlchemyError:
        verdicts["database"] = "error"

    # The commit layer: binary, database, the committed model file and
    # the provisioned schema agreeing through one cheap governed read.
    # Named on purpose - the named surface makes the programme the
    # authority, so this proves the model too; the client's timeout makes
    # a hang a fast verdict.
    try:
        client.claims_named("MayCaptureTrade")
        verdicts["commit"] = "ok"
    except (MorphologError, OSError):
        verdicts["commit"] = "error"

    return verdicts
