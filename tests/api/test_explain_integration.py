"""`/explain` against the real binary: an authorised actor's capture is
admissible, and an unauthorised one is refused with the exact missing
capability claim and its candidate supplier - the same-snapshot "why"
the workbench needs, travelling over HTTP.

Same gating and provisioning contract as the other integration legs.
"""

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def _capture_args(actor_trade: str) -> dict[str, object]:
    return models.CaptureTradeRequest(
        org=ORG,
        book=BOOK,
        trade=actor_trade,
        counterparty="stadtwerk-x",
        market=MARKET,
        direction="buy",
        quantity=Decimal("10"),
        price=Decimal("86.25"),
        delivery_start=T0,
        delivery_end=T0 + dt.timedelta(hours=3),
    ).to_args_named()


@pytest.fixture(scope="module")
def granted() -> None:
    """Alice may capture; bob may not - the difference the gate tests."""
    engine = provision()
    engine.dispose()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    assert isinstance(
        client.submit(
            models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
            actor="bootstrap",
        ),
        Committed,
    )


@pytest.fixture
def api(granted: None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    return TestClient(create_app())


def test_authorised_capture_is_admissible(api: TestClient) -> None:
    with api as client:
        response = client.post(
            "/explain",
            json={"transformation": "capture_trade", "args": _capture_args("T-A")},
            headers={"X-Actor": "alice"},
        )
    assert response.status_code == 200
    assert response.json() == {"admissible": True, "rejection": None}


def test_unauthorised_capture_names_the_missing_claim(api: TestClient) -> None:
    with api as client:
        response = client.post(
            "/explain",
            json={"transformation": "capture_trade", "args": _capture_args("T-B")},
            headers={"X-Actor": "bob"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["admissible"] is False
    assert body["rejection"]["kind"] == "gate"
    predicates = {m["predicate"] for m in body["rejection"]["directly_missing_claims"]}
    assert "MayCaptureTrade" in predicates
