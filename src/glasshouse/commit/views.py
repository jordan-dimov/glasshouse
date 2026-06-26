"""The official inspection model: per-predicate SQL views over governed
state (law 4).

`morpholog generate views` emits one read-only view per declared
predicate over `morpholog.claims`, plus a `_morpholog_catalog` view that
stamps the programme's model hash. The script is committed byte-exact
(`morpholog_views.sql`) and drift-checked by regenerate-and-diff in the
integration leg, exactly like the generated Python client: the binary is
the authority, the positional JSONB mapping is the binary's (never
hand-rolled, which law 4 forbids), and our gate is that the committed
artefact still matches what the binary produces.

The views are the *official inspection model*, not the app's primary
read model - that stays the projection tables (law 4 again). Their
consumer is anything that speaks SQL (auditors, BI, the demo's "inspect
the governed claims" surface) plus `glasshouse verify`, which checks the
live catalogue still names the committed programme.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

# The committed, byte-exact view surface; regenerate with
# `morpholog generate views <MODEL_FILE> --out` and the drift gate keeps
# it honest against the pinned binary.
VIEWS_FILE = Path(__file__).parent / "morpholog_views.sql"

# Where the generated script lands its views and catalogue.
VIEWS_SCHEMA = "morpholog_views"


def apply_views(engine: sa.Engine) -> None:
    """Apply the committed view script in one shot.

    The script is a single transactional unit (`BEGIN; ... COMMIT;`), so
    a failure rolls back rather than leaving a half-built surface - the
    programmatic equivalent of `psql -v ON_ERROR_STOP=1`. We run it on a
    raw autocommit connection and let the script's own `BEGIN/COMMIT`
    bound the transaction; `CREATE OR REPLACE VIEW` makes re-application
    idempotent. Views live in the `morpholog_views` schema, namespaced
    away from the governed `morpholog` schema, so this never touches
    governed state.
    """
    script = VIEWS_FILE.read_text()
    raw = engine.raw_connection()
    try:
        # The real psycopg3 connection: in autocommit it runs the whole
        # multi-statement script in one call, and the script's own
        # BEGIN/COMMIT makes it atomic.
        driver = raw.driver_connection
        if driver is None:  # pragma: no cover - a live engine always has one
            raise RuntimeError("no driver connection to apply the view surface")
        driver.autocommit = True
        driver.execute(script)
    finally:
        raw.close()


def views_model_hash(engine: sa.Engine) -> str | None:
    """The model hash the live view surface names, or `None` if the
    surface is not applied. Read-only; `glasshouse verify` compares it to
    the committed `MODEL_HASH` to prove the official inspection model and
    the programme still agree."""
    catalog = sa.text(f'SELECT DISTINCT model_hash FROM "{VIEWS_SCHEMA}"."_morpholog_catalog"')
    try:
        with engine.connect() as connection:
            return connection.execute(catalog).scalar_one_or_none()
    except sa.exc.SQLAlchemyError:
        # The schema or catalogue view is absent: the surface has never
        # been applied. That is a verdict for the caller, not an error.
        return None
