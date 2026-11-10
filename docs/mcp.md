# Agents: Glasshouse over MCP

Glasshouse can be used by an AI agent. It speaks the [Model Context Protocol](https://modelcontextprotocol.io) (MCP), the common way for an agent to be given tools. An agent connected to Glasshouse can read the book, ask whether a trade would be accepted, and propose one.

An agent gets no special treatment. Its trade is a proposal like anyone's, and the same rules accept or refuse it. It acts under its own name, and what it does is recorded under that name.

## The tools

| Tool | What it does |
|---|---|
| `list_trades` | Every trade in the book, each with its price and the currency of that price. Takes `as_of` to see the book at a past time. |
| `list_positions` | Net MW per delivery hour, per book. Takes `as_of` too. |
| `list_markets` | The markets, each with the currency it is priced in. |
| `position_limits` | The cap on each book's position in any one hour. |
| `explain_trade` | Asks whether a trade would be accepted. Records nothing. |
| `propose_trade` | Proposes one trade. The answer is accepted or refused. |
| `audit_history` | The accepted changes, newest first, with who made each. |
| `recent_refusals` | Recent refused proposals, with the rule and the figures. |

Quantities are MW. A price is per MWh in the currency of its market: euros for German power, pounds for British. Every price and valuation a tool returns comes with a `currency` field, and `list_markets` gives each market's currency. Numbers are sent as text such as `"2.5"`, never as floating point numbers. Times are UTC and must carry an offset, such as `2026-07-01T12:00:00+00:00`.

No tool takes an actor or an organisation. Both are fixed when the server starts, so an agent cannot act as someone else or read another organisation's book.

## Connect locally

Follow [Run it locally](../README.md#run-it-locally) first, so there is a seeded book. Then the server is one command. It talks over standard input and output, which is how MCP clients start a local server:

```bash
uv run python -m glasshouse.cli mcp --actor agent
```

You rarely run that by hand. You give the command to your MCP client and it starts the server itself. For Claude Code:

```bash
claude mcp add glasshouse -- uv run --directory /path/to/glasshouse python -m glasshouse.cli mcp --actor agent
```

Other clients take the same command and arguments in their own settings file.

The seeded book has an actor called `agent`, allowed to trade in the `spec-de` book and nowhere else. `--actor` can name any actor, but an actor the book has granted nothing will be refused everything.

## Connect to a hosted Glasshouse

A hosted Glasshouse serves the same tools at `/mcp`, but only if its operator has set `GLASSHOUSE_MCP_TOKEN`. Without the token the address does not exist. With it, a client must send the token as a bearer header:

```bash
claude mcp add --transport http glasshouse https://your-host/mcp --header "Authorization: Bearer YOUR-TOKEN"
```

Whoever holds the token acts as one actor (`agent`, or the name in `GLASSHOUSE_MCP_ACTOR`). The token is a shared secret, not a sign-in: there is no OAuth here, so clients that only connect through an OAuth sign-in screen cannot use it yet.

## Walk through: the agent meets the limit

The seeded `spec-de` book may hold at most 25 MW in any one delivery hour. At 12:00 on 1 July 2026 it already holds 20 MW. The example script buys 2 MW in that hour, again and again, until the rules say no. It needs no AI model and no API key.

```bash
uv run python examples/limit_refusal.py
```

```
spec-de, hour 2026-07-01 12:00 UTC: holding 20 MW, cap 25 MW
  buy 2 MW: accepted as agent
  buy 2 MW: accepted as agent
  buy 2 MW: REFUSED

rule:     hour_within_limit
figures:  26 MW <= 25 MW does not hold
hour:     2026-07-01T12:00:00+00:00
```

The first two trades take the hour to 22 and then 24 MW. The third would make it 26, so it is refused. A refusal is a normal answer, not an error. The agent receives it as data it can reason about:

```json
{
  "accepted": false,
  "actor": "agent",
  "summary": "hour_within_limit: 26 MW <= 25 MW does not hold (h = 2026-07-01T12:00:00+00:00)",
  "refusal": {
    "kind": "gate",
    "rule": "hour_within_limit",
    "compared": { "op": "<=", "left": "26 MW", "right": "25 MW" },
    "witness": [
      { "var": "h", "value": "2026-07-01T12:00:00+00:00" },
      { "var": "cap", "value": "25 MW" }
    ]
  }
}
```

(Shortened here. The full answer also carries the rule as written, and every value it was reading.)

- `rule` names the rule in [the rule file](../src/glasshouse/commit/glasshouse.morph) that refused.
- `compared` holds the two figures and the comparison that failed.
- `witness` holds the values the rule was reading, including the hour (`h`).

An agent that calls `position_limits` and `list_positions` first can work out that only 5 MW of room is left, and `explain_trade` will confirm it without proposing anything. Try asking the agent to buy in the `hedge-de` book as well: it is refused there by a different rule, `may_capture_trade`, because nobody gave it that authority.

## What is recorded

An accepted trade is in the audit history under the agent's name, like any other change. `audit_history` with `actor` set to `agent` shows everything it did.

A refusal is not in the audit history. The audit history holds only what was accepted. Refusals are kept in a separate log that is best-effort: each one is written once, after the proposal is undone, and that write can occasionally fail. `recent_refusals` reads that log and says so in every answer. Treat it as a good lead, not as proof.

## What an agent cannot do

- Write to the database any way other than a proposal.
- Trade in a book it has not been given.
- Pass a position limit.
- Choose which actor or organisation it is.

Each of these is enforced by the rules in the ledger, not by the MCP server. The server has no way round them.

## Limits of this first version

- One trade type: a fixed-price power trade. No amendments yet.
- No tools yet for curves, valuations or the list of organisations.
- A trade takes the currency of its market. You do not state one, and a trade in a market that has not been declared is refused.
- One token and one actor per hosted Glasshouse. Everyone holding the token shares one name and one limit, so a class should run local copies.
- The hosted demo resets every night.
