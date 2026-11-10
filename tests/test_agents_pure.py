"""The agent's door without a ledger: which tools exist and what they
ask for, that the actor and organisation are never the caller's to
choose, that a trade which cannot be written down honestly is the
caller's error before anything is proposed, and the bearer-token gate on
`/mcp` (shut unless configured, never opened by the demo login, and never
in the way of anything else).
"""

import json
from pathlib import Path
from typing import Any

import anyio
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from glasshouse.agents import REFUSALS_CAVEAT, Desk, build_server
from glasshouse.api.app import create_app
from glasshouse.commit import GlasshouseClient
from glasshouse.config import Settings
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"
TOKEN = "a-token-long-enough"
PASSWORD = "a-password-long-enough"
TOOLS = {
    "list_trades",
    "list_positions",
    "list_markets",
    "position_limits",
    "explain_trade",
    "propose_trade",
    "audit_history",
    "recent_refusals",
}
TRADE = {
    "book": "spec-de",
    "trade": "A-1",
    "counterparty": "nordkraft",
    "market": "de-power",
    "direction": "buy",
    "quantity": "2",
    "price": "80",
    "delivery_start": "2026-07-01T12:00:00+00:00",
    "delivery_end": "2026-07-01T13:00:00+00:00",
    "trade_date": "2026-06-30",
}


def desk(binary: str = "/nonexistent/morpholog") -> Desk:
    return Desk(
        client=GlasshouseClient("model.morph", "postgres:///x", binary=binary),
        engine=sa.create_engine("postgresql+psycopg://127.0.0.1:1/nowhere"),
        actor="agent",
        org="acme-energy",
    )


def call(at: Desk, tool: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
    async def go() -> CallToolResult:
        async with Client(build_server(lambda: at)) as client:
            return await client.call_tool(tool, arguments or {})

    return anyio.run(go)


def said(result: CallToolResult) -> str:
    (block,) = result.content
    assert isinstance(block, TextContent)
    return block.text


def test_the_tools_are_the_eight_and_none_takes_an_actor_or_an_organisation() -> None:
    async def listed() -> dict[str, dict[str, Any]]:
        async with Client(build_server(desk)) as client:
            return {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}

    schemas = anyio.run(listed)
    assert set(schemas) == TOOLS
    for name, schema in schemas.items():
        # Identity and tenancy are the server's, fixed when it was built:
        # no tool can be asked to act as someone else or read elsewhere.
        assert not {"actor_as", "org", "organisation"} & set(schema["properties"]), name
    assert "actor" not in schemas["propose_trade"]["properties"]
    assert "actor" not in schemas["explain_trade"]["properties"]
    assert set(schemas["propose_trade"]["required"]) == set(TRADE)
    # Quantities and prices cross as text, never as a float (law 8).
    assert schemas["propose_trade"]["properties"]["quantity"]["type"] == "string"
    assert schemas["propose_trade"]["properties"]["price"]["type"] == "string"


@pytest.mark.parametrize(
    ("field", "bad", "says"),
    [
        ("quantity", "lots", "exact decimals"),
        ("trade_date", "yesterday", "ISO date"),
        ("delivery_start", "noon", "RFC 3339"),
        # No offset: a naive time is never guessed at (law 9).
        ("delivery_start", "2026-07-01T12:00:00", "could not be read"),
    ],
)
def test_a_trade_that_cannot_be_read_is_an_error_before_anything_is_proposed(
    field: str, bad: str, says: str
) -> None:
    # The binary does not exist: reaching it would be a crash, whose
    # message the SDK withholds. The caller reads its own mistake instead.
    for tool in ("propose_trade", "explain_trade"):
        result = call(desk(), tool, TRADE | {field: bad})
        assert result.is_error
        assert says in said(result)


def test_a_malformed_as_of_or_window_is_the_callers_error() -> None:
    future = call(desk(), "list_trades", {"as_of": "2999-01-01T00:00"})
    assert future.is_error
    assert "in the future" in said(future)
    window = call(desk(), "list_positions", {"start": "last Tuesday"})
    assert window.is_error
    assert "start:" in said(window)


def test_the_limits_are_read_through_the_commit_client_for_this_organisation(
    tmp_path: Path,
) -> None:
    claims = [
        {"predicate": "PositionLimit", "args": {"org": org, "book": book, "cap": cap}}
        | {"asserted_in": "t-1"}
        for org, book, cap in (
            ("acme-energy", "spec-de", "25"),
            ("acme-energy", "hedge-de", "20"),
            ("someone-else", "spec-de", "99"),
        )
    ]
    binary = fake_binary(tmp_path, json.dumps(claims))
    result = call(desk(str(binary)), "position_limits")
    assert result.structured_content == {
        "result": [{"book": "hedge-de", "cap_mw": "20"}, {"book": "spec-de", "cap_mw": "25"}]
    }


def test_the_markets_come_with_their_currency(tmp_path: Path) -> None:
    claims = [
        {"predicate": "MarketCurrency", "args": {"org": org, "market": market, "currency": ccy}}
        | {"asserted_in": "t-1"}
        for org, market, ccy in (
            ("acme-energy", "gb-power", "GBP"),
            ("acme-energy", "de-power", "EUR"),
            ("someone-else", "pl-power", "PLN"),
        )
    ]
    result = call(desk(str(fake_binary(tmp_path, json.dumps(claims)))), "list_markets")
    assert result.structured_content == {
        "result": [
            {"market": "de-power", "currency": "EUR"},
            {"market": "gb-power", "currency": "GBP"},
        ]
    }


def test_no_tool_description_names_a_currency_for_a_price() -> None:
    # A price's currency is its market's, read from the result. A
    # description that said "EUR/MWh" would be a second, wrong source.
    async def described() -> dict[str, str]:
        async with Client(build_server(desk)) as client:
            return {t.name: t.description or "" for t in (await client.list_tools()).tools}

    for name, text in anyio.run(described).items():
        assert "EUR/MWh" not in text, name
        assert "GBP/MWh" not in text, name
    assert "currency of the market" in anyio.run(described)["propose_trade"]


def test_the_refusal_log_always_says_what_it_is(tmp_path: Path) -> None:
    result = call(desk(str(fake_binary(tmp_path, "[]"))), "recent_refusals")
    assert result.structured_content == {"caveat": REFUSALS_CAVEAT, "refusals": []}
    assert "not the audit history" in REFUSALS_CAVEAT
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["inspect", "rejections"]
    assert argv[argv.index("--mentions") + 1] == "acme-energy"


INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _app(monkeypatch: pytest.MonkeyPatch, *, token: str | None, password: str | None) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/nonexistent/morpholog")
    for name, secret in (("GLASSHOUSE_MCP_TOKEN", token), ("GLASSHOUSE_DEMO_PASSWORD", password)):
        if secret is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, secret)
    return TestClient(create_app())


