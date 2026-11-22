"""An agent trades only under a mandate a person approved, inside every
bound of it, against the real stack: the seeded book, where `agent` is a
declared agent with no mandate and `algo` holds the approved mandate
`M-ALGO` (spec-de, both sides, 2 MW an order, 20 MW in all, 50 to 150,
delivery on 1 July 2026) and an approved record of its model.

Each test takes its own mandate or its own hour, so the module can share
one seeded ledger in any order.
"""

from __future__ import annotations

import datetime as dt
import itertools
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import anyio
import pytest
import sqlalchemy as sa
from mcp import Client
from mcp.types import CallToolResult
from typer.testing import CliRunner

from glasshouse import cli
from glasshouse.agents import Desk, build_server
from glasshouse.api import mandates
from glasshouse.commit import GlasshouseClient, models
from glasshouse.compute.store import engine_url
from glasshouse.imports.report import wire_text
from glasshouse.seed import ALGO, ALGO_MANDATE, ALGO_MODEL, ORG
from tests.support import DB, committed, needs_live_stack, refused, seeded_ledger

pytestmark = needs_live_stack

MARKET = "de-power"
BOOK = "spec-de"
DAY = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
WINTER = dt.datetime(2026, 12, 1, tzinfo=dt.UTC)
_ids = itertools.count(1)


@pytest.fixture(scope="module")
def ledger() -> GlasshouseClient:
    client, engine = seeded_ledger()
    engine.dispose()
    return client


def trade(
    mandate: str,
    *,
    hour: dt.datetime,
    quantity: str = "1",
    price: str = "95",
    direction: str = "buy",
    model: str = ALGO_MODEL,
    hours: int = 1,
) -> models.TradeUnderMandateRequest:
    n = next(_ids)
    return models.TradeUnderMandateRequest(
        org=ORG,
        mandate=mandate,
        trade=f"MT-{n}",
        counterparty="epex",
        version=f"MT-{n}/v1",
        direction=direction,
        quantity=Decimal(quantity),
        price=Decimal(price),
        delivery_start=hour,
        delivery_end=hour + dt.timedelta(hours=hours),
        trade_date=dt.date(2026, 11, 22),
        model=model,
    )


def mandate(
    name: str,
    *,
    agent: str = ALGO,
    side: str = "both",
    max_order: str = "2",
    max_volume: str = "6",
    valid_for: dt.timedelta = dt.timedelta(days=30),
) -> models.ProposeMandateRequest:
    return models.ProposeMandateRequest(
        org=ORG,
        mandate=name,
        agent=agent,
        book=BOOK,
        market=MARKET,
        side=side,
        max_order=Decimal(max_order),
        max_volume=Decimal(max_volume),
        price_floor=Decimal("50"),
        price_ceiling=Decimal("150"),
        deliver_from=WINTER,
        deliver_until=WINTER + dt.timedelta(days=31),
        valid_until=dt.datetime.now(dt.UTC) + valid_for,
    )


def given(ledger: GlasshouseClient, request: models.ProposeMandateRequest) -> str:
    """A mandate proposed by risk and approved by the demo login."""
    committed(ledger, request, "risk-manager")
    committed(ledger, models.ApproveMandateRequest(org=ORG, mandate=request.mandate), "demo")
    return request.mandate


def test_an_agent_books_nothing_through_the_acts_people_use(ledger: GlasshouseClient) -> None:
    # Even holding the right a trader holds, a declared agent is refused
    # by name: within limits is not authorised.
    committed(
        ledger,
        models.GrantCaptureAuthorityRequest(principal="agent", org=ORG, book=BOOK),
        "bootstrap",
    )
    capture = models.CaptureTradeRequest(
        org=ORG,
        book=BOOK,
        trade="AG-1",
        counterparty="epex",
        market=MARKET,
        direction="buy",
        version="AG-1/v1",
        quantity=Decimal("1"),
        price=Decimal("95"),
        delivery_start=WINTER,
        delivery_end=WINTER + dt.timedelta(hours=1),
        trade_date=dt.date(2026, 11, 22),
    )
    assert refused(ledger, capture, "agent").rule == "actor_is_not_an_agent"
    # The same trade by a person in the same book is admitted.
    committed(ledger, capture, "alice")


def test_the_trading_agent_trades_freely_inside_its_mandate(ledger: GlasshouseClient) -> None:
    # Many trades, no person in the loop: each is its own decision.
    for k in range(4):
        committed(ledger, trade(ALGO_MANDATE, hour=DAY + dt.timedelta(hours=k)), ALGO)
    (row,) = [m for m in mandates.list_mandates(ledger, org=ORG) if m.mandate == ALGO_MANDATE]
    assert row.standing == "open"
    assert row.model_approved
    assert row.trades >= 4


