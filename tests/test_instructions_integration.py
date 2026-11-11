"""Instructions against the real stack, on the seeded book: an open
instruction holds room under its book's position limit, a trade that
would have fitted is refused BY NAME because the room is held, the
refusal's figures say so, cancelling frees the room, open instructions
are never set against the other side, and an overdue instruction goes on
holding until its lapse is recorded. Through the MCP tools, the API and
the Positions screen.

spec-de holds 20 MW in the 12:00 hour, 20 MW in 13:00, and its cap is
25 MW. Each test works in an hour of its own so they do not interfere.
"""

import datetime as dt
import time
from typing import Any

import anyio
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult

from glasshouse.agents import Desk, build_server
from glasshouse.api.app import create_app
from glasshouse.commit import Committed, Rejected, models
from glasshouse.compute.store import engine_url
from glasshouse.seed import AGENT, ORG
from tests.support import BINARY, DB, needs_live_stack, seeded_ledger

pytestmark = needs_live_stack

DAY = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def hour(n: int) -> dt.datetime:
    return DAY + dt.timedelta(hours=n)


def soon(seconds: float = 3600) -> str:
    return (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=seconds)).isoformat()


def instruction(
    name: str, at: int, quantity: str, direction: str = "buy", **more: str
) -> dict[str, str]:
    return {
        "book": "spec-de",
        "instruction": name,
        "market": "de-power",
        "direction": direction,
        "quantity": quantity,
        "limit_price": "80",
        "delivery_start": hour(at).isoformat(),
        "delivery_end": hour(at + 1).isoformat(),
        "valid_until": soon(),
    } | more


def trade(name: str, at: int, quantity: str, direction: str = "buy") -> dict[str, str]:
    return {
        "book": "spec-de",
        "trade": name,
        "counterparty": "nordkraft",
        "market": "de-power",
        "direction": direction,
        "quantity": quantity,
        "price": "80",
        "delivery_start": hour(at).isoformat(),
        "delivery_end": hour(at + 1).isoformat(),
        "trade_date": "2026-06-30",
    }


@pytest.fixture(scope="module")
def desk() -> Any:
    client, engine = seeded_ledger()
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


def test_the_walk_through_place_refuse_cancel_fit(desk: Desk) -> None:
    # 12:00 holds 20 MW of 25. An instruction for 4 MW takes 4 of the 5
    # left. A 2 MW trade would fit on the booked trades alone (22), and
    # is refused because the room is held: by its own rule, with the
    # instructed quantity against the room the trades leave.
    placed = call(desk, "place_instruction", instruction("WALK-1", 12, "4"))
    assert placed["accepted"], placed
    assert placed["actor"] == AGENT

    refused = call(desk, "propose_trade", trade("WALK-T", 12, "2"))
    assert refused["accepted"] is False
    refusal = refused["refusal"]
    assert refusal["rule"] == "room_not_held_by_instructions"  # not hour_within_limit
    assert refusal["compared"] == {"op": "<=", "left": "4 MW", "right": "3 MW"}
    assert {b["var"]: b["value"] for b in refusal["witness"]}["h"] == hour(12).isoformat()
    # Asking first gives the same answer, and records nothing.
    would = call(desk, "explain_trade", trade("WALK-T", 12, "2"))
    assert would["admissible"] is False
    assert would["refusal"]["rule"] == "room_not_held_by_instructions"

    cancelled = call(desk, "cancel_instruction", {"instruction": "WALK-1"})
    assert cancelled["accepted"], cancelled
    fits = call(desk, "propose_trade", trade("WALK-T", 12, "2"))
    assert fits["accepted"], fits

    (listed,) = [row for row in call(desk, "list_instructions") if row["instruction"] == "WALK-1"]
    assert (listed["status"], listed["holding_mw"], listed["proposer"]) == ("cancelled", "0", AGENT)
    assert listed["currency"] == "EUR"
    # A second cancel has nothing to cancel.
    again = call(desk, "cancel_instruction", {"instruction": "WALK-1"})
    assert again["accepted"] is False


def test_a_refusal_by_booked_trades_keeps_its_own_name(desk: Desk) -> None:
    # 13:00 holds 20 MW. Six more is past the cap on trades alone: the
    # older rule refuses, so a caller can tell the two causes apart.
    refused = call(desk, "propose_trade", trade("NAME-T", 13, "6"))
    assert refused["refusal"]["rule"] == "hour_within_limit"
    assert refused["refusal"]["compared"]["left"] == "26 MW"


def test_an_instruction_that_does_not_fit_is_refused(desk: Desk) -> None:
    # 14:00 holds 20 MW: 5 MW of room. Three is held, then three more
    # would make six instructed against five.
    assert call(desk, "place_instruction", instruction("FIT-1", 14, "3"))["accepted"]
    over = call(desk, "place_instruction", instruction("FIT-2", 14, "3"))
    assert over["accepted"] is False
    assert over["refusal"]["rule"] == "instruction_fits_under_limit"
    assert over["refusal"]["compared"] == {"op": "<=", "left": "6 MW", "right": "5 MW"}
    # The same id twice is refused as a duplicate, not as a limit.
    twice = call(desk, "place_instruction", instruction("FIT-1", 15, "1"))
    assert twice["refusal"]["rule"] == "instruction_is_new"


