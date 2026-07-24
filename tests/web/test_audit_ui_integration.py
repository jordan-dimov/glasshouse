"""The Audit/Evidence screen against the real stack: the ledger-wide
log newest-first with attestation beside every actor, the on-demand
six-leg verify panel, and the full offline evidence loop - a downloaded
pack plus a downloaded anchor verifying with no database access.
Self-provisioned module slate.
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.commit.morpholog_client.envelopes import TreeIntact
from glasshouse.compute.store import CurveStore
from glasshouse.seed import ORG, seed_demo
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def seeded() -> sa.Engine:
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    seed_demo(client, CurveStore(engine), engine)
    return engine


@pytest.fixture
def ui(seeded: sa.Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    return TestClient(create_app())


def test_the_log_is_newest_first_scoped_by_default_with_attestation(ui: TestClient) -> None:
    with ui as client:
        page = client.get("/ui/audit", params={"org": ORG})
        scoped = client.get("/audit", params={"org": ORG}).json()
        ledger = client.get("/audit", params={"org": ORG, "scope": "ledger"}).json()
    assert page.status_code == 200
    # The default view is scoped to the org and says so; the whole
    # ledger is an explicit auditor-view step.
    assert "a scoped view of the wider ledger" in page.text
    assert "auditor view" in page.text
    assert len(scoped) <= len(ledger)
    assert all(
        any(c["args"].get("org") == ORG for c in (*e["asserted"], *e["retracted"])) for e in scoped
    )
    # The seed ends with valuations, so the newest transformation is
    # admit_valuation, in both scopes.
    assert "admit_valuation" in page.text
    assert scoped[0]["transformation"] == "admit_valuation"
    committed = [e["committed_at"] for e in ledger]
    assert committed == sorted(committed, reverse=True)
    # Every row written by the current binary carries its attestation.
    assert "gateway via" in page.text
    assert all(e["attestation"]["mode"] == "gateway" for e in ledger)
    assert "audit-row--org" in page.text


def test_the_ledger_scope_shows_every_tenant_and_labels_itself(ui: TestClient) -> None:
    with ui as client:
        page = client.get("/ui/audit", params={"org": ORG, "scope": "ledger"})
    assert page.status_code == 200
    assert "every tenant" in page.text
    assert "auditor view" in page.text


def test_the_verify_panel_renders_six_green_legs(ui: TestClient) -> None:
    with ui as client:
        full_page = client.post("/ui/audit/verify", data={"org": ORG})
        fragment = client.post(
            "/ui/audit/verify", data={"org": ORG}, headers={"HX-Request": "true"}
        )
    assert full_page.status_code == 200
    assert "Verification: consistent" in full_page.text
    for leg in ("model", "ledger", "tree", "projections", "payloads", "views"):
        assert leg in full_page.text
    assert "FAIL" not in full_page.text
    assert "<html" not in fragment.text  # the HTMX face is a fragment


def test_the_evidence_loop_closes_offline(ui: TestClient, tmp_path) -> None:  # type: ignore[no-untyped-def]
    with ui as client:
        anchor = client.post("/ui/audit/checkpoint")
        pack = client.get("/ui/audit/evidence-pack")
    assert anchor.status_code == 200
    assert "attachment" in anchor.headers["content-disposition"]
    assert pack.status_code == 200
    pack_file = tmp_path / "pack.json"
    anchor_file = tmp_path / "anchor.json"
    pack_file.write_bytes(pack.content)
    anchor_file.write_bytes(anchor.content)
    # The downloads verify offline against each other - no database.
    offline = GlasshouseClient(str(MODEL_FILE), "", binary=str(BINARY))
    verdict = offline.evidence_verify(str(pack_file), anchor_file=str(anchor_file))
    assert isinstance(verdict, TreeIntact)
