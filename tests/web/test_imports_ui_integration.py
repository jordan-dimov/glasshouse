"""The Imports workbench against the real stack: the first governed
writes from a browser. Preview commits nothing; commit produces
receipts with transition ids and catches the projections up; a
re-commit of the same bytes comes back all rejected (the double-submit
story proven, not tokenised). Self-provisioned module slate.
"""

import base64
import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import correct_curve_version
from glasshouse.compute.store import CurveStore
from glasshouse.seed import CURVE_V1, CURVE_V2, ORG, seed_demo
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

        # Different business dates are not a correction comparison: the
        # diff refuses them by name rather than juxtaposing two
        # unrelated timelines.
        incomparable = client.get(
            "/curves/diff",
            params={"org": ORG, "market": "de-power", "base": CURVE_V1, "compare": "crv-web-1"},
        )
        assert incomparable.status_code == 422
        assert "different business dates" in incomparable.json()["detail"]
        page = client.get(
            "/ui/curves",
            params={"org": ORG, "market": "de-power", "base": CURVE_V1, "compare": "crv-web-1"},
        )
        assert page.status_code == 422
        assert "Not comparable" in page.text


def test_a_refused_curve_commit_does_not_consume_the_version_id(
    ui: TestClient, seeded: sa.Engine
) -> None:
    # Registering a version for the already-official July 1 date is a
    # lawful rejection (the honest move is a correction). The rejection
    # must give the version id back: the payload stored before the
    # proposal is discarded again, so a LATER legitimate correction can
    # use the same id with different (corrected) bytes.
    poison_csv = (
        "market,as_of,version,period_start,price\n"
        "de-power,2026-07-01,crv-poison,2026-07-01T00:00:00Z,60\n"
        "de-power,2026-07-01,crv-poison,2026-07-01T01:00:00Z,61\n"
    )
    with ui as client:
        preview = _post(client, "/ui/imports/preview", poison_csv, kind="curves", actor="carol")
        assert "badge--refused" in preview.text  # the workbench warned
        receipts = _post(
            client,
            "/ui/imports/commit",
            "",
            kind="curves",
            actor="carol",
            text_b64=base64.b64encode(poison_csv.encode()).decode(),
        )
        assert "1 rejected" in receipts.text  # committed anyway: lawful refusal

    # The proper correction path now reuses the id with corrected bytes.
    day = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    corrected = HourlyCurve(
        tuple((day + dt.timedelta(hours=h), Decimal(str(90 + h))) for h in range(24))
    )
    client_direct = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    outcome = correct_curve_version(
        client_direct,
        CurveStore(seeded),
        actor="carol",
        org=ORG,
        market="de-power",
        as_of=day.date(),
        prior_version=CURVE_V2,
        new_version="crv-poison",
        curve=corrected,
    )
    assert isinstance(outcome, Committed)
