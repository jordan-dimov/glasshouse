"""The templates rendered directly with fabricated typed rows: exact
decimal strings pass through untouched, negatives get the loud class,
the chrome states its honesty (Viewing: Current, readiness L0), and the
fragment is a fragment.
"""

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

from glasshouse.api.schemas import (
    AttestationInfo,
    AuditClaim,
    AuditEntry,
    BlotterTrade,
    BookSummary,
    CurveDiff,
    CurveDiffPeriod,
    CurveVersion,
    OverviewSummary,
    PositionHour,
    ProjectionCursor,
    TradeValuation,
    ValuationSummary,
    VersionMark,
)
from glasshouse.imports import ImportReport, RowOutcome
from glasshouse.imports.curves import COLUMNS as CURVE_COLUMNS
from glasshouse.imports.trades import COLUMNS as TRADE_COLUMNS
from glasshouse.projections.runner import ProjectorProgress
from glasshouse.verify import Leg, VerifyReport
from glasshouse.web.templating import _ago, templates

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def _now() -> dt.datetime:
    """Operational wall-clock, for the ages the screens render."""
    return dt.datetime.now(dt.UTC)


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
        health={"morpholog": "ok", "database": "ok", "commit": "error", "projector": "ok"},
        projector=ProjectorProgress(polled_at=_now(), applied_at=T0, applied_total=12),
    )
    assert "spec-de" in html
    assert "-66.25" in html
    assert "2026-07-01 00:00Z" in html  # instants render as explicit UTC
    assert "badge--ok" in html
    assert "badge--break" in html
    assert "error" in html  # the verdict is text, never colour alone
    # The projector's own progress, beside the cursor: a cursor moves
    # only when the ledger does, so its age alone cannot tell an idle
    # projector from a stuck one.
    assert "Last poll" in html
    assert "s ago" in html


def test_overview_says_when_a_failing_projector_is_failing() -> None:
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
        health={"morpholog": "ok", "database": "ok", "commit": "ok", "projector": "error"},
        projector=ProjectorProgress(
            polled_at=None, consecutive_failures=21, last_error="MorphologError"
        ),
    )
    assert "never" in html  # it has not once got through
    assert "21 consecutive, MorphologError" in html
    assert "badge--break" in html


def test_overview_does_not_invent_a_verdict_for_another_services_projector() -> None:
    # The worker profile projects in a different process. This screen can
    # see the cursor and nothing else, and says so rather than rendering
    # a blank where a number belongs.
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
        health={"morpholog": "ok", "database": "ok", "commit": "ok"},
        projector=None,
    )
    assert "Last poll" not in html
    assert "outside this process" in html


def test_curves_render_status_lineage_and_the_diff() -> None:
    versions = [
        CurveVersion(
            version="crv-a-v1",
            as_of=dt.date(2026, 7, 1),
            status="superseded",
            payload_hash="sha256:aa11",
            payload="ok",
            supersedes=None,
            superseded_by="crv-a-v2",
            mark_count=1,
            books=["spec-de"],
        ),
        CurveVersion(
            version="crv-a-v2",
            as_of=dt.date(2026, 7, 1),
            status="official",
            payload_hash="sha256:bb22",
            payload="mismatch",
            supersedes="crv-a-v1",
            superseded_by=None,
            mark_count=0,
            books=[],
        ),
    ]
    diff = CurveDiff(
        org="acme-energy",
        market="de-power",
        base="crv-a-v1",
        compare="crv-a-v2",
        periods=[
            CurveDiffPeriod(
                period_start=T0,
                base_price=Decimal("78"),
                compare_price=Decimal("75.5"),
                delta=Decimal("-2.5"),
            )
        ],
        base_marks=[VersionMark(trade="T-001", book="spec-de", mtm=Decimal("-220"))],
        compare_marks=[],
    )
    html = _render(
        "curves.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="curves",
        markets=["de-power"],
        market="de-power",
        versions=versions,
        chains=[["crv-a-v2", "crv-a-v1"]],
        base="crv-a-v1",
        compare="crv-a-v2",
        diff=diff,
    )
    assert "badge--official" in html
    assert "badge--superseded" in html
    assert ">mismatch<" in html  # the verdict is text, never colour alone
    assert "crv-a-v2</span> supersedes <span" in html  # the lineage line
    assert 'class="numeric neg">-2.5' in html  # a price drop is loud
    assert 'class="numeric neg">-220' in html  # the mark struck on the base version
    assert "Marks struck on" in html


def test_the_imports_page_derives_identity_from_the_login() -> None:
    html = _render(
        "imports.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="imports",
        authenticated_actor="demo",
    )
    assert 'name="actor"' not in html  # identity never comes from a form when logged in
    assert "Importing as <strong>demo</strong>" in html
    assert "gateway that asserted it" in html


def test_the_imports_page_states_the_contracts_truthfully() -> None:
    html = _render("imports.html", org="acme-energy", org_options=["acme-energy"], active="imports")
    # The rendered header lines carry exactly the column contracts the
    # import layer enforces - the template's canonical order is display,
    # this test keeps it honest against the frozensets.
    trades_line = (
        "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end"
    )
    curves_line = "market,as_of,version,period_start,price"
    assert trades_line in html
    assert curves_line in html
    assert set(trades_line.split(",")) == TRADE_COLUMNS
    assert set(curves_line.split(",")) == CURVE_COLUMNS
    assert "morpholog#204" in html  # the honest rejections-panel omission
    assert "asserted, not authenticated" in html


