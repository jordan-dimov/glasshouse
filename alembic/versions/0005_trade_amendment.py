"""Trade amendment on the read side: the blotter carries the current
terms version, the amendment trail gets a table of its own, and a mark
is pinned to its terms version as well as its curve version.

The projection-migration pattern (revision 0003): a projection's schema
change wipes the projection (and the cursor) and lets the next replay
refill it. Projections are derived state, so nothing is backfilled here
and NOT NULL is safe with no defaults. `position_hour` (the hypertable)
keeps its shape and is only wiped."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

PROJECTIONS = ("blotter_trade", "position_hour", "trade_valuation", "projection_progress")


def upgrade() -> None:
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    for name, kind in (
        ("trade_date", sa.Date),
        ("terms_version", sa.Text),
        ("effective_from", sa.Date),
        ("amendment_count", sa.Integer),
        ("terms_at", sa.DateTime(timezone=True)),
        ("terms_transition_id", sa.Text),
        ("terms_actor", sa.Text),
    ):
        op.add_column("blotter_trade", sa.Column(name, kind, nullable=False))
    op.create_table(
        "trade_terms_version",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("trade", sa.Text, primary_key=True),
        sa.Column("version", sa.Text, primary_key=True),
        sa.Column("prior_version", sa.Text, nullable=True),
        sa.Column("quantity", sa.Numeric, nullable=False),
        sa.Column("price", sa.Numeric, nullable=False),
        sa.Column("delivery_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
    )
    op.drop_constraint("trade_valuation_pkey", "trade_valuation", type_="primary")
    op.add_column("trade_valuation", sa.Column("terms_version", sa.Text, nullable=False))
    op.create_primary_key(
        "trade_valuation_pkey",
        "trade_valuation",
        ["org", "trade", "curve_version", "terms_version"],
    )


def downgrade() -> None:
    for table in (*PROJECTIONS, "trade_terms_version"):
        op.execute(f"DELETE FROM {table}")
    op.drop_constraint("trade_valuation_pkey", "trade_valuation", type_="primary")
    op.drop_column("trade_valuation", "terms_version")
    op.create_primary_key(
        "trade_valuation_pkey", "trade_valuation", ["org", "trade", "curve_version"]
    )
    op.drop_table("trade_terms_version")
    for name in (
        "terms_actor",
        "terms_transition_id",
        "terms_at",
        "amendment_count",
        "effective_from",
        "terms_version",
        "trade_date",
    ):
        op.drop_column("blotter_trade", name)
