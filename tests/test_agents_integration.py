"""The agent's door against the real stack: the seeded book read through
the tools, a trade the trading agent `algo` books under its mandate found
in the audit history under its own actor, the position limit refusing the
next one with its rule and figures as data, the assistant `agent` (no
mandate) booking nothing, the refusal found in the best-effort log, and
both ways in (the CLI's stdio server, and `/mcp` behind its token, which
must also say the model the agent runs on).
"""

import datetime as dt
import sys
from decimal import Decimal
from typing import Any

import anyio
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters
from mcp.types import CallToolResult

from glasshouse.agents import Desk, build_server
from glasshouse.api.app import create_app
from glasshouse.compute.store import engine_url
from glasshouse.seed import AGENT, ALGO, ALGO_MANDATE, ALGO_MODEL, ORG
from tests.support import BINARY, DB, needs_live_stack, seeded_ledger

pytestmark = needs_live_stack

# What the mandated act's limit gate reads, in the act's order (v0.0.20).
RELATED = [
    "org",
    "mandate",
    "trade",
    "direction",
    "quantity",
    "delivery_start",
    "delivery_end",
    "trade_date",
]
HOUR = dt.datetime(2026, 7, 1, 12, tzinfo=dt.UTC)  # spec-de holds 20 MW here, cap 25
TOKEN = "a-token-long-enough"


def trade(name: str, *, quantity: str = "2", direction: str = "buy") -> dict[str, str]:
    """One trade under the trading agent's mandate, in the noon hour."""
    return {
        "mandate": ALGO_MANDATE,
        "trade": name,
        "counterparty": "nordkraft",
        "direction": direction,
        "quantity": quantity,
        "price": "80",
        "delivery_start": HOUR.isoformat(),
        "delivery_end": (HOUR + dt.timedelta(hours=1)).isoformat(),
    }


@pytest.fixture(scope="module")
def desk() -> Any:
    """A freshly seeded ledger and the agent's desk on it. Module-scoped:
    the tests below read one story, each from its own trades."""
    client, engine = seeded_ledger()
    engine.dispose()
    reads = sa.create_engine(engine_url(DB))
    yield Desk(client=client, engine=reads, actor=ALGO, org=ORG, model=ALGO_MODEL)
    reads.dispose()


