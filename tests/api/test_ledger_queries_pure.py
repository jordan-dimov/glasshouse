"""The ledger query layer's deterministic legs: the pure diff and
org-mention helpers over fabricated rows, and the pinned JSON 503 when
the commit layer cannot answer (a fake binary that fails operationally,
so the verdict holds whatever is installed locally).
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.api.audit import mentions_org
from glasshouse.api.curves import _diff_periods
from glasshouse.api.schemas import AttestationInfo, AuditClaim, AuditEntry
from glasshouse.compute.curves import HourlyCurve
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def _curve(start_hour: int, prices: list[str]) -> HourlyCurve:
    return HourlyCurve(
        tuple((T0 + dt.timedelta(hours=start_hour + i), Decimal(p)) for i, p in enumerate(prices))
    )


def test_diff_periods_align_by_hour_and_carry_deltas() -> None:
    base = _curve(0, ["70", "71", "72"])
    compare = _curve(0, ["70", "74", "69.5"])
    rows = _diff_periods(base, compare)
    assert [(r.base_price, r.compare_price, r.delta) for r in rows] == [
        (Decimal("70"), Decimal("70"), Decimal("0")),
        (Decimal("71"), Decimal("74"), Decimal("3")),
        (Decimal("72"), Decimal("69.5"), Decimal("-2.5")),  # a drop: negative, exact
    ]


def test_diff_periods_leave_unmatched_hours_without_a_delta() -> None:
    base = _curve(0, ["70", "71"])
    compare = _curve(1, ["80", "81"])  # overlaps only at hour 1
    rows = _diff_periods(base, compare)
    assert [(r.base_price, r.compare_price, r.delta) for r in rows] == [
        (Decimal("70"), None, None),
        (Decimal("71"), Decimal("80"), Decimal("9")),
        (None, Decimal("81"), None),
    ]


def _entry(asserted: list[AuditClaim]) -> AuditEntry:
    return AuditEntry(
        transition_id="txn-1",
        committed_at=T0,
        transformation="capture_trade",
        actor="alice",
        attestation=AttestationInfo(mode="gateway", authenticated_by="glasshouse_web"),
        asserted=asserted,
        retracted=[],
        invariants_checked=3,
        intents=0,
    )


def test_mentions_org_is_a_named_field_lookup_never_a_guess() -> None:
    ours = _entry([AuditClaim(predicate="TradeCaptured", args={"org": "acme-energy"})])
    theirs = _entry([AuditClaim(predicate="TradeCaptured", args={"org": "someone-else"})])
    orgless = _entry([AuditClaim(predicate="CurveSupersedes", args={"new_version": "v2"})])
    assert mentions_org(ours, "acme-energy")
    assert not mentions_org(theirs, "acme-energy")
    assert not mentions_org(orgless, "acme-energy")


@pytest.fixture
def dead_stack(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)
    # An operationally failing binary (empty stdout, exit 1) makes the
    # commit layer's unavailability deterministic whatever is on PATH.
    broken = fake_binary(tmp_path, "", stderr="Error: database unreachable", exit_code=1)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(broken))


@pytest.mark.parametrize(
    "endpoint",
    [
        "/markets",
        "/curves",
        "/curves/diff",
        "/audit",
    ],
)
def test_a_dead_commit_layer_is_the_pinned_json_503(dead_stack: None, endpoint: str) -> None:
    params = {
        "org": "acme-energy",
        "market": "de-power",
        "base": "v1",
        "compare": "v2",
    }
    with TestClient(create_app()) as client:
        response = client.get(endpoint, params=params)
    assert response.status_code == 503
    assert response.json() == {"detail": "commit layer unavailable"}
