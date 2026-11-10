"""Currency on the read side: each market's declared currency gets a
table, and a mark carries the currency it states.

The projection-migration pattern (revision 0003): a projection's schema
change wipes the projection (and the cursor) and lets the next replay
refill it. Projections are derived state, so nothing is backfilled here
and NOT NULL is safe with no defaults.

This revision arrives with a change to the ledger's own rules (a mark
gains a currency field, and every market must be declared). No ledger
was being kept when it was made, so it has no upgrade path: a database
from before it is re-seeded, not migrated. See "Upgrading" in
docs/development.md."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

PROJECTIONS = (
    "blotter_trade",
    "trade_terms_version",
    "position_hour",
    "trade_valuation",
    "projection_progress",
)


def upgrade() -> None:
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    op.add_column("trade_valuation", sa.Column("currency", sa.Text, nullable=False))
    op.create_table(
        "market_currency",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("market", sa.Text, primary_key=True),
        sa.Column("currency", sa.Text, nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
    )


def downgrade() -> None:
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    op.drop_table("market_currency")
    op.drop_column("trade_valuation", "currency")
