"""The three screens against the real stack, fed by the seed dataset:
every number an exact string, negatives visible, filters narrowing,
the fragment a fragment, tenancy explicit even when empty.

Self-provisioned module slate, same contract as every integration leg.
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore
from glasshouse.seed import seed_demo
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG = "acme-energy"


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


def test_the_picker_lists_the_seeded_org(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui")
    assert response.status_code == 200
    assert ORG in response.text


def test_overview_renders_the_seeded_day(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui", params={"org": ORG})
    assert response.status_code == 200
    assert "spec-de" in response.text
    assert "hedge-de" in response.text
    assert "3 trades" in response.text  # spec-de carries T-001, T-002, T-005
    assert "System health" in response.text


def test_blotter_renders_exact_terms_and_filters_narrow(ui: TestClient) -> None:
    with ui as client:
        full = client.get("/ui/blotter", params={"org": ORG})
        by_book = client.get("/ui/blotter", params={"org": ORG, "book": "hedge-de"})
        by_market = client.get("/ui/blotter", params={"org": ORG, "market": "fr-power"})
    assert full.status_code == 200
    assert "T-001" in full.text
    assert "7.5" in full.text  # T-002's quantity, the exact string
    assert "Next 50" not in full.text  # six trades: one page, honestly
    assert "T-003" in by_book.text
    assert "T-001" not in by_book.text
    assert "No trades match" in by_market.text


def test_the_blotter_fragment_swaps_in_place(ui: TestClient) -> None:
    with ui as client:
        fragment = client.get("/ui/blotter", params={"org": ORG}, headers={"HX-Request": "true"})
    assert fragment.status_code == 200
    assert "T-001" in fragment.text
    assert "<html" not in fragment.text


def test_positions_show_the_short_hour_and_current_marks(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui/positions", params={"org": ORG})
    assert response.status_code == 200
    # T-006 alone in hedge-de makes a net-short hour; T-001, struck above
    # the curve, carries a negative mark - both rendered loud.
    assert 'class="numeric neg">-15<' in response.text
    assert 'class="numeric neg">-220<' in response.text
    assert response.text.count("crv-2026-07-01") == 6  # one current mark per trade
    with ui as client:
        windowed = client.get(
            "/ui/positions",
            params={"org": ORG, "start": "2026-07-01T09:00", "end": "2026-07-01T10:00"},
        )
    assert windowed.status_code == 200
    assert "2026-07-01 09:00Z" in windowed.text
    assert "2026-07-01 10:00Z" not in windowed.text


def test_a_second_org_is_empty_but_visible(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui/blotter", params={"org": "someone-else"})
    assert response.status_code == 200
    assert "No trades match" in response.text
    # The requested org joins the selector: tenancy stays explicit even
    # on an empty screen.
    assert '<option value="someone-else" selected>' in response.text
    assert f'<option value="{ORG}" ' in response.text