def test_every_bound_of_the_mandate_is_a_gate(ledger: GlasshouseClient) -> None:
    name = given(ledger, mandate("M-BOUNDS", side="buy"))
    hour = WINTER + dt.timedelta(hours=1)
    cases = {
        "side_within_mandate": trade(name, hour=hour, direction="sell"),
        "order_within_mandate": trade(name, hour=hour, quantity="2.5"),
        "price_not_below_mandate": trade(name, hour=hour, price="49.99"),
        "price_not_above_mandate": trade(name, hour=hour, price="150.01"),
        "delivery_not_before_mandate": trade(name, hour=WINTER - dt.timedelta(hours=1)),
        "delivery_not_after_mandate": trade(name, hour=WINTER + dt.timedelta(days=31)),
    }
    for rule, request in cases.items():
        assert refused(ledger, request, ALGO).rule == rule, rule
    # Someone else's mandate is not yours.
    assert refused(ledger, trade(name, hour=hour), "agent").rule == "actor_holds_the_mandate"


def test_splitting_an_order_gains_nothing(ledger: GlasshouseClient) -> None:
    # 6 MW in all, 2 an order: three orders, and a fourth of any size is
    # refused, with the ledger's own running total in the figures.
    name = given(ledger, mandate("M-SPLIT"))
    for k in range(3):
        committed(ledger, trade(name, hour=WINTER + dt.timedelta(hours=10 + k), quantity="2"), ALGO)
    fourth = refused(
        ledger, trade(name, hour=WINTER + dt.timedelta(hours=13), quantity="0.1"), ALGO
    )
    assert fourth.rule == "volume_within_mandate"
    assert fourth.compared is not None
    assert (
        wire_text(fourth.compared.left),
        fourth.compared.op,
        wire_text(fourth.compared.right),
    ) == (
        "6.1 MW",
        "<=",
        "6 MW",
    )


def test_a_suspended_mandate_refuses_the_next_trade(ledger: GlasshouseClient) -> None:
    name = given(ledger, mandate("M-STOP"))
    committed(ledger, trade(name, hour=WINTER + dt.timedelta(hours=20)), ALGO)
    committed(ledger, models.SuspendMandateRequest(org=ORG, mandate=name), "risk-manager")
    after = refused(ledger, trade(name, hour=WINTER + dt.timedelta(hours=21)), ALGO)
    assert after.rule == "mandate_not_suspended"
    # Final: it cannot be approved back into force.
    again = models.ApproveMandateRequest(org=ORG, mandate=name)
    assert refused(ledger, again, "demo").rule == "mandate_not_yet_approved"


def test_an_agent_may_suspend_its_own_mandate_and_no_one_elses(ledger: GlasshouseClient) -> None:
    mine = given(ledger, mandate("M-SELF"))
    theirs = given(ledger, mandate("M-THEIRS"))
    suspend = models.SuspendMandateRequest(org=ORG, mandate=theirs)
    assert refused(ledger, suspend, "agent").rule == "may_suspend"
    committed(ledger, models.SuspendMandateRequest(org=ORG, mandate=mine), ALGO)


def test_an_unapproved_or_expired_mandate_admits_nothing(ledger: GlasshouseClient) -> None:
    pending = mandate("M-PENDING")
    committed(ledger, pending, "risk-manager")
    hour = WINTER + dt.timedelta(hours=30)
    assert refused(ledger, trade("M-PENDING", hour=hour), ALGO).rule == "mandate_is_approved"
    # The proposer is not the second pair of eyes.
    approve = models.ApproveMandateRequest(org=ORG, mandate="M-PENDING")
    assert refused(ledger, approve, "risk-manager").rule == "approver_is_not_proposer"
    short = given(ledger, mandate("M-SHORT", valid_for=dt.timedelta(seconds=3)))
    time.sleep(4)
    assert refused(ledger, trade(short, hour=hour), ALGO).rule == "mandate_has_not_expired"


