"""The curve payload store: the first hash-anchored payload table.

The read-side law admits exactly two kinds of app-schema table:
projections and hash-anchored payloads. This is a payload table: bulk
curve content keyed by the registered identity, whose hash was admitted
in the governed claim (`CurveRegistered.payload_hash`). It is immutable
per version - a correction registers a new version with new content;
nothing here is ever updated in place, which is what keeps re-hashing
meaningful.

DDL lives in Alembic (app schema only; the morpholog schema is the
substrate's, provisioned by `morpholog init` and never touched here).
The table object below is the single Python-side definition, used by
queries and by Alembic autogenerate alike.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import sqlalchemy as sa

from glasshouse.compute.curves import HourlyCurve

metadata = sa.MetaData()

curve_payload_period = sa.Table(
    "curve_payload_period",
    metadata,
    sa.Column("org", sa.Text, primary_key=True),
    sa.Column("curve_version", sa.Text, primary_key=True),
    sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("price", sa.Numeric, nullable=False),  # exact: PG numeric <-> Decimal
)


class StoreError(RuntimeError):
    """The store cannot honour the request (absent payload, or an
    attempt to overwrite one - payloads are immutable per version)."""


def engine_url(database_url: str) -> str:
    """Normalise a libpq-style URL to the SQLAlchemy psycopg3 dialect."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


@dataclass(frozen=True, slots=True)
class CurveStore:
    engine: sa.Engine

    def save(self, *, org: str, version: str, curve: HourlyCurve) -> None:
        """Store one version's payload, once. Content goes in before the
        identity claim is proposed, so a committed claim never anchors
        missing content; if the proposal is then rejected, the orphaned
        payload is detectable garbage, not a lie in the ledger."""
        with self.engine.begin() as connection:
            existing = connection.execute(
                sa.select(sa.literal(True))
                .select_from(curve_payload_period)
                .where(
                    curve_payload_period.c.org == org,
                    curve_payload_period.c.curve_version == version,
                )
                .limit(1)
            ).scalar()
            if existing:
                raise StoreError(
                    f"payload for curve version {version!r} in org {org!r} already "
                    "stored; payloads are immutable - register a new version"
                )
            connection.execute(
                sa.insert(curve_payload_period),
                [
                    {
                        "org": org,
                        "curve_version": version,
                        "period_start": start,
                        "price": price,
                    }
                    for start, price in curve.periods
                ],
            )

    def load(self, *, org: str, version: str) -> HourlyCurve:
        with self.engine.connect() as connection:
            rows = connection.execute(
                sa.select(curve_payload_period.c.period_start, curve_payload_period.c.price)
                .where(
                    curve_payload_period.c.org == org,
                    curve_payload_period.c.curve_version == version,
                )
                .order_by(curve_payload_period.c.period_start)
            ).all()
        if not rows:
            raise StoreError(f"no payload stored for curve version {version!r} in org {org!r}")
        return HourlyCurve(tuple((row.period_start.astimezone(dt.UTC), row.price) for row in rows))
