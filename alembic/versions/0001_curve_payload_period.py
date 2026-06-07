"""curve_payload_period: the first hash-anchored payload table.

Bulk curve content keyed by registered identity; the hash over its
canonical form is admitted in the governed claim. Immutable per
version by application contract (the store refuses overwrites)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curve_payload_period",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("curve_version", sa.Text, primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("price", sa.Numeric, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("curve_payload_period")