def test_an_agent_cannot_give_widen_renew_or_approve_a_mandate(ledger: GlasshouseClient) -> None:
    # It holds no right to, and cannot be given one.
    wider = mandate("M-WIDER", max_order="50", max_volume="500")
    assert refused(ledger, wider, ALGO).rule == "may_mandate"
    grant = models.GrantMandateAuthorityRequest(principal=ALGO, org=ORG)
    assert refused(ledger, grant, "bootstrap").rule == "principal_is_not_an_agent"
    committed(ledger, wider, "risk-manager")
    approve = models.ApproveMandateRequest(org=ORG, mandate="M-WIDER")
    assert refused(ledger, approve, ALGO).rule == "may_mandate"
    # Nor can someone who may grant be declared an agent afterwards.
    declare = models.DeclareAgentRequest(org=ORG, agent="bootstrap")
    assert refused(ledger, declare, "bootstrap").rule == "agent_cannot_grant"


def test_a_new_model_stops_the_mandates_until_someone_else_approves_it(
    ledger: GlasshouseClient,
) -> None:
    name = given(ledger, mandate("M-MODEL"))
    hour = WINTER + dt.timedelta(hours=40)
    # The door says another model than the approved one: refused.
    other = trade(name, hour=hour, model="another-model-2")
    assert refused(ledger, other, ALGO).rule == "model_is_approved"
    # A person records the new model: the old approval goes with it.
    record = models.RecordAgentModelRequest(org=ORG, agent=ALGO, model="another-model-2")
    committed(ledger, record, "risk-manager")
    stale = trade(name, hour=hour)
    assert refused(ledger, stale, ALGO).rule == "model_is_approved"
    approve = models.ApproveAgentModelRequest(org=ORG, agent=ALGO, model="another-model-2")
    assert refused(ledger, approve, "risk-manager").rule == "approver_is_not_recorder"
    assert refused(ledger, approve, ALGO).rule == "may_mandate"
    committed(ledger, approve, "demo")
    committed(ledger, trade(name, hour=hour, model="another-model-2"), ALGO)
    # Put the seed's model back for the other tests.
    back = models.RecordAgentModelRequest(org=ORG, agent=ALGO, model=ALGO_MODEL)
    committed(ledger, back, "risk-manager")
    committed(
        ledger, models.ApproveAgentModelRequest(org=ORG, agent=ALGO, model=ALGO_MODEL), "demo"
    )


def test_the_book_cap_still_binds_a_mandated_trade(ledger: GlasshouseClient) -> None:
    # spec-de holds 20 MW at 12:00 on 1 July against a cap of 25: the
    # mandate allows 2 MW an order, the cap stops the third.
    noon = DAY + dt.timedelta(hours=12)
    outcomes = [
        ledger.submit(trade(ALGO_MANDATE, hour=noon, quantity="2"), actor=ALGO) for _ in range(3)
    ]
    assert getattr(outcomes[-1], "rule", None) == "hour_within_limit"


def _tool(at: Desk, name: str, arguments: Mapping[str, object] | None = None) -> Any:
    async def go() -> CallToolResult:
        async with Client(build_server(lambda: at), raise_exceptions=True) as client:
            return await client.call_tool(name, dict(arguments or {}))

    result = anyio.run(go)
    assert not result.is_error, result.content
    structured = result.structured_content
    assert structured is not None
    return structured.get("result", structured)


