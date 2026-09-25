"""The projection tables: the needle's read side.

Every table here is a projection under the read-side law: derived state
carrying the transition id it came from, rebuilt at any time by
replaying the log from zero (`projector.rebuild`). Nothing in this
module is ever the source of truth; the ledger is.

`projection_progress` is the projector's cursor - projection-class
bookkeeping under the same law (it records a position in the log and is
rebuilt from zero like everything else), kept as a table so an idle
projector can answer "am I caught up?" without scanning the data tables.

DDL lives in Alembic (revisions 0002-0005); the table objects below are
the single Python-side definition. Columns added by a later revision are
listed LAST in their table, whatever their physical position in a
migrated database: the projector's tuples and `verify`'s `select(table)`
both follow this declaration order, so the two agree on every database.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

# One row per trade: TradeCaptured joined with its CURRENT TradeTerms
# (the version with the latest effective date in the log, never the
# wall clock), the blotter screen's backing table. `captured_at`,
# `transition_id` and `actor` are the capture's provenance; the `terms_*`
# columns are the current version's, which an amendment moves.
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
    # Revision 0005: the current terms version and its provenance.
    sa.Column("trade_date", sa.Date, nullable=False),  # the first version's effective date
    sa.Column("terms_version", sa.Text, nullable=False),
    sa.Column("effective_from", sa.Date, nullable=False),
    sa.Column("amendment_count", sa.Integer, nullable=False),
    sa.Column("terms_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("terms_transition_id", sa.Text, nullable=False),
    sa.Column("terms_actor", sa.Text, nullable=False),
)

# Every version of every trade's terms, as admitted: the amendment
# trail. `prior_version` is NULL for the version a capture admitted and
# names the superseded version for an amendment (the lineage claim).
trade_terms_version = sa.Table(
    "trade_terms_version",
    metadata,
    sa.Column("org", sa.Text, primary_key=True),
    sa.Column("trade", sa.Text, primary_key=True),
    sa.Column("version", sa.Text, primary_key=True),
    sa.Column("prior_version", sa.Text, nullable=True),
    sa.Column("quantity", sa.Numeric, nullable=False),  # MW; exact
    sa.Column("price", sa.Numeric, nullable=False),
    sa.Column("delivery_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("delivery_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("effective_from", sa.Date, nullable=False),
    sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("transition_id", sa.Text, nullable=False),
    sa.Column("actor", sa.Text, nullable=False),
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

# One row per admitted mark-to-market result, pinned to the curve
# version AND the terms version it was computed against: every mark
# survives a correction or an amendment, exactly like the ledger.
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
    # Revision 0005: part of the key, listed last (see the module note).
    sa.Column("terms_version", sa.Text, primary_key=True),
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
