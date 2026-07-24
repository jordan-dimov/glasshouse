"""The templates rendered directly with fabricated typed rows: exact
decimal strings pass through untouched, negatives get the loud class,
the chrome states its honesty (Viewing: Current, readiness L0), and the
fragment is a fragment.
"""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from glasshouse.api.schemas import (
    BlotterTrade,
    BookSummary,
    OverviewSummary,
    PositionHour,
    ProjectionCursor,
    TradeValuation,
    ValuationSummary,
)
from glasshouse.web.templating import templates

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
REQUEST = SimpleNamespace(url=SimpleNamespace(path="/ui"))


def _render(name: str, **context: object) -> str:
    return templates.env.get_template(name).render(request=REQUEST, **context)


def _sell(trade: str = "T-002") -> BlotterTrade:
    return BlotterTrade(
        org="acme-energy",
        trade=trade,
        book="spec-de",
        counterparty="stadtwerk-x",
        market="de-power",
        direction="sell",
        quantity=Decimal("7.5"),
        price=Decimal("86.25"),
        delivery_start=T0,
        delivery_end=T0 + dt.timedelta(hours=2),
        captured_at=T0,
        transition_id="txn-0123456789abcdef",
        actor="alice",
    )


def test_blotter_renders_exact_decimals_and_the_chrome() -> None:
    html = _render(
        "blotter.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="blotter",
        book=None,
        market=None,
        trades=[_sell()],
        has_more=False,
        offset=0,
        prev_offset=0,
        next_offset=50,
    )
    assert "86.25" in html  # exact, straight through, no float anywhere
    assert "7.5" in html
    assert "sell" in html
    assert "Viewing: Current" in html
    assert "readiness L0" in html
    assert 'class="numeric' in html
    assert 'title="txn-0123456789abcdef"' in html  # the full id one hover away
    assert 'aria-current="page"' in html


def test_the_blotter_fragment_is_a_fragment() -> None:
    html = _render(
        "partials/blotter_table.html",
        org="acme-energy",
        book=None,
        market=None,
        trades=[_sell()],
        has_more=True,
        offset=0,
        prev_offset=0,
        next_offset=50,
    )
    assert "<html" not in html
    assert "<body" not in html
    assert "Next 50" in html


def test_negatives_are_loud_on_positions_and_marks() -> None:
    html = _render(
        "positions.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="positions",
        book=None,
        market=None,
        start="",
        end="",
        positions=[
            PositionHour(
                org="acme-energy",
                book="spec-de",
                market="de-power",
                period_start=T0,
                net_mw=Decimal("-7.5"),
                transition_id="txn-1",
            )
        ],
        valuations=[
            TradeValuation(
                org="acme-energy",
                trade="T-002",
                curve_version="crv-v2",
                book="spec-de",
                mtm=Decimal("-101.25"),
                valued_at=T0,
                transition_id="txn-2",
                actor="risk-engine",
            )
        ],
    )
    assert 'class="numeric neg">-7.5' in html
    assert 'class="numeric neg">-101.25' in html
    assert "crv-v2" in html  # the mark's in-place explanation


def test_overview_renders_the_tiles_and_health() -> None:
    summary = OverviewSummary(
        org="acme-energy",
        books=[BookSummary(book="spec-de", trade_count=2)],
        valuation=ValuationSummary(trade_count=2, valued_at=T0, total_mtm=Decimal("-66.25")),
        projection=ProjectionCursor(committed_at=T0, transition_id="txn-3"),
    )
    html = _render(
        "overview.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="overview",
        summary=summary,
        health={"morpholog": "ok", "database": "ok", "commit": "error"},
    )
    assert "spec-de" in html
    assert "-66.25" in html
    assert "2026-07-01 00:00Z" in html  # instants render as explicit UTC
    assert "badge--ok" in html
    assert "badge--break" in html
    assert "error" in html  # the verdict is text, never colour alone


def test_the_error_page_needs_no_context() -> None:
    html = templates.env.get_template("error.html").render()
    assert "database is unavailable" in html
    assert "Viewing: Current" in html  # the chrome still stands
