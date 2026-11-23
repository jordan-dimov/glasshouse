"""A trading agent meets the position limit.

A plain MCP client, no model and no API key, acting as the seeded trading
agent `algo`: it reads the cap on a book, reads the book's position in one
delivery hour, then buys 2 MW in that hour under its mandate (`M-ALGO`)
again and again, with no person in the loop, until the rules refuse, and
prints the refusal.

Against a local Glasshouse (the default; it starts the server itself):

    uv run python examples/limit_refusal.py

Against a hosted one, with a token its operator gave you for `algo` (and
`algo`'s model set in its GLASSHOUSE_MCP_MODELS):

    GLASSHOUSE_MCP_TOKEN=... uv run python examples/limit_refusal.py --url https://host/mcp
"""

import argparse
import datetime as dt
import json
import os
import sys
from decimal import Decimal

import anyio
import httpx2
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

BOOK = "spec-de"
AGENT = "algo"
MANDATE = "M-ALGO"
MODEL = "example-strategy-1"  # the model the seed's approved record names
HOUR = dt.datetime(2026, 7, 1, 12, tzinfo=dt.UTC)  # an hour the demo book already holds
STEP_MW = "2"


def connect(url: str | None) -> Client:
    if url is None:
        # Local: the server is a child process speaking on its stdin and
        # stdout. It reads GLASSHOUSE_DATABASE_URL like every command.
        return Client(
            StdioServerParameters(
                command=sys.executable,
                args=["-m", "glasshouse.cli", "mcp", "--actor", AGENT, "--model", MODEL],
                env=dict(os.environ),
            )
        )
    token = os.environ["GLASSHOUSE_MCP_TOKEN"]
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    return Client(streamable_http_client(url, http_client=http))


async def run(url: str | None) -> int:
    run_id = dt.datetime.now(dt.UTC).strftime("%H%M%S")
    hour_end = HOUR + dt.timedelta(hours=1)
    async with connect(url) as agent:
        limits = (await agent.call_tool("position_limits", {})).structured_content
        cap = next(Decimal(limit["cap_mw"]) for limit in limits["result"] if limit["book"] == BOOK)
        held = (
            await agent.call_tool(
                "list_positions",
                {"book": BOOK, "start": HOUR.isoformat(), "end": hour_end.isoformat()},
            )
        ).structured_content["result"]
        net = Decimal(held[0]["net_mw"]) if held else Decimal(0)
        print(f"{BOOK}, hour {HOUR:%Y-%m-%d %H:%M} UTC: holding {net} MW, cap {cap} MW")

        for attempt in range(1, 51):
            outcome = (
                await agent.call_tool(
                    "trade_under_mandate",
                    {
                        "mandate": MANDATE,
                        "trade": f"AGENT-{run_id}-{attempt}",
                        "counterparty": "nordkraft",
                        "direction": "buy",
                        "quantity": STEP_MW,
                        "price": "80",
                        "delivery_start": HOUR.isoformat(),
                        "delivery_end": hour_end.isoformat(),
                    },
                )
            ).structured_content
            if outcome["accepted"]:
                print(f"  buy {STEP_MW} MW: accepted as {outcome['actor']}")
                continue
            refusal = outcome["refusal"]
            print(f"  buy {STEP_MW} MW: REFUSED")
            print(f"\nrule:     {refusal.get('rule')}")
            if refusal.get("compared"):
                c = refusal["compared"]
                print(f"figures:  {c['left']} {c['op']} {c['right']} does not hold")
            hours = [b["value"] for b in refusal.get("witness", []) if b["var"] == "h"]
            if hours:
                print(f"hour:     {hours[0]}")
            print(f"in words: {outcome['summary']}")
            print("\nThe whole refusal, as the agent receives it:")
            print(json.dumps(refusal, indent=2))
            return 0
    print("fifty trades were accepted and nothing refused: is the book capped?")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="a hosted Glasshouse's /mcp address (default: run locally)")
    sys.exit(anyio.run(run, parser.parse_args().url))
