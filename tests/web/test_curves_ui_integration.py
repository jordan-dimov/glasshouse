"""The Curves screen against the real stack, fed by the seeded Tuesday
correction: v2 official, v1 superseded, both payloads verified live,
the diff showing exactly the four revised hours, and each version's
marks. Self-provisioned module slate.
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore
from glasshouse.seed import CURVE_V1, CURVE_V2, ORG, seed_demo
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


def test_versions_show_status_lineage_and_live_payload_verdicts(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui/curves", params={"org": ORG})
    assert response.status_code == 200
    # v2 is the official pointer, v1 stays on the record as superseded.
    assert "badge--official" in response.text
    assert "badge--superseded" in response.text
    assert f"{CURVE_V2}</span> supersedes <span" in response.text
    # Both payloads re-hash to their admitted hashes, live.
    assert response.text.count(">ok<") >= 2
    assert "mismatch" not in response.text


def test_the_diff_shows_exactly_the_revised_hours_and_each_versions_marks(
    ui: TestClient,
) -> None:
    with ui as client:
        response = client.get(
            "/ui/curves",
            params={"org": ORG, "market": "de-power", "base": CURVE_V1, "compare": CURVE_V2},
        )
    assert response.status_code == 200
    # Hours 08-11 were revised up by exactly 3 EUR; every other hour is
    # identical, so the delta column carries exactly four 3s.
    assert response.text.count('class="numeric">3</td>') == 4
    # Each version carries all six marks (every trade re-marked).
    assert response.text.count("<h2>Marks struck on") == 2
    with ui as client:
        json_diff = client.get(
            "/curves/diff",
            params={"org": ORG, "market": "de-power", "base": CURVE_V1, "compare": CURVE_V2},
        ).json()
    deltas = [p["delta"] for p in json_diff["periods"] if p["delta"] != "0"]
    assert deltas == ["3", "3", "3", "3"]  # exact strings on the wire
    assert len(json_diff["base_marks"]) == 6
    assert len(json_diff["compare_marks"]) == 6


def test_an_unknown_version_is_a_named_404(ui: TestClient) -> None:
    with ui as client:
        page = client.get(
            "/ui/curves",
            params={"org": ORG, "market": "de-power", "base": CURVE_V1, "compare": "no-such"},
        )
        json_response = client.get(
            "/curves/diff",
            params={"org": ORG, "market": "de-power", "base": CURVE_V1, "compare": "no-such"},
        )
    assert page.status_code == 404
    assert "no-such" in page.text
    assert json_response.status_code == 404
    assert "no-such" in json_response.json()["detail"]


def test_the_json_twin_lists_versions_with_exact_hashes(ui: TestClient) -> None:
    with ui as client:
        versions = client.get("/curves", params={"org": ORG, "market": "de-power"}).json()
        markets = client.get("/markets", params={"org": ORG}).json()
    assert markets == ["de-power"]
    by_version = {v["version"]: v for v in versions}
    assert by_version[CURVE_V2]["status"] == "official"
    assert by_version[CURVE_V1]["status"] == "superseded"
    assert by_version[CURVE_V1]["superseded_by"] == CURVE_V2
    assert by_version[CURVE_V2]["supersedes"] == CURVE_V1
    assert all(v["payload"] == "ok" for v in versions)
    assert all(v["payload_hash"].startswith("sha256:") for v in versions)
    assert by_version[CURVE_V1]["mark_count"] == 6
    assert sorted(by_version[CURVE_V1]["books"]) == ["hedge-de", "spec-de"]
