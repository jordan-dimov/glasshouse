"""The needle's projections: blotter, hourly positions, valuations, and
the projector's cursor.

All four are projection-class under the read-side law: derived state
carrying the transition id it came from, rebuilt at any time by
replaying the log from zero (`glasshouse.projections.rebuild`)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blotter_trade",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("trade", sa.Text, primary_key=True),
        sa.Column("book", sa.Text, nullable=False),
        sa.Column("counterparty", sa.Text, nullable=False),
        sa.Column("market", sa.Text, nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("quantity", sa.Numeric, nullable=False),
        sa.Column("price", sa.Numeric, nullable=False),
        sa.Column("delivery_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
    )
    op.create_table(
        "position_hour",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("book", sa.Text, primary_key=True),
        sa.Column("market", sa.Text, primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("net_mw", sa.Numeric, nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
    )
    op.create_table(
        "trade_valuation",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("trade", sa.Text, primary_key=True),
        sa.Column("curve_version", sa.Text, primary_key=True),
        sa.Column("book", sa.Text, nullable=False),
        sa.Column("mtm", sa.Numeric, nullable=False),
        sa.Column("valued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
    )
    op.create_table(
        "projection_progress",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
    )


def downgrade() -> None:
    for table in ("projection_progress", "trade_valuation", "position_hour", "blotter_trade"):
        op.drop_table(table)
