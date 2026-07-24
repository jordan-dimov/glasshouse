"""The Imports workbench against the real stack: the first governed
writes from a browser. Preview commits nothing; commit produces
receipts with transition ids and catches the projections up; a
re-commit of the same bytes comes back all rejected (the double-submit
story proven, not tokenised). Self-provisioned module slate.
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

TRADES_CSV = (
    "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end\n"
    "spec-de,T-100,stadtwerk-x,de-power,buy,5,80,2026-07-01T08:00:00Z,2026-07-01T10:00:00Z\n"
    "spec-de,T-101,nordkraft,sell,not-a-market,x,y,z,w\n"
)

CURVES_CSV = (
    "market,as_of,version,period_start,price\n"
    "de-power,2026-07-02,crv-web-1,2026-07-02T00:00:00Z,75\n"
    "de-power,2026-07-02,crv-web-1,2026-07-02T01:00:00Z,76\n"
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
    return TestClient(create_app())


def _post(client: TestClient, path: str, csv: str, **form: str):  # type: ignore[no-untyped-def]
    data = {"org": ORG, "kind": "trades", "actor": "alice", **form}
    if path.endswith("preview"):
        return client.post(
            path, data=data, files={"file": ("upload.csv", csv.encode(), "text/csv")}
        )
    return client.post(path, data=data)


def test_the_workbench_previews_commits_and_survives_a_resubmit(ui: TestClient) -> None:
    with ui as client:
        preview = _post(client, "/ui/imports/preview", TRADES_CSV)
        assert preview.status_code == 200
        assert "Nothing has been committed" in preview.text
        assert "badge--admissible" in preview.text  # T-100 would commit
        assert "badge--quarantined" in preview.text  # the malformed row
        # Preview really committed nothing: the blotter shows no T-100.
        assert "T-100" not in client.get("/ui/blotter", params={"org": ORG}).text

        text_b64 = base64.b64encode(TRADES_CSV.encode()).decode()
        receipts = _post(client, "/ui/imports/commit", "", text_b64=text_b64)
        assert receipts.status_code == 200
        assert "1 committed" in receipts.text
        assert "badge--committed" in receipts.text
        assert "badge--quarantined" in receipts.text
        assert "transition(s) applied" in receipts.text
        # The inline catch-up made the write visible on the blotter.
        assert "T-100" in client.get("/ui/blotter", params={"org": ORG}).text

        # The double-submit story, proven: the same bytes again come
        # back as a lawful rejection, not a duplicate trade.
        again = _post(client, "/ui/imports/commit", "", text_b64=text_b64)
        assert again.status_code == 200
        assert "0 committed" in again.text
        assert "1 rejected" in again.text


def test_a_curves_upload_reaches_the_curves_screen(ui: TestClient) -> None:
    with ui as client:
        preview = _post(client, "/ui/imports/preview", CURVES_CSV, kind="curves", actor="carol")
        assert preview.status_code == 200
        assert "badge--admissible" in preview.text
        receipts = _post(
            client,
            "/ui/imports/commit",
            "",
            kind="curves",
            actor="carol",
            text_b64=base64.b64encode(CURVES_CSV.encode()).decode(),
        )
        assert receipts.status_code == 200
        assert "1 committed" in receipts.text
        # The new version is on the Curves screen, payload verified live.
        curves_page = client.get("/ui/curves", params={"org": ORG, "market": "de-power"})
        assert "crv-web-1" in curves_page.text
