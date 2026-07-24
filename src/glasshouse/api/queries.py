"""The one projection-query layer (UI law 4).

Every screen is a rendering of a public API query: the JSON routers and
the server-rendered UI both call these functions, so there is no private
UI query and the UI is the standing proof that the API suffices. Each
function selects over a projection table (the primary read model, law 4),
scoped to one organisation (the tenancy boundary, law 6), and returns the
typed HTTP-boundary models - money and quantity stay exact Decimals end
to end.

A database that cannot answer raises `ReadUnavailableError`; the app-level
handler maps it to the same 503 verdict whichever edge asked. The
projection tables are a cache of the ledger, so their unavailability is
operational, never an internal error.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from glasshouse.api.schemas import (
    BlotterTrade,
    BookSummary,
    OverviewSummary,
    PositionHour,
    ProjectionCursor,
    TradeValuation,
    ValuationSummary,
)
from glasshouse.projections.projector import CURSOR
from glasshouse.projections.tables import (
    blotter_trade,
    position_hour,
    projection_progress,
    trade_valuation,
)


class ReadUnavailableError(Exception):
    """The projection tables (a cache of the ledger) cannot answer."""


def _rows(engine: sa.Engine, statement: sa.Select) -> list[dict]:  # type: ignore[type-arg]
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement).mappings()]
    except sa.exc.SQLAlchemyError as exc:
        raise ReadUnavailableError from exc


def list_orgs(engine: sa.Engine) -> list[str]:
    """Organisations currently represented in the projection read model -
    the ordered distinct union across the three tables, not an
    organisation registry (none exists yet). An org absent here is still
    a valid tenancy scope; it simply has no projected activity."""
    union = sa.union(
        sa.select(blotter_trade.c.org),
        sa.select(position_hour.c.org),
        sa.select(trade_valuation.c.org),
    ).subquery()
    statement = sa.select(union.c.org).order_by(union.c.org)
    return [row["org"] for row in _rows(engine, statement)]


def list_trades(
    engine: sa.Engine,
    *,
    org: str,
    book: str | None = None,
    market: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[BlotterTrade]:
    statement = sa.select(blotter_trade).where(blotter_trade.c.org == org)
    if book is not None:
        statement = statement.where(blotter_trade.c.book == book)
    if market is not None:
        statement = statement.where(blotter_trade.c.market == market)
    statement = statement.order_by(blotter_trade.c.trade)
    if limit is not None:
        statement = statement.limit(limit)
    if offset is not None:
        statement = statement.offset(offset)
    return [BlotterTrade.model_validate(row) for row in _rows(engine, statement)]


def list_positions(
    engine: sa.Engine,
    *,
    org: str,
    book: str | None = None,
    market: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[PositionHour]:
    statement = sa.select(position_hour).where(position_hour.c.org == org)
    if book is not None:
        statement = statement.where(position_hour.c.book == book)
    if market is not None:
        statement = statement.where(position_hour.c.market == market)
    if start is not None:
        statement = statement.where(position_hour.c.period_start >= start)
    if end is not None:
        statement = statement.where(position_hour.c.period_start < end)
    statement = statement.order_by(
        position_hour.c.book, position_hour.c.market, position_hour.c.period_start
    )
    return [PositionHour.model_validate(row) for row in _rows(engine, statement)]


def _latest_valuations(org: str) -> sa.Select:  # type: ignore[type-arg]
    # One row per trade: the latest admitted mark. `trade_valuation`
    # deliberately keeps the mark against every curve version a trade was
    # struck against (both survive a correction, law 2), so "current MtM"
    # must pick the newest per trade, never sum history.
    ranked = (
        sa.select(
            trade_valuation,
            sa.func.row_number()
            .over(
                partition_by=(trade_valuation.c.org, trade_valuation.c.trade),
                order_by=(
                    trade_valuation.c.valued_at.desc(),
                    trade_valuation.c.transition_id.desc(),
                ),
            )
            .label("rank"),
        )
        .where(trade_valuation.c.org == org)
        .subquery()
    )
    return (
        sa.select(*[ranked.c[column.name] for column in trade_valuation.c])
        .where(ranked.c.rank == 1)
        .order_by(ranked.c.trade)
    )


def list_valuations(
    engine: sa.Engine,
    *,
    org: str,
    trade: str | None = None,
    latest: bool = False,
) -> list[TradeValuation]:
    """All admitted marks by default (valuation history is a lawful,
    visible thing); `latest=True` narrows to the current mark per trade -
    the only rows that may ever be summed as current P&L."""
    if latest:
        statement = _latest_valuations(org)
        if trade is not None:
            statement = statement.where(statement.selected_columns.trade == trade)
    else:
        statement = sa.select(trade_valuation).where(trade_valuation.c.org == org)
        if trade is not None:
            statement = statement.where(trade_valuation.c.trade == trade)
        statement = statement.order_by(trade_valuation.c.trade, trade_valuation.c.curve_version)
    return [TradeValuation.model_validate(row) for row in _rows(engine, statement)]


def overview(engine: sa.Engine, *, org: str) -> OverviewSummary:
    """The operational landing tiles, read in one REPEATABLE READ
    snapshot so book counts, the current valuation summary and the
    projection cursor describe one coherent moment."""
    books_statement = (
        sa.select(blotter_trade.c.book, sa.func.count().label("trade_count"))
        .where(blotter_trade.c.org == org)
        .group_by(blotter_trade.c.book)
        .order_by(blotter_trade.c.book)
    )
    latest = _latest_valuations(org).subquery()
    valuation_statement = sa.select(
        sa.func.count().label("trade_count"),
        sa.func.max(latest.c.valued_at).label("valued_at"),
        sa.func.sum(latest.c.mtm).label("total_mtm"),
    )
    cursor_statement = sa.select(
        projection_progress.c.committed_at, projection_progress.c.transition_id
    ).where(projection_progress.c.name == CURSOR)

    try:
        with (
            engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
            connection.begin(),
        ):
            books = connection.execute(books_statement).all()
            marks = connection.execute(valuation_statement).one()
            cursor = connection.execute(cursor_statement).one_or_none()
    except sa.exc.SQLAlchemyError as exc:
        raise ReadUnavailableError from exc

    return OverviewSummary(
        org=org,
        books=[BookSummary(book=book, trade_count=count) for book, count in books],
        valuation=ValuationSummary(
            trade_count=marks.trade_count,
            valued_at=marks.valued_at,
            total_mtm=None if marks.total_mtm is None else Decimal(marks.total_mtm),
        ),
        projection=ProjectionCursor(
            committed_at=cursor.committed_at if cursor else None,
            transition_id=cursor.transition_id if cursor else None,
        ),
    )
