"""Two currencies through every read surface, on the seeded book: the
German market in euros and the British one in pounds. Every price and
mark returned names its currency, the totals are one per currency, and
the German figures are what they were before a second market existed.
"""

import datetime as dt
from typing import Any

import anyio
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from mcp import Client

from glasshouse.agents import Desk, build_server
from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.seed import AGENT, CURVE_V1, CURVE_V2, GB_BOOK, GB_CURVE, GB_MARKET, ORG, seed_demo
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def seeded() -> GlasshouseClient:
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    seed_demo(client, CurveStore(engine), engine)
    engine.dispose()
    return client


@pytest.fixture
def app(seeded: GlasshouseClient, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    return TestClient(create_app())


def get(client: TestClient, path: str, **params: str) -> Any:
    response = client.get(path, params={"org": ORG, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_every_price_and_mark_names_its_currency(app: TestClient) -> None:
    with app as client:
        trades = get(client, "/trades")
        marks = get(client, "/valuations", latest="true")
        terms = get(client, "/trades/G-001/terms")
        then = get(client, "/trades", as_of=dt.datetime.now(dt.UTC).isoformat())
    by_market = {(t["market"], t["currency"]) for t in trades}
    assert by_market == {("de-power", "EUR"), (GB_MARKET, "GBP")}
    assert {t["trade"]: t["currency"] for t in trades if t["book"] == GB_BOOK} == {
        "G-001": "GBP",
        "G-002": "GBP",
    }
    # A mark states its own currency, and it is its trade's.
    traded_in = {t["trade"]: t["currency"] for t in trades}
    assert marks
    assert all(mark["currency"] == traded_in[mark["trade"]] for mark in marks)
    assert [(v["version"], v["price"], v["currency"]) for v in terms] == [("G-001/v1", "66", "GBP")]
    # The past carries it the same way as the present.
    assert then == trades


def test_totals_are_one_per_currency_and_the_german_figures_stand(app: TestClient) -> None:
    with app as client:
        overview = get(client, "/overview")
        page = client.get("/ui", params={"org": ORG}).text
    totals = {v["currency"]: (v["trade_count"], v["total_mtm"]) for v in overview["valuations"]}
    # 287.5 EUR is the German total from before this change, to the cent.
    assert totals == {"EUR": (6, "287.5"), "GBP": (2, "140")}
    assert [v["currency"] for v in overview["valuations"]] == ["EUR", "GBP"]
    assert "6 trades valued in EUR" in page
    assert "2 trades valued in GBP" in page
    # No figure anywhere adds the two.
    assert "427.5" not in page


def test_curves_say_what_their_prices_are_in(app: TestClient) -> None:
    with app as client:
        british = get(client, "/curves", market=GB_MARKET)
        german = get(client, "/curves", market="de-power")
        diff = get(client, "/curves/diff", market="de-power", base=CURVE_V1, compare=CURVE_V2)
        page = client.get("/ui/curves", params={"org": ORG, "market": GB_MARKET}).text
    assert [(v["version"], v["currency"]) for v in british] == [(GB_CURVE, "GBP")]
    assert {v["currency"] for v in german} == {"EUR"}
    assert diff["currency"] == "EUR"
    assert {mark["currency"] for mark in diff["base_marks"] + diff["compare_marks"]} == {"EUR"}
    assert "Prices in GBP per MWh" in page


def test_the_screens_show_the_currency_beside_the_number(app: TestClient) -> None:
    with app as client:
        blotter = client.get("/ui/blotter", params={"org": ORG}).text
        positions = client.get("/ui/positions", params={"org": ORG}).text
    # No heading claims a currency for a column that holds two.
    for page in (blotter, positions):
        assert "(EUR" not in page
        assert "<td>GBP</td>" in page
        assert "<td>EUR</td>" in page
    assert "Price (per MWh)" in blotter


def test_an_agent_is_told_the_currency_of_every_price(seeded: GlasshouseClient) -> None:
    engine = sa.create_engine(engine_url(DB))
    desk = Desk(client=seeded, engine=engine, actor=AGENT, org=ORG)

    async def go() -> tuple[Any, Any]:
        async with Client(build_server(lambda: desk), raise_exceptions=True) as client:
            markets = await client.call_tool("list_markets", {})
            trades = await client.call_tool("list_trades", {"market": GB_MARKET})
            return markets.structured_content, trades.structured_content

    try:
        markets, trades = anyio.run(go)
    finally:
        engine.dispose()
    assert markets == {
        "result": [
            {"market": "de-power", "currency": "EUR"},
            {"market": GB_MARKET, "currency": "GBP"},
        ]
    }
    assert [(t["trade"], t["price"], t["currency"]) for t in trades["result"]] == [
        ("G-001", "66", "GBP"),
        ("G-002", "80", "GBP"),
    ]
