"""position_hour becomes a TimescaleDB hypertable.

position_hour is the time-series projection the killer query groups over
(net MW per UTC delivery hour); a hypertable partitions it by
period_start into chunks, so a period-windowed read (the API's
``/positions?start=&end=``) prunes to the chunks it needs instead of
scanning the whole table. Only the *core* hypertable is used - chunk
exclusion - which is TimescaleDB's Apache-2 feature set; continuous
aggregates and compression (TSL "community" features) are deliberately
not used, which keeps the stack Apache-2 and works on managed Postgres
that ships only the Apache-2 subset.

This follows the projection-migration pattern (0003): a projection's
storage change wipes the projection and the cursor, and the next
catch-up refills it by replay - projections are derived state, the
migration never backfills, the log does. So position_hour is empty when
it is converted, and the partition column (period_start) is part of its
primary key, which a hypertable's unique constraints require.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PROJECTIONS = ("blotter_trade", "position_hour", "trade_valuation", "projection_progress")

# Power positions are viewed by delivery month; a one-month chunk keeps
# the chunk count low over a multi-year delivery horizon while still
# pruning period-windowed reads.
CHUNK_INTERVAL = "1 month"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    # Wipe the read side and the cursor; replay refills it after the
    # conversion (so the table is empty when it becomes a hypertable).
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    op.execute(
        "SELECT create_hypertable("
        "'position_hour', 'period_start', "
        f"chunk_time_interval => INTERVAL '{CHUNK_INTERVAL}', "
        "migrate_data => true)"
    )


def downgrade() -> None:
    # No direct "un-hypertable": drop and recreate position_hour as a
    # plain table (its 0002 shape), then let replay refill it. The
    # extension is left in place - dropping it is global and other state
    # may depend on it.
    for table in PROJECTIONS:
        op.execute(f"DELETE FROM {table}")
    op.drop_table("position_hour")
    op.create_table(
        "position_hour",
        sa.Column("org", sa.Text, primary_key=True),
        sa.Column("book", sa.Text, primary_key=True),
        sa.Column("market", sa.Text, primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("net_mw", sa.Numeric, nullable=False),
        sa.Column("transition_id", sa.Text, nullable=False),
    )
