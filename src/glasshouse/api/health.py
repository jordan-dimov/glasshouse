"""The readiness checks, extracted so `/readyz` and the Overview screen
render one public verdict (UI law 4: the health tile is a rendering of
the same query the deployment hook asks).

Independent verdicts: is the binary present and speaking, does the
database answer, do the two agree through a governed read, and - where
the projector runs in this process - is it actually getting anywhere.
Each failure is a verdict string, never an exception: a hanging binary
or a dead database is a readiness answer, not a 500.

The projector verdict lives HERE rather than in the `/readyz` route,
because the health tile on the Overview screen renders whatever this
returns. It was computed in the route, so the probe reported a verdict
the screen did not show - the screen said three things were fine while
the probe knew about a fourth. One query, one answer, both faces.
"""

from __future__ import annotations

import shutil
import subprocess

import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient, MorphologError
from glasshouse.config import Settings
from glasshouse.projections.runner import RunningProjector

# How many consecutive failed polls make a projector unready. The
# backoff doubles from the poll interval, so three failures is roughly
# fifteen seconds of continuous trouble: long enough to ride out a
# database restart or a nightly reset without crying wolf, short enough
# that a condition which will still be there at 02:30 is visible now.
FAILURE_THRESHOLD = 3


def checks(
    settings: Settings,
    engine: sa.Engine,
    client: GlasshouseClient,
    projector: RunningProjector | None = None,
) -> dict[str, str]:
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

    # Only the run modes that project IN THIS PROCESS can answer for the
    # projector: the worker profile's projector is another service's
    # readiness, and claiming a verdict for it would be invention.
    if projector is not None:
        progress = projector.status.progress()
        alive = projector.thread.is_alive()
        stuck = progress.consecutive_failures >= FAILURE_THRESHOLD
        verdicts["projector"] = "ok" if alive and not stuck else "error"

    return verdicts