def test_the_preview_page_commits_nothing_and_says_so() -> None:
    report = ImportReport(
        (
            RowOutcome(ref="line 2", status="admissible", detail="admissible"),
            RowOutcome(ref="line 3", status="refused", detail="missing MayCaptureTrade(...)"),
            RowOutcome(ref="line 4", status="quarantined", detail="quantity: not a decimal"),
        )
    )
    html = _render(
        "import_preview.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="imports",
        kind="trades",
        actor="alice",
        filename="monday.csv",
        report=report,
        text_b64="Ym9vaw==",
    )
    assert "Nothing has been committed" in html
    assert "badge--admissible" in html
    assert "badge--refused" in html
    assert "badge--quarantined" in html
    assert "missing MayCaptureTrade" in html  # the why rides the row
    assert 'name="text_b64" value="Ym9vaw=="' in html


def test_the_receipts_page_accounts_for_every_row() -> None:
    report = ImportReport(
        (
            RowOutcome(ref="line 2", status="committed", detail="transition txn-1"),
            RowOutcome(ref="line 3", status="rejected", detail="would break invariant x"),
            RowOutcome(ref="line 4", status="error", detail="payload already stored"),
        )
    )
    html = _render(
        "imports_result.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="imports",
        kind="trades",
        actor="alice",
        report=report,
        applied=2,
    )
    assert "1 committed, 1 rejected, 1 errored, 0 quarantined" in html
    assert "projected: 2 transition(s) applied" in html
    # A failed catch-up never costs the operator their receipts: the
    # committed writes are stated, the lag is stated, nothing is lost.
    lagged = _render(
        "imports_result.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="imports",
        kind="trades",
        actor="alice",
        report=report,
        applied=None,
    )
    assert "projection catch-up failed" in lagged
    assert "receipts above are complete" in lagged
    assert "1 committed" in lagged
    assert "badge--committed" in html
    assert "badge--rejected" in html
    assert "badge--error" in html
    assert "safe by construction" in html  # the double-submit story, stated


def _audit_entry(attested: bool) -> AuditEntry:
    return AuditEntry(
        transition_id="txn-9f8e7d6c5b4a",
        committed_at=T0,
        transformation="capture_trade",
        actor="alice",
        attestation=(
            AttestationInfo(mode="gateway", authenticated_by="glasshouse_web") if attested else None
        ),
        asserted=[
            AuditClaim(predicate="TradeCaptured", args={"org": "acme-energy", "trade": "T-001"})
        ],
        retracted=[],
        invariants_checked=4,
        intents=0,
    )


def test_the_audit_page_is_scoped_by_default_and_says_so() -> None:
    html = _render(
        "audit.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="audit",
        rows=[(_audit_entry(attested=True), True), (_audit_entry(attested=False), True)],
        scope="org",
        total=2,
        ledger_total=5,
        offset=0,
        prev_offset=0,
        next_offset=50,
        has_more=False,
        report=None,
    )
    assert "a scoped view of the wider ledger (2 of 5)" in html
    assert "auditor view" in html  # the whole ledger is an explicit step
    assert "gateway via" in html  # attestation beside the actor
    assert "not recorded" in html  # pre-attestation rows render gracefully
    assert "TradeCaptured(org=acme-energy" in html
    assert "glasshouse evidence-verify" in html  # the offline pointer
    assert "whole ledger prefix, every tenant included" in html  # the pack is honest


def test_the_audit_pages_ledger_scope_labels_itself() -> None:
    html = _render(
        "audit.html",
        org="acme-energy",
        org_options=["acme-energy"],
        active="audit",
        rows=[(_audit_entry(attested=True), False)],
        scope="ledger",
        total=5,
        ledger_total=5,
        offset=0,
        prev_offset=0,
        next_offset=50,
        has_more=False,
        report=None,
    )
    assert "every tenant" in html
    assert "auditor view" in html


def test_the_verify_report_fragment_is_a_fragment_with_loud_failures() -> None:
    report = VerifyReport(
        (
            Leg("model", True, "binary and committed client agree"),
            Leg("projections", False, "blotter_trade: 1 missing, 0 unexpected"),
        )
    )
    html = templates.env.get_template("partials/verify_report.html").render(report=report)
    assert "<html" not in html
    assert "DIVERGENT" in html
    assert ">FAIL<" in html
    assert ">ok<" in html
    assert "1 missing" in html


def test_the_error_page_needs_no_context() -> None:
    html = templates.env.get_template("error.html").render()
    assert "database is unavailable" in html
    assert "Viewing: Current" in html  # the chrome still stands


def test_the_ago_filter_speaks_in_the_coarsest_honest_unit() -> None:
    # Operational ages only: a delivery period is an exact instant by
    # law, and never rendered like this.
    now = dt.datetime(2026, 7, 26, 12, 0, tzinfo=dt.UTC)
    assert _ago(None) == "never"
    assert _ago(now - dt.timedelta(seconds=5), now=now) == "5s ago"
    assert _ago(now - dt.timedelta(minutes=4), now=now) == "4m ago"
    assert _ago(now - dt.timedelta(hours=3), now=now) == "3h ago"
    assert _ago(now - dt.timedelta(days=9), now=now) == "9d ago"
    # A clock ahead of the database is shown as itself, not as "just now".
    assert _ago(now + dt.timedelta(minutes=1), now=now) == "ahead of this clock"
