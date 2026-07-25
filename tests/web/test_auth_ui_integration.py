"""The authenticated workbench end to end: with the demo login active,
a browser import carries the login-derived actor - no actor field
posted, the receipts and the blotter show `demo`, and the ledger's
gateway attestation stands behind it. The same POST without credentials
is challenged. Self-provisioned module slate.
"""

import base64

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore
from glasshouse.seed import ORG, seed_demo
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

PASSWORD = "integration-demo-pw"
CSV = (
    "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end\n"
    "spec-de,T-500,nordkraft,de-power,buy,2,79,2026-07-01T06:00:00Z,2026-07-01T08:00:00Z\n"
)


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
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", PASSWORD)
    return TestClient(create_app())


def test_the_seed_grants_the_demo_actor_every_capability(seeded: sa.Engine) -> None:
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    for predicate in ("MayCaptureTrade", "MayRegisterCurve", "MayValueTrade"):
        principals = {claim.args["actor"] for claim in client.claims_named(predicate)}
        assert "demo" in principals, predicate


def test_an_authenticated_import_carries_the_derived_actor(ui: TestClient) -> None:
    creds = ("demo", PASSWORD)
    with ui as client:
        # No actor field anywhere in the flow: identity is the login's.
        preview = client.post(
            "/ui/imports/preview",
            data={"org": ORG, "kind": "trades"},
            files={"file": ("t.csv", CSV.encode(), "text/csv")},
            auth=creds,
        )
        assert preview.status_code == 200
        assert "badge--admissible" in preview.text

        receipts = client.post(
            "/ui/imports/commit",
            data={
                "org": ORG,
                "kind": "trades",
                "text_b64": base64.b64encode(CSV.encode()).decode(),
            },
            auth=creds,
        )
        assert receipts.status_code == 200
        assert "1 committed" in receipts.text

        blotter = client.get("/trades", params={"org": ORG, "book": "spec-de"}, auth=creds)
        by_trade = {row["trade"]: row for row in blotter.json()}
        assert by_trade["T-500"]["actor"] == "demo"


TAMPER_CSV = (
    "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end\n"
    "spec-de,T-501,nordkraft,de-power,buy,1,77,2026-07-01T03:00:00Z,2026-07-01T04:00:00Z\n"
)


def test_a_tampered_org_field_is_ignored_on_authenticated_writes(ui: TestClient) -> None:
    # Org derives from configuration when authenticated - a hostile form
    # value must not choose the tenancy of a governed write.
    creds = ("demo", PASSWORD)
    with ui as client:
        receipts = client.post(
            "/ui/imports/commit",
            data={
                "org": "evil-corp",
                "kind": "trades",
                "text_b64": base64.b64encode(TAMPER_CSV.encode()).decode(),
            },
            auth=creds,
        )
        assert receipts.status_code == 200
        assert "1 committed" in receipts.text
        # The trade landed in the CONFIGURED org, not the submitted one.
        ours = client.get("/trades", params={"org": ORG, "book": "spec-de"}, auth=creds).json()
        theirs = client.get("/trades", params={"org": "evil-corp"}, auth=creds).json()
    assert "T-501" in {row["trade"] for row in ours}
    assert theirs == []


def test_the_same_post_without_credentials_is_challenged(ui: TestClient) -> None:
    with ui as client:
        response = client.post(
            "/ui/imports/commit",
            data={"org": ORG, "kind": "trades", "text_b64": "Ym9vaw=="},
        )
    assert response.status_code == 401
    assert "www-authenticate" in response.headers
