"""The read endpoints against the real stack: seed the needle (grants,
capture, an official curve, a mark), project it, then read it back over
HTTP. Money and quantity come back as exact strings; every row carries
its evidence trail (the transition id, and the actor where stored).

Same gating and provisioning contract as the other integration legs.
"""

import datetime as dt
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import correct_curve_version, register_curve_version, value_trade
from glasshouse.compute.store import CurveStore
from glasshouse.projections import rebuild
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
AS_OF = dt.date(2026, 6, 8)
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def seeded() -> sa.Engine:
    """One captured trade, marked against the official curve and then
    re-marked after a correction, projected - the read side the API
    serves, including the valuation history a correction leaves behind.
    Module-scoped: the reads are pure, so the slate is shared."""
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    store = CurveStore(engine)
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=BOOK),
    ):
        assert isinstance(client.submit(grant, actor="bootstrap"), Committed)
    assert isinstance(
        client.submit(
            models.CaptureTradeRequest(
                org=ORG,
                book=BOOK,
                trade="T-001",
                counterparty="stadtwerk-x",
                market=MARKET,
                direction="buy",
                quantity=Decimal("10"),
                price=Decimal("86.25"),
                delivery_start=T0,
                delivery_end=T0 + dt.timedelta(hours=3),
            ),
            actor="alice",
        ),
        Committed,
    )
    curve = HourlyCurve(
        tuple(
            (T0 + dt.timedelta(hours=i), p)
            for i, p in enumerate(map(Decimal, ["90", "88", "86.25"]))
        )
    )
    assert isinstance(
        register_curve_version(
            client,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=AS_OF,
            version="crv-v1",
            curve=curve,
        ),
        Committed,
    )
    assert isinstance(
        value_trade(client, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001"),
        Committed,
    )
    # The Tuesday correction: crv-v2 supersedes crv-v1, and the trade is
    # re-marked against it. Both marks survive (law 2), so the read side
    # carries valuation history and a well-defined "current" mark.
    corrected = HourlyCurve(
        tuple(
            (T0 + dt.timedelta(hours=i), p)
            for i, p in enumerate(map(Decimal, ["89", "87", "86.25"]))
        )
    )
    assert isinstance(
        correct_curve_version(
            client,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=AS_OF,
            prior_version="crv-v1",
            new_version="crv-v2",
            curve=corrected,
        ),
        Committed,
    )
    assert isinstance(
        value_trade(client, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001"),
        Committed,
    )
    rebuild(client, engine)
    return engine


@pytest.fixture
def api(seeded: sa.Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    return TestClient(create_app())


def test_trades_reads_the_blotter(api: TestClient) -> None:
    with api as client:
        (trade,) = client.get("/trades", params={"org": ORG}).json()
    assert trade["trade"] == "T-001"
    assert trade["direction"] == "buy"
    assert trade["quantity"] == "10"  # exact, a string, not a JSON float
    assert trade["price"] == "86.25"
    assert trade["actor"] == "alice"  # the evidence trail rode the read
    assert trade["transition_id"]


def test_trades_are_scoped_to_the_org(api: TestClient) -> None:
    with api as client:
        assert client.get("/trades", params={"org": "someone-else"}).json() == []
        # The optional book narrowing on top of the org.
        assert len(client.get("/trades", params={"org": ORG, "book": BOOK}).json()) == 1
        assert client.get("/trades", params={"org": ORG, "book": "no-such-book"}).json() == []


def test_positions_are_net_mw_per_hour(api: TestClient) -> None:
    with api as client:
        positions = client.get("/positions", params={"org": ORG}).json()
    assert [p["net_mw"] for p in positions] == ["10", "10", "10"]
    assert {p["market"] for p in positions} == {MARKET}
    with api as client:
        # The book and market narrowings on top of the org.
        assert len(client.get("/positions", params={"org": ORG, "book": BOOK}).json()) == 3
        assert client.get("/positions", params={"org": ORG, "market": "fr-power"}).json() == []
        # The period window narrows to the first delivery hour.
        windowed = client.get(
            "/positions",
            params={
                "org": ORG,
                "start": T0.isoformat(),
                "end": (T0 + dt.timedelta(hours=1)).isoformat(),
            },
        ).json()
    assert len(windowed) == 1


def test_valuations_pin_the_curve_version(api: TestClient) -> None:
    # The default read is valuation history: both marks survive the
    # correction, each pinned to the curve version it was struck against.
    with api as client:
        marks = client.get("/valuations", params={"org": ORG, "trade": "T-001"}).json()
    assert [(m["curve_version"], m["mtm"]) for m in marks] == [
        ("crv-v1", "55.00"),  # exact EUR, a string
        ("crv-v2", "35.00"),
    ]
    assert {m["actor"] for m in marks} == {"risk-engine"}


def test_latest_valuation_is_one_current_mark_per_trade(api: TestClient) -> None:
    # `latest=true` is the only surface that may be summed as current
    # P&L: exactly the newest mark per trade, never history.
    with api as client:
        (mark,) = client.get("/valuations", params={"org": ORG, "latest": "true"}).json()
    assert mark["curve_version"] == "crv-v2"
    assert mark["mtm"] == "35.00"


def test_orgs_lists_projected_organisations(api: TestClient) -> None:
    with api as client:
        assert client.get("/orgs").json() == [ORG]


def test_overview_summarises_one_coherent_snapshot(api: TestClient) -> None:
    with api as client:
        summary = client.get("/overview", params={"org": ORG}).json()
    assert summary["org"] == ORG
    assert summary["books"] == [{"book": BOOK, "trade_count": 1}]
    # The current total is the latest mark per trade - summing history
    # (55.00 + 35.00) would double-count the corrected curve.
    assert summary["valuation"]["trade_count"] == 1
    assert summary["valuation"]["total_mtm"] == "35.00"
    assert summary["valuation"]["valued_at"] is not None
    assert summary["projection"]["transition_id"]  # the cursor is set after rebuild


def test_overview_for_an_unknown_org_is_empty_not_an_error(api: TestClient) -> None:
    with api as client:
        summary = client.get("/overview", params={"org": "someone-else"}).json()
    assert summary["books"] == []
    assert summary["valuation"] == {"trade_count": 0, "valued_at": None, "total_mtm": None}


def test_trades_market_filter_and_pagination(api: TestClient) -> None:
    with api as client:
        assert len(client.get("/trades", params={"org": ORG, "market": MARKET}).json()) == 1
        assert client.get("/trades", params={"org": ORG, "market": "fr-power"}).json() == []
        assert len(client.get("/trades", params={"org": ORG, "limit": 1}).json()) == 1
        # One trade exists, so the second page is honestly empty.
        assert client.get("/trades", params={"org": ORG, "limit": 1, "offset": 1}).json() == []