def call(at: Desk, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    async def go() -> CallToolResult:
        async with Client(build_server(lambda: at), raise_exceptions=True) as client:
            return await client.call_tool(tool, arguments or {})

    result = anyio.run(go)
    assert not result.is_error, result.content
    structured = result.structured_content
    assert structured is not None
    return structured.get("result", structured)


def net_at_noon(at: Desk, **extra: str) -> str | None:
    window = {"start": HOUR.isoformat(), "end": (HOUR + dt.timedelta(hours=1)).isoformat()}
    rows = call(at, "list_positions", {"book": "spec-de"} | window | extra)
    return rows[0]["net_mw"] if rows else None


def test_the_book_is_read_through_the_tools_now_and_as_it_stood(desk: Desk) -> None:
    assert {"T-001", "T-005"} <= {row["trade"] for row in call(desk, "list_trades")}
    assert {limit["book"]: limit["cap_mw"] for limit in call(desk, "position_limits")} == {
        "spec-de": "25",
        "hedge-de": "20",
    }
    assert net_at_noon(desk) is not None
    # Before the first capture the book was empty, and the tool says so.
    tail = desk.client.audit_named()
    first = next(i for i, row in enumerate(tail) if row.transformation_name == "capture_trade")
    before = tail[first - 1].ordering
    assert before is not None
    assert call(desk, "list_trades", {"as_of": before.ordered_at.isoformat()}) == []


def test_the_limit_refuses_the_agent_with_the_rule_the_hour_and_the_figures(desk: Desk) -> None:
    # The agent buys 2 MW in one hour until the cap says no. However many
    # this module's other tests have already added, the refusal is the
    # first proposal that would pass 25 MW, and it names that sum.
    held = net_at_noon(desk)
    assert held is not None
    accepted = []
    for attempt in range(1, 10):
        outcome = call(desk, "trade_under_mandate", trade(f"LIMIT-{attempt}"))
        if not outcome["accepted"]:
            break
        accepted.append(outcome)
    else:
        pytest.fail("nine trades were accepted and the cap never refused")
    assert all(one["actor"] == ALGO and one["transition_id"] for one in accepted)
    # The agent's own reads see its accepted trades at once.
    assert Decimal(net_at_noon(desk) or "0") == Decimal(held) + 2 * len(accepted)

    refusal = outcome["refusal"]
    assert outcome["transition_id"] is None
    assert refusal["kind"] == "gate"
    assert refusal["rule"] == "hour_within_limit"
    would_be = Decimal(held) + 2 * (len(accepted) + 1)
    assert refusal["compared"] == {"op": "<=", "left": f"{would_be} MW", "right": "25 MW"}
    witness = {binding["var"]: binding["value"] for binding in refusal["witness"]}
    assert witness["h"] == HOUR.isoformat()
    assert witness["cap"] == "25 MW"
    assert outcome["summary"].startswith("hour_within_limit:")
    # The arguments the refusing gate reads (v0.0.20), in the act's order.
    assert outcome["related_parameters"] == RELATED
    # Nothing of the refused trade exists.
    assert f"LIMIT-{len(accepted) + 1}" not in {row["trade"] for row in call(desk, "list_trades")}


def test_an_accepted_trade_is_in_the_audit_history_under_the_agents_actor(desk: Desk) -> None:
    outcome = call(desk, "trade_under_mandate", trade("AUDIT-1", quantity="0.5"))
    assert outcome["accepted"], outcome
    mine = call(desk, "audit_history", {"actor": ALGO, "limit": 50})
    entry = next(e for e in mine if e["transition_id"] == outcome["transition_id"])
    assert entry["actor"] == ALGO
    assert entry["transformation"] == "trade_under_mandate"
    assert {entry["actor"] for entry in mine} == {ALGO}
    # And on the blotter, attributed the same way.
    row = next(r for r in call(desk, "list_trades") if r["trade"] == "AUDIT-1")
    assert row["actor"] == ALGO


def test_an_assistant_with_no_mandate_books_nothing(desk: Desk) -> None:
    # The seeded `agent` holds no right and no mandate: a plain capture is
    # refused for want of the right, and another agent's mandate is not
    # its own.
    assistant = Desk(client=desk.client, engine=desk.engine, actor=AGENT, org=ORG, model=ALGO_MODEL)
    plain = trade("HEDGE-1", quantity="1")
    capture = {k: v for k, v in plain.items() if k != "mandate"} | {
        "book": "hedge-de",
        "market": "de-power",
        "trade_date": "2026-06-30",
    }
    outcome = call(assistant, "propose_trade", capture)
    assert outcome["accepted"] is False
    assert outcome["refusal"]["rule"] == "may_capture_trade"
    (missing,) = outcome["refusal"]["directly_missing_claims"]
    assert missing["rendered"] == f"MayCaptureTrade({AGENT}, {ORG}, hedge-de)"
    borrowed = call(assistant, "trade_under_mandate", plain)
    assert borrowed["refusal"]["rule"] == "actor_holds_the_mandate"
    # Nor can the trading agent use the plain capture, whatever it holds.
    assert call(desk, "propose_trade", capture)["refusal"]["rule"] == "may_capture_trade"


def test_asking_first_records_nothing(desk: Desk) -> None:
    rows_before = len(desk.client.audit_named())
    refusals_before = len(desk.client.rejections(limit=500))
    would = call(desk, "explain_trade_under_mandate", trade("ASK-1", quantity="500"))
    assert would["admissible"] is False
    assert would["refusal"]["rule"] == "order_within_mandate"
    fine = call(
        desk, "explain_trade_under_mandate", trade("ASK-2", quantity="0.1", direction="sell")
    )
    assert fine == {"admissible": True, "actor": ALGO, "summary": None, "refusal": None}
    assert len(desk.client.audit_named()) == rows_before
    assert len(desk.client.rejections(limit=500)) == refusals_before


def test_a_refusal_is_in_the_best_effort_log_under_the_agents_actor(desk: Desk) -> None:
    refused = call(desk, "trade_under_mandate", trade("LOG-1", quantity="400"))
    assert refused["accepted"] is False
    log = call(desk, "recent_refusals", {"actor": ALGO})
    assert "not the audit history" in log["caveat"]
    newest = log["refusals"][0]
    assert newest["actor"] == ALGO
    assert newest["rule"] == "order_within_mandate"
    assert newest["transformation"] == "trade_under_mandate"
    assert log["limit"] == 20
    # The figures the rule compared are in the recorded row.
    assert newest["compared"] == {"op": "<=", "left": "400 MW", "right": "2 MW"}
    assert newest["summary"].startswith("order_within_mandate: ")
    # And it is not in the audit history: that holds what was admitted.
    history = call(desk, "audit_history", {"actor": ALGO, "limit": 500})
    assert not any(
        claim["args"].get("trade") == "LOG-1" for entry in history for claim in entry["asserted"]
    )


def test_the_cli_serves_the_same_tools_over_stdio(desk: Desk) -> None:
    async def go() -> tuple[set[str], Any]:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "glasshouse.cli", "mcp", "--actor", ALGO, "--database-url", DB],
            env={"GLASSHOUSE_MORPHOLOG_BIN": str(BINARY)},
        )
        async with Client(server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            limits = await client.call_tool("position_limits", {})
            return names, limits.structured_content

    names, limits = anyio.run(go)
    assert {"propose_trade", "trade_under_mandate"} <= names
    assert len(names) == 23
    assert {"book": "spec-de", "cap_mw": "25"} in limits["result"]


def test_over_http_the_token_is_the_agent(desk: Desk, monkeypatch: pytest.MonkeyPatch) -> None:
    # The hosted way in, whole: a JSON-RPC call to /mcp with the bearer
    # token proposes as the configured actor, and the audit history says
    # so. No actor travels in the request.
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_MCP_TOKEN", TOKEN)
    monkeypatch.setenv("GLASSHOUSE_MCP_ACTOR", ALGO)
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", "a-password-long-enough")
    call_tool = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "trade_under_mandate", "arguments": trade("HTTP-1", quantity="0.25")},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    bearer = headers | {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app()) as client:
        refused = client.post("/mcp", json=call_tool, headers=headers)
        # A door told no model names none: the mandate admits nothing.
        unstated = client.post("/mcp", json=call_tool, headers=bearer)
    monkeypatch.setenv("GLASSHOUSE_MCP_MODELS", f'{{"{ALGO}": "{ALGO_MODEL}"}}')
    with TestClient(create_app()) as client:
        answered = client.post("/mcp", json=call_tool, headers=bearer)
    assert refused.status_code == 401
    assert unstated.json()["result"]["structuredContent"]["refusal"]["rule"] == (
        "model_is_approved"
    )
    assert answered.status_code == 200, answered.text
    outcome = answered.json()["result"]["structuredContent"]
    assert outcome["accepted"] is True, outcome
    assert outcome["actor"] == ALGO
    row = next(r for r in desk.client.audit_named() if r.transition_id == outcome["transition_id"])
    assert row.actor == ALGO


def test_the_same_refusals_are_on_the_api_and_the_audit_screen(
    desk: Desk, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = call(desk, "trade_under_mandate", trade("SCREEN-1", quantity="300"))
    assert refused["accepted"] is False
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.delenv("GLASSHOUSE_MCP_TOKEN", raising=False)
    monkeypatch.delenv("GLASSHOUSE_DEMO_PASSWORD", raising=False)
    with TestClient(create_app()) as client:
        api = client.get("/refusals", params={"org": ORG, "actor": ALGO}).json()
        page = client.get("/ui/audit", params={"org": ORG}).text
        older = client.get("/ui/audit", params={"org": ORG, "offset": 50}).text
    assert api["refusals"][0]["rule"] == "order_within_mandate"
    assert api["refusals"][0]["actor"] == ALGO
    assert "not the audit history" in api["caveat"]
    assert "Refused lately" in page
    assert "This order is larger than the mandate&#39;s largest order." in page
    assert "keeps the latest 20" in page
    # The refused trade is nowhere in the record of changes below it.
    assert "SCREEN-1" not in page[page.index("<h2>Changes") :]
    # A short recent list on the first page, not repeated on every page.
    assert "Refused lately" not in older