def _knock(client: TestClient, path: str = "/mcp", bearer: str | None = None) -> Any:
    headers = MCP_HEADERS | ({"Authorization": f"Bearer {bearer}"} if bearer else {})
    return client.post(path, json=INITIALIZE, headers=headers, follow_redirects=False)


@pytest.mark.parametrize("password", [None, PASSWORD])
def test_the_door_opens_to_the_token_and_to_nothing_else(
    monkeypatch: pytest.MonkeyPatch, password: str | None
) -> None:
    with _app(monkeypatch, token=TOKEN, password=password) as client:
        unsigned = _knock(client)
        wrong = _knock(client, bearer="not-the-token-at-all")
        # The demo login is another door's key.
        basic = client.post(
            "/mcp", json=INITIALIZE, headers=MCP_HEADERS, auth=("demo", password or "x")
        )
        opened = _knock(client, bearer=TOKEN)
        slashed = _knock(client, "/mcp/", bearer=TOKEN)
        listed = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers=MCP_HEADERS
            | {"Authorization": f"Bearer {TOKEN}", "MCP-Protocol-Version": "2025-06-18"},
        )
    assert unsigned.status_code == wrong.status_code == basic.status_code == 401
    assert unsigned.headers["WWW-Authenticate"].startswith("Bearer")
    # The exact path answers, with no redirect for a proxy to mangle.
    assert opened.status_code == slashed.status_code == 200
    assert opened.json()["result"]["serverInfo"]["name"] == "glasshouse"
    assert {tool["name"] for tool in listed.json()["result"]["tools"]} == TOOLS


def test_with_no_token_configured_there_is_no_door(monkeypatch: pytest.MonkeyPatch) -> None:
    with _app(monkeypatch, token=None, password=None) as client:
        assert _knock(client, bearer=TOKEN).status_code == 404
    # Behind the demo login it is an unknown path like any other: the
    # login's challenge, and nothing learnt about what exists.
    with _app(monkeypatch, token=None, password=PASSWORD) as client:
        shut = _knock(client, bearer=TOKEN)
    assert shut.status_code == 401
    assert shut.headers["WWW-Authenticate"].startswith("Basic")


def test_the_token_opens_nothing_but_the_door(monkeypatch: pytest.MonkeyPatch) -> None:
    with _app(monkeypatch, token=TOKEN, password=PASSWORD) as client:
        bearer = {"Authorization": f"Bearer {TOKEN}"}
        assert client.get("/ui", headers=bearer).status_code == 401
        assert client.get("/trades?org=acme-energy", headers=bearer).status_code == 401
        assert client.get("/mcp/anything-else", headers=bearer).status_code == 401
        assert client.get("/healthz").status_code == 200


def test_a_trivial_token_fails_at_boot() -> None:
    with pytest.raises(ValidationError, match="at least 12"):
        Settings(mcp_token="short")
    assert Settings().mcp_token is None
    # Left empty in a hosting dashboard: not set, door shut, no crash.
    assert Settings(mcp_token="  ").mcp_token is None
    assert Settings().mcp_actor == "agent"
