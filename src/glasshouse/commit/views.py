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

    AUTOCOMMIT is requested as a SQLAlchemy execution option, so the
    driver runs the whole multi-statement script as sent and SQLAlchemy
    restores the connection's mode when it returns to the pool (setting
    raw `autocommit` by hand leaks the flag to the next checkout). The
    script carries its own `BEGIN; ... COMMIT;`, so it stays a single
    atomic unit - the programmatic equivalent of `psql -v ON_ERROR_STOP=1`
    - and `CREATE OR REPLACE VIEW` makes re-application idempotent. Views
    live in the `morpholog_views` schema, namespaced away from the
    governed `morpholog` schema, so this never touches governed state.
    """
    script = VIEWS_FILE.read_text()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(script)


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


def missing_catalogued_views(engine: sa.Engine) -> tuple[str, ...]:
    """Views the catalogue lists but that no longer exist - a dropped or
    renamed view the model-hash check alone would miss (the catalogue is
    a view too, so a hash read can succeed while a sibling is gone). An
    empty tuple means the inventory is whole; an absent surface also
    reads as empty (it is `views_model_hash`'s not-applied verdict)."""
    query = sa.text(
        f'SELECT c.view_name FROM "{VIEWS_SCHEMA}"."_morpholog_catalog" c '
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM information_schema.views v"
        "  WHERE v.table_schema = :schema AND v.table_name = c.view_name"
        ") ORDER BY c.view_name"
    )
    try:
        with engine.connect() as connection:
            return tuple(connection.execute(query, {"schema": VIEWS_SCHEMA}).scalars())
    except sa.exc.SQLAlchemyError:
        return ()