def test_open_instructions_are_never_set_against_the_other_side(desk: Desk) -> None:
    # 15:00 holds 20 MW bought. An open sale of 10 does NOT make room to
    # buy 10 more: it may never fill. A buy of 6 is still past the cap.
    assert call(desk, "place_instruction", instruction("NET-S", 15, "10", "sell"))["accepted"]
    still_over = call(desk, "propose_trade", trade("NET-T", 15, "6"))
    assert still_over["accepted"] is False
    assert still_over["refusal"]["rule"] == "hour_within_limit"
    # And an open buy of 5 fills the bought side while the sale stands.
    assert call(desk, "place_instruction", instruction("NET-B", 15, "5"))["accepted"]
    one_more = call(desk, "place_instruction", instruction("NET-B2", 15, "1"))
    assert one_more["refusal"]["rule"] == "instruction_fits_under_limit"
    # The sold side has its own room: 25 + 20 bought = 45 MW of sales.
    assert call(desk, "place_instruction", instruction("NET-S2", 15, "35", "sell"))["accepted"]
    past_it = call(desk, "place_instruction", instruction("NET-S3", 15, "1", "sell"))
    assert past_it["refusal"]["compared"] == {"op": "<=", "left": "46 MW", "right": "45 MW"}


def test_the_agent_may_not_instruct_where_it_was_not_given(desk: Desk) -> None:
    refused = call(desk, "place_instruction", instruction("HEDGE-1", 2, "1") | {"book": "hedge-de"})
    assert refused["refusal"]["rule"] == "may_instruct"


def test_an_instruction_that_cannot_be_read_is_the_callers_error(desk: Desk) -> None:
    async def go(fields: dict[str, str]) -> CallToolResult:
        async with Client(build_server(lambda: desk)) as client:
            return await client.call_tool("place_instruction", fields)

    for bad in (
        instruction("BAD-1", 16, "lots"),
        instruction("BAD-2", 16, "1", valid_until="tomorrow"),
        instruction("BAD-3", 16, "1", valid_until="2026-12-01T00:00:00"),  # no offset
    ):
        result = anyio.run(go, bad)
        assert result.is_error
    assert not [r for r in call(desk, "list_instructions") if r["instruction"].startswith("BAD")]


def test_an_overdue_instruction_holds_its_room_until_the_lapse_is_recorded(desk: Desk) -> None:
    # 16:00 holds 20 MW. An instruction for 5, valid for two seconds,
    # takes all the room. Once overdue it is listed as such and STILL
    # holds: the limit rule reads no clock. Recording the lapse frees it.
    brief = instruction("LAPSE-1", 16, "5", valid_until=soon(2))
    assert call(desk, "place_instruction", brief)["accepted"]
    lapse = models.MarkInstructionLapsedRequest(org=ORG, instruction="LAPSE-1")
    too_soon = desk.client.submit(lapse, actor=AGENT)
    assert isinstance(too_soon, Rejected)
    assert too_soon.rule == "instruction_has_lapsed"
    time.sleep(2.5)
    (overdue,) = [
        r
        for r in call(desk, "list_instructions", {"open_only": True})
        if r["instruction"] == "LAPSE-1"
    ]
    assert (overdue["status"], overdue["holding_mw"]) == ("overdue", "5")
    held = call(desk, "propose_trade", trade("LAPSE-T", 16, "1"))
    assert held["refusal"]["rule"] == "room_not_held_by_instructions"
    assert isinstance(desk.client.submit(lapse, actor=AGENT), Committed)
    assert call(desk, "propose_trade", trade("LAPSE-T", 16, "1"))["accepted"]
    (ended,) = [r for r in call(desk, "list_instructions") if r["instruction"] == "LAPSE-1"]
    assert (ended["status"], ended["holding_mw"]) == ("lapsed", "0")


def test_the_api_and_the_positions_screen_show_what_is_held(
    desk: Desk, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 17:00 holds 20 MW. Two held leaves three.
    assert call(desk, "place_instruction", instruction("SCREEN-1", 17, "2"))["accepted"]
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.delenv("GLASSHOUSE_MCP_TOKEN", raising=False)
    monkeypatch.delenv("GLASSHOUSE_DEMO_PASSWORD", raising=False)
    window = {"start": hour(17).isoformat(), "end": hour(18).isoformat()}
    with TestClient(create_app()) as client:
        listed = client.get("/instructions", params={"org": ORG, "open_only": "true"}).json()
        page = client.get("/ui/positions", params={"org": ORG, "book": "spec-de"} | window).text
    mine = next(row for row in listed if row["instruction"] == "SCREEN-1")
    assert (mine["status"], mine["holding_mw"], mine["book"]) == ("open", "2", "spec-de")
    assert all(row["holding_mw"] != "0" for row in listed)
    assert "Open instructions" in page
    assert "SCREEN-1" in page
    # In the window shown, 17:00 is the tightest hour: 20 held as
    # position, 2 by the instruction, 25 the cap, 3 left.
    limits = page[page.index("Against the limits") : page.index("Open instructions")]
    row = limits[limits.index("<td>spec-de</td>") :]
    row = row[: row.index("</tr>")]
    assert "2026-07-01 17:00Z" in row
    for figure in ("20", "2", "25", "3"):
        assert f'class="numeric">{figure}<' in row
    assert "not approved by anyone and not connected to fills yet" in page
