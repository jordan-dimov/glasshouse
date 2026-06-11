"""The projection tables: the needle's read side.

Every table here is a projection under the read-side law: derived state
carrying the transition id it came from, rebuilt at any time by
replaying the log from zero (`projector.rebuild`). Nothing in this
module is ever the source of truth; the ledger is.

`projection_progress` is the projector's cursor - projection-class
bookkeeping under the same law (it records a position in the log and is
rebuilt from zero like everything else), kept as a table so an idle
projector can answer "am I caught up?" without scanning the data tables.

DDL lives in Alembic (revision 0002); the table objects below are the
single Python-side definition.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

# One row per trade: TradeCaptured joined with its TradeTerms, the
# blotter screen's backing table.
blotter_trade = sa.Table(
    "blotter_trade",
    metadata,
    sa.Column("org", sa.Text, primary_key=True),
    sa.Column("trade", sa.Text, primary_key=True),
    sa.Column("book", sa.Text, nullable=False),
    sa.Column("counterparty", sa.Text, nullable=False),
    sa.Column("market", sa.Text, nullable=False),
    sa.Column("direction", sa.Text, nullable=False),
    sa.Column("quantity", sa.Numeric, nullable=False),  # MW; exact
    sa.Column("price", sa.Numeric, nullable=False),
    sa.Column("delivery_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivery_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("transition_id", sa.Text, nullable=False),
    sa.Column("actor", sa.Text, nullable=False),  # who captured it - the evidence trail
)

# Net position per UTC delivery hour: the killer query GROUPs over this
# directly (buy +, sell -). Hypertable/continuous-aggregate conversion
# is a later optimisation that does not change the law.
position_hour = sa.Table(
    "position_hour",
    metadata,
    sa.Column("org", sa.Text, primary_key=True),
    sa.Column("book", sa.Text, primary_key=True),
    sa.Column("market", sa.Text, primary_key=True),
    sa.Column("period_start", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("net_mw", sa.Numeric, nullable=False),
    sa.Column("transition_id", sa.Text, nullable=False),  # last applied
)

# One row per admitted mark-to-market result, pinned to its curve
# version: both marks survive a correction, exactly like the ledger.
trade_valuation = sa.Table(
    "trade_valuation",
    metadata,
    sa.Column("org", sa.Text, primary_key=True),
    sa.Column("trade", sa.Text, primary_key=True),
    sa.Column("curve_version", sa.Text, primary_key=True),
    sa.Column("book", sa.Text, nullable=False),
    sa.Column("mtm", sa.Numeric, nullable=False),  # EUR; exact
    sa.Column("valued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("transition_id", sa.Text, nullable=False),
    sa.Column("actor", sa.Text, nullable=False),  # who admitted it - the evidence trail
)

# The projector's position in the log: (committed_at, transition_id) of
# the last transition applied, advanced in the same transaction as its
# effects, so application is exactly-once by construction.
projection_progress = sa.Table(
    "projection_progress",
    metadata,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("transition_id", sa.Text, nullable=False),
)
