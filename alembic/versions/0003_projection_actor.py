"""Evidence on the projections: blotter and valuation rows carry the
actor from the transition that produced them.

This revision establishes the projection-migration pattern: a
projection's schema change wipes the projection (and the cursor) and
lets the next catch-up refill it by replay - projections are derived
state, so the migration never backfills, the log does. NOT NULL is
therefore safe with no defaults."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PROJECTIONS = ("blotter_trade", "position_hour", "trade_valuation", "projection_progress")


def upgrade() -> None:
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    op.add_column("blotter_trade", sa.Column("actor", sa.Text, nullable=False))
    op.add_column("trade_valuation", sa.Column("actor", sa.Text, nullable=False))


def downgrade() -> None:
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    op.drop_column("trade_valuation", "actor")
    op.drop_column("blotter_trade", "actor")