def test_people_give_approve_and_suspend_over_mcp_and_the_agent_sees_it(
    ledger: GlasshouseClient,
) -> None:
    reads = sa.create_engine(engine_url(DB))
    risk = Desk(client=ledger, engine=reads, actor="risk-manager", org=ORG)
    demo = Desk(client=ledger, engine=reads, actor="demo", org=ORG)
    algo = Desk(client=ledger, engine=reads, actor=ALGO, org=ORG, model=ALGO_MODEL)
    terms = {
        "mandate": "M-MCP",
        "agent": ALGO,
        "book": BOOK,
        "market": MARKET,
        "side": "sell",
        "max_order": "1",
        "max_volume": "3",
        "price_floor": "60",
        "price_ceiling": "120",
        "deliver_from": WINTER.isoformat(),
        "deliver_until": (WINTER + dt.timedelta(days=31)).isoformat(),
        "valid_until": (dt.datetime.now(dt.UTC) + dt.timedelta(days=5)).isoformat(),
    }
    assert _tool(algo, "propose_mandate", terms)["refusal"]["rule"] == "may_mandate"
    assert _tool(risk, "propose_mandate", terms)["accepted"]
    assert _tool(demo, "approve_mandate", {"mandate": "M-MCP"})["accepted"]
    sell = {
        "mandate": "M-MCP",
        "trade": "MCP-1",
        "counterparty": "epex",
        "direction": "sell",
        "quantity": "1",
        "price": "90",
        "delivery_start": (WINTER + dt.timedelta(hours=50)).isoformat(),
        "delivery_end": (WINTER + dt.timedelta(hours=51)).isoformat(),
    }
    assert _tool(algo, "explain_trade_under_mandate", sell)["admissible"]
    assert _tool(algo, "trade_under_mandate", sell)["accepted"]
    (mine,) = [
        m for m in _tool(algo, "list_mandates", {"mine_only": True}) if m["mandate"] == "M-MCP"
    ]
    assert (mine["used_mw"], mine["trades"], mine["standing"], mine["model_approved"]) == (
        "1",
        1,
        "open",
        True,
    )
    assert _tool(risk, "suspend_mandate", {"mandate": "M-MCP"})["accepted"]
    (after,) = [m for m in _tool(risk, "list_mandates") if m["mandate"] == "M-MCP"]
    assert (after["standing"], after["suspended_by"]) == ("suspended", "risk-manager")
    # The model record over MCP: the agent cannot write its own, and the
    # recorder cannot approve it.
    record = {"agent": ALGO, "model": ALGO_MODEL}
    assert _tool(algo, "record_agent_model", record)["refusal"]["rule"] == "may_mandate"
    assert _tool(risk, "record_agent_model", record)["accepted"]
    assert _tool(risk, "approve_agent_model", record)["refusal"]["rule"] == (
        "approver_is_not_recorder"
    )
    assert _tool(demo, "approve_agent_model", record)["accepted"]

    # A request the tools cannot read is the caller's error, not a refusal.
    async def bad() -> CallToolResult:
        async with Client(build_server(lambda: algo)) as client:
            return await client.call_tool("trade_under_mandate", sell | {"quantity": "lots"})

    assert anyio.run(bad).is_error
    reads.dispose()


def test_the_command_line_gives_approves_lists_and_suspends(ledger: GlasshouseClient) -> None:
    runner = CliRunner()
    base = ["--org", ORG, "--database-url", DB]

    def run(*argv: str) -> str:
        result = runner.invoke(cli.app, [*argv, *base])
        assert result.exit_code == 0, result.output
        return result.output

    assert "is an agent" in run("declare-agent", "--agent", "cli-bot", "--actor", "bootstrap")
    run(
        "propose-mandate",
        "--mandate",
        "M-CLI",
        "--agent",
        "cli-bot",
        "--book",
        BOOK,
        "--market",
        MARKET,
        "--side",
        "both",
        "--max-order",
        "1",
        "--max-volume",
        "4",
        "--price-floor",
        "10",
        "--price-ceiling",
        "200",
        "--deliver-from",
        WINTER.isoformat(),
        "--deliver-until",
        (WINTER + dt.timedelta(days=31)).isoformat(),
        "--valid-until",
        (dt.datetime.now(dt.UTC) + dt.timedelta(days=3)).isoformat(),
        "--actor",
        "risk-manager",
    )
    assert "approved by demo" in run("approve-mandate", "--mandate", "M-CLI", "--actor", "demo")
    assert "awaiting approval" in run(
        "record-agent-model", "--agent", "cli-bot", "--model", "m-9", "--actor", "risk-manager"
    )
    assert "approved by demo" in run(
        "approve-agent-model", "--agent", "cli-bot", "--model", "m-9", "--actor", "demo"
    )
    listed = run("mandates")
    line = next(row for row in listed.splitlines() if row.startswith("M-CLI"))
    assert "used 0 of 4 MW" in line
    assert "open" in line
    assert "m-9 (approved)" in line
    assert "suspended by risk-manager" in run(
        "suspend-mandate", "--mandate", "M-CLI", "--actor", "risk-manager"
    )
    # A refusal prints the ledger's own words and exits 1.
    again = runner.invoke(
        cli.app, ["approve-mandate", "--mandate", "M-CLI", "--actor", "demo", *base]
    )
    assert again.exit_code == 1
    # A time with no offset is the typist's mistake: exit 2, nothing proposed.
    naive = runner.invoke(
        cli.app,
        [
            "propose-mandate",
            "--mandate",
            "M-NAIVE",
            "--agent",
            "cli-bot",
            "--book",
            BOOK,
            "--market",
            MARKET,
            "--side",
            "both",
            "--max-order",
            "1",
            "--max-volume",
            "4",
            "--price-floor",
            "10",
            "--price-ceiling",
            "200",
            "--deliver-from",
            "2026-12-01T00:00:00",
            "--deliver-until",
            "2027-01-01T00:00:00+00:00",
            "--valid-until",
            "2026-12-31T00:00:00+00:00",
            "--actor",
            "risk-manager",
            *base,
        ],
    )
    assert naive.exit_code == 2, naive.output
