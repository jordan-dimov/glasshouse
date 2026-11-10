"""The agent's door against the real stack: the seeded book read through
the tools, a trade accepted and found in the audit history under the
agent's own actor, the position limit refusing the next one with its rule
and figures as data, a book the agent holds no authority in refusing it
by the same rules as anyone, the refusal found in the best-effort log,
and both ways in (the CLI's stdio server, and `/mcp` behind its token).
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
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore, engine_url
from glasshouse.seed import AGENT, ORG, seed_demo
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

HOUR = dt.datetime(2026, 7, 1, 12, tzinfo=dt.UTC)  # spec-de holds 20 MW here, cap 25
TOKEN = "a-token-long-enough"


def trade(name: str, *, book: str = "spec-de", quantity: str = "2") -> dict[str, str]:
    return {
        "book": book,
        "trade": name,
        "counterparty": "nordkraft",
        "market": "de-power",
        "direction": "buy",
        "quantity": quantity,
        "price": "80",
        "delivery_start": HOUR.isoformat(),
        "delivery_end": (HOUR + dt.timedelta(hours=1)).isoformat(),
        "trade_date": "2026-06-30",
    }


@pytest.fixture(scope="module")
def desk() -> Any:
    """A freshly seeded ledger and the agent's desk on it. Module-scoped:
    the tests below read one story, each from its own trades."""
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    seed_demo(client, CurveStore(engine), engine)
    engine.dispose()
    reads = sa.create_engine(engine_url(DB))
    yield Desk(client=client, engine=reads, actor=AGENT, org=ORG)
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
        outcome = call(desk, "propose_trade", trade(f"LIMIT-{attempt}"))
        if not outcome["accepted"]:
            break
        accepted.append(outcome)
    else:
        pytest.fail("nine trades were accepted and the cap never refused")
    assert all(one["actor"] == AGENT and one["transition_id"] for one in accepted)
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
    # Nothing of the refused trade exists.
    assert f"LIMIT-{len(accepted) + 1}" not in {row["trade"] for row in call(desk, "list_trades")}


def test_an_accepted_trade_is_in_the_audit_history_under_the_agents_actor(desk: Desk) -> None:
    outcome = call(desk, "propose_trade", trade("AUDIT-1", quantity="0.5"))
    assert outcome["accepted"], outcome
    mine = call(desk, "audit_history", {"actor": AGENT, "limit": 50})
    entry = next(e for e in mine if e["transition_id"] == outcome["transition_id"])
    assert entry["actor"] == AGENT
    assert entry["transformation"] == "capture_trade"
    assert {entry["actor"] for entry in mine} == {AGENT}
    # And on the blotter, attributed the same way.
    row = next(r for r in call(desk, "list_trades") if r["trade"] == "AUDIT-1")
    assert row["actor"] == AGENT


def test_the_agent_has_no_authority_a_trader_was_not_given(desk: Desk) -> None:
    # The seed grants the agent the speculative book only. The hedge book
    # refuses it by the capture rule itself, naming the missing authority.
    outcome = call(desk, "propose_trade", trade("HEDGE-1", book="hedge-de", quantity="1"))
    assert outcome["accepted"] is False
    assert outcome["refusal"]["rule"] == "may_capture_trade"
    (missing,) = outcome["refusal"]["directly_missing_claims"]
    assert missing["rendered"] == f"MayCaptureTrade({AGENT}, {ORG}, hedge-de)"


def test_asking_first_records_nothing(desk: Desk) -> None:
    rows_before = len(desk.client.audit_named())
    refusals_before = len(desk.client.rejections(limit=500))
    would = call(desk, "explain_trade", trade("ASK-1", quantity="500"))
    assert would["admissible"] is False
    assert would["refusal"]["rule"] == "hour_within_limit"
    fine = call(desk, "explain_trade", trade("ASK-2", quantity="0.1"))
    assert fine == {"admissible": True, "actor": AGENT, "summary": None, "refusal": None}
    assert len(desk.client.audit_named()) == rows_before
    assert len(desk.client.rejections(limit=500)) == refusals_before


def test_a_refusal_is_in_the_best_effort_log_under_the_agents_actor(desk: Desk) -> None:
    refused = call(desk, "propose_trade", trade("LOG-1", quantity="400"))
    assert refused["accepted"] is False
    log = call(desk, "recent_refusals", {"actor": AGENT})
    assert "not the audit history" in log["caveat"]
    newest = log["refusals"][0]
    assert newest["actor"] == AGENT
    assert newest["rule"] == "hour_within_limit"
    assert newest["transformation"] == "capture_trade"
    # And it is not in the audit history: that holds what was admitted.
    history = call(desk, "audit_history", {"actor": AGENT, "limit": 500})
    assert not any(
        claim["args"].get("trade") == "LOG-1" for entry in history for claim in entry["asserted"]
    )


def test_the_cli_serves_the_same_tools_over_stdio(desk: Desk) -> None:
    async def go() -> tuple[set[str], Any]:
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "glasshouse.cli", "mcp", "--actor", AGENT, "--database-url", DB],
            env={"GLASSHOUSE_MORPHOLOG_BIN": str(BINARY)},
        )
        async with Client(server) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            limits = await client.call_tool("position_limits", {})
            return names, limits.structured_content

    names, limits = anyio.run(go)
    assert "propose_trade" in names
    assert len(names) == 8
    assert {"book": "spec-de", "cap_mw": "25"} in limits["result"]


def test_over_http_the_token_is_the_agent(desk: Desk, monkeypatch: pytest.MonkeyPatch) -> None:
    # The hosted way in, whole: a JSON-RPC call to /mcp with the bearer
    # token proposes as the configured actor, and the audit history says
    # so. No actor travels in the request.
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_MCP_TOKEN", TOKEN)
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", "a-password-long-enough")
    call_tool = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "propose_trade", "arguments": trade("HTTP-1", quantity="0.25")},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }
    with TestClient(create_app()) as client:
        refused = client.post("/mcp", json=call_tool, headers=headers)
        answered = client.post(
            "/mcp", json=call_tool, headers=headers | {"Authorization": f"Bearer {TOKEN}"}
        )
    assert refused.status_code == 401
    assert answered.status_code == 200, answered.text
    outcome = answered.json()["result"]["structuredContent"]
    assert outcome["accepted"] is True, outcome
    assert outcome["actor"] == AGENT
    row = next(r for r in desk.client.audit_named() if r.transition_id == outcome["transition_id"])
    assert row.actor == AGENT
