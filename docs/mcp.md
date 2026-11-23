# Agents: Glasshouse over MCP

Glasshouse can be used by an AI agent. It speaks the [Model Context Protocol](https://modelcontextprotocol.io) (MCP), the common way for an agent to be given tools. An agent connected to Glasshouse can read the book, ask whether a trade would be accepted, and propose one.

An agent's trade is a proposal like anyone's, and the same rules accept or refuse it. It acts under its own name, and what it does is recorded under that name.

## Agents trade only under a mandate

Staying within a book's limits is not the same as being allowed to trade. A book's limit says how much the book may hold, not what an agent was given to do. So the ledger knows which actors are agents, and treats them differently in one way: **an agent books nothing through the acts people use.** Entering a trade, amending one, placing, cancelling or approving an instruction, setting a limit, answering a breach: each refuses an agent by the rule `actor_is_not_an_agent`, even if someone gave the agent the right a trader holds.

An agent trades only with `trade_under_mandate`, inside a **mandate**. A mandate is what a desk approves for an algorithm, once:

- one book and one market;
- buy, sell or both;
- the largest single order, in MW;
- the most energy it may trade in all, in MWh: each trade counts its MW times its delivery hours;
- a price band;
- a delivery window;
- when the mandate ends.

A person proposes the mandate (`glasshouse propose-mandate`), and a different person approves it (`glasshouse approve-mandate`). The agent then trades inside it as often as its strategy calls for, with no person in the loop. Each trade is checked against every bound. The total is energy: each trade counts its MW times its delivery hours, added by the ledger to the trades already booked under the mandate, so splitting one large order into many small ones gains nothing. The book's position limit applies as well.

Anyone who may give mandates can suspend one at once (`glasshouse suspend-mandate`), and the next trade is refused. The agent may suspend its own mandate. It cannot propose, approve, widen or renew one: each of those is a new mandate, proposed by a person and approved by another. An agent cannot be given the right to give mandates or grant permissions, and an actor that holds either cannot be declared an agent.

**The model behind the agent.** A mandate admits trades only while the model the agent runs on is the one a person approved. A person records the model, with its version (`glasshouse record-agent-model`), and a different person approves the record (`glasshouse approve-agent-model`). Recording a new model drops the old approval, so the agent's mandates admit nothing until the new record is approved. The agent can still read the book. The agent never says which model it is. The server it connects through says it, from what its operator configured: `glasshouse mcp --model`, or `GLASSHOUSE_MCP_MODELS` for a hosted server. A server with no model configured names none, and every mandated trade through it is refused by `model_is_approved`.

Two kinds of agent, two answers:

- **An assistant** asked a question holds no mandate. It reads, explains and books nothing. The seeded `agent` is one.
- **A trading agent** holds a mandate and books everything inside it without asking anyone. The seeded `algo` is one: mandate `M-ALGO` in `spec-de`, buy or sell, 2 MW an order, 20 MWh in all, prices 50 to 150, delivery on 1 July 2026, model `example-strategy-1`.

[Agent authority, tested](agent-authority.md) reports a test of these rules against a strong model given tasks designed to get round them.

## The tools

| Tool | What it does |
|---|---|
| `list_trades` | Every trade in the book, each with its price and the currency of that price. Takes `as_of` to see the book at a past time. |
| `list_positions` | Net MW per delivery hour, per book. Takes `as_of` too. |
| `list_markets` | The markets, each with the currency it is priced in. |
| `business_date` | The business date (the day valuations are for), how many trades are valued for it, and why the others are not. Read only. |
| `position_limits` | The cap on each book's position in any one hour. |
| `explain_trade` | Asks whether a trade would be accepted. Records nothing. |
| `explain_trade_under_mandate` | Asks whether a trade under a mandate would be accepted now. Records nothing. |
| `propose_trade` | Proposes one trade, for a person. An agent is refused. |
| `list_mandates` | Agents' mandates: the bounds of each, how much is used, whether it is open, suspended, expired or awaiting approval, and whether the agent's model record is approved. |
| `trade_under_mandate` | An agent books one trade under its mandate. The book and market are the mandate's; the trade date is today and the model is the one the server reports. |
| `propose_mandate` | A person's act: an agent that calls it is refused. People use `glasshouse propose-mandate`. |
| `approve_mandate` | A person's act, refused for an agent (`glasshouse approve-mandate`). |
| `suspend_mandate` | Suspends a mandate at once and for good. An agent may suspend its own. |
| `record_agent_model` | A person's act, refused for an agent (`glasshouse record-agent-model`). |
| `approve_agent_model` | A person's act, refused for an agent (`glasshouse approve-agent-model`). |
| `place_instruction` | Records an intention to buy or sell that has not happened yet, and holds room for it under the book's limit. For a person: an agent is refused. |
| `cancel_instruction` | Cancels an instruction. Its room is free again at once. |
| `approve_instruction` | Approves an instruction as a second person. Refused if you placed it yourself. |
| `reject_instruction` | Turns an instruction down. It ends and its room is free again. |
| `lapse_overdue_instructions` | Records the lapse of every instruction in a book whose time has run out, and frees the room each held. |
| `list_instructions` | Instructions, how much each still holds, whether each is open, overdue or ended (filled, cancelled, rejected or lapsed), and whether it is approved. |
| `audit_history` | The accepted changes, newest first, with who made each. |
| `recent_refusals` | Recent refused proposals, with the rule and the figures. |

Quantities are MW. A price is per MWh in the currency of its market: euros for German power, pounds for British. Every price and valuation a tool returns comes with a `currency` field, and `list_markets` gives each market's currency. Numbers are sent as text such as `"2.5"`, never as floating point numbers. Times are UTC and must carry an offset, such as `2026-07-01T12:00:00+00:00`.

No tool takes an actor or an organisation. Both are fixed when the server starts, so an agent cannot act as someone else or read another organisation's book.

## Connect locally

Follow [Run it locally](../README.md#run-it-locally) first, so there is a seeded book. Then the server is one command. It talks over standard input and output, which is how MCP clients start a local server:

```bash
uv run python -m glasshouse.cli mcp --actor agent
```

For the trading agent, name it and the model it runs on:

```bash
uv run python -m glasshouse.cli mcp --actor algo --model example-strategy-1
```

You rarely run that by hand. You give the command to your MCP client and it starts the server itself. For Claude Code:

```bash
claude mcp add glasshouse -- uv run --directory /path/to/glasshouse python -m glasshouse.cli mcp --actor agent
```

Other clients take the same command and arguments in their own settings file.

The seeded book has two agents: `agent`, an assistant with no mandate, and `algo`, which trades under mandate `M-ALGO` in `spec-de`. `--actor` can name any actor, but an actor the book has granted nothing will be refused everything.

## Connect to a hosted Glasshouse

A hosted Glasshouse serves the same tools at `/mcp`, but only if its operator has set `GLASSHOUSE_MCP_TOKEN`. Without the token the address does not exist. With it, a client must send the token as a bearer header:

```bash
claude mcp add --transport http glasshouse https://your-host/mcp --header "Authorization: Bearer YOUR-TOKEN"
```

Whoever holds the token acts as one actor (`agent`, or the name in `GLASSHOUSE_MCP_ACTOR`).

**This door serves agents only.** Every call made with a token for an actor the ledger has not declared an agent is refused, and the server names such tokens in its log when it starts. `glasshouse mcp` will not start for one either. People use the screens and the command line.

An operator can give out more tokens, each for its own named actor, with `GLASSHOUSE_MCP_TOKENS`: a JSON object of actor and token, such as `{"risk-manager": "a-long-secret", "alice": "another-long-secret"}`. Each caller is then one name in the record. Each must be a declared agent.

`GLASSHOUSE_MCP_MODELS` says which model each agent behind the hosted server runs on: a JSON object of actor and model, such as `{"algo": "example-strategy-1"}`. An agent with no entry names no model, and its mandated trades are refused.

What a token does not prove:

- that the person holding it is who the name says;
- that two tokens are held by two different people;
- anything about someone who has the server's database address, who can still act as anyone.

A token is a shared secret, not a sign-in. There is no OAuth here, so clients that only connect through an OAuth sign-in screen cannot use it yet.

## Walk through: a trading agent meets the limit

The seeded `spec-de` book may hold at most 25 MW in any one delivery hour. At 12:00 on 1 July 2026 it already holds 20 MW. The example script acts as `algo` and buys 2 MW in that hour under its mandate, again and again, until the rules say no. It needs no AI model and no API key.

```bash
uv run python examples/limit_refusal.py
```

```
spec-de, hour 2026-07-01 12:00 UTC: holding 20 MW, cap 25 MW
  buy 2 MW: accepted as algo
  buy 2 MW: accepted as algo
  buy 2 MW: REFUSED

rule:     hour_within_limit
figures:  26 MW <= 25 MW does not hold
hour:     2026-07-01T12:00:00+00:00
```

The first two trades take the hour to 22 and then 24 MW. The third would make it 26, so it is refused. A refusal is a normal answer, not an error. The agent receives it as data it can reason about:

```json
{
  "accepted": false,
  "actor": "algo",
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

An agent that calls `position_limits` and `list_positions` first can work out that only 5 MW of room is left. The mandate allowed each of these trades; the book's limit refused the third. Try asking `algo` to sell 3 MW: it is refused by a different rule, `order_within_mandate`, because its mandate allows 2 MW an order. Ask `agent` to book anything and it is refused by `actor_is_not_an_agent`, or `actor_holds_the_mandate` if it names `algo`'s mandate.

## Walk through: an open order holds its room

A trader sends an order to market. Until it fills, nothing has been traded, so the book looks as if there is room. Someone else then books a trade into that room, and when the order fills the limit is broken. An instruction closes that gap: it tells the book about the intention, and the book holds the room.

The `spec-de` book holds 20 MW at 12:00 and its limit is 25 MW, so 5 MW is left.

1. **Place an instruction for 4 MW.** `place_instruction` is accepted. 4 of the 5 MW are now held.
2. **Propose a trade for 2 MW in the same hour.** On the booked trades alone it would fit: 22 of 25 MW. It is refused, and the refusal says why:

```json
{
  "accepted": false,
  "summary": "room_not_held_by_instructions: 4 MW <= 3 MW does not hold (h = 2026-07-01T12:00:00+00:00)",
  "refusal": {
    "rule": "room_not_held_by_instructions",
    "compared": { "op": "<=", "left": "4 MW", "right": "3 MW" }
  }
}
```

Read the two figures as: 4 MW is instructed, and after this trade only 3 MW of room would be left.

3. **Cancel the instruction.** `cancel_instruction` is accepted, and the 4 MW are free.
4. **Propose the same trade again.** It is accepted.

How to tell the two refusals apart:

- `hour_within_limit`: the booked trades themselves leave no room. The figures are the position and the limit.
- `room_not_held_by_instructions`: there would be room, but open instructions hold it. The figures are the quantity instructed and the room the trades leave.

Three things to know:

- **Buys and sells are not set against each other.** An open sale does not make room to buy, because it may never fill. Open buys count towards the bought side and open sells towards the sold side.
- **An instruction holds its room until it ends or its time runs out.** It ends when it is cancelled, rejected or filled. Once its `valid_until` time has passed it holds no room and is shown as overdue until someone cancels it or the sweep records that it lapsed.
- **Booking the trade that results is still `propose_trade`.** Cancel the instruction first, or its room stays held on top of the trade.

## Walk through: a second person approves

A book can require approval. In the demo book, `spec-de` does. An instruction placed there shows `"approval": "awaiting"` in `list_instructions` until a different person approves it.

Two rules decide who may approve:

- they must hold the right to approve in that book (`may_approve`);
- they must not be the one who placed the instruction (`approver_is_not_proposer`).

Approving is a person's act, so it is done on the command line or by code calling the tools directly, not through the agents' door. With three names (`demo`, who may both instruct and approve, `risk-manager`, who may approve, and `agent`, an agent):

1. **`demo` places an instruction.** Accepted. It holds its room at once and awaits approval.
2. **`demo` tries to approve it.** Refused by `approver_is_not_proposer`.
3. **`agent` tries to approve it.** Refused by `may_approve`: it was never given that right, and an agent would be refused by `actor_is_not_an_agent` if it had been.
4. **`risk-manager` approves it.** Accepted. `list_instructions` now shows `"approval": "approved"` and `"approved_by": "risk-manager"`.

The audit history holds two changes for that instruction under two different names. `reject_instruction` works under the same two rules, and ends the instruction.

Things to know:

- **An approval does not go out of date.** An instruction cannot be changed after it is placed, and it has held its room since then. To change the size or price, cancel it and place a new one, which needs a new approval.
- **Approval is recorded, not enforced on the market.** Glasshouse cannot stop an order leaving the building. A client must read `approval` and not act on an instruction that is awaiting it.
- **A refused approval is not in the audit history.** Like every refusal, it is in the separate best-effort list (`recent_refusals`).

### What an instruction is not, yet

- **Not yours to fill.** When the order fills in the market, the firm's reconciling service records the fill: it books the trade and frees the room the instruction held. No tool records a fill, and booking the result with `propose_trade` would count it twice.

## What is recorded

An accepted trade is in the audit history under the agent's name, like any other change. `audit_history` with `actor` set to `agent` shows everything it did.

A refusal is not in the audit history. The audit history holds only what was accepted. Refusals are kept in a separate log that is best-effort: each one is written once, after the proposal is undone, and that write can occasionally fail. `recent_refusals` reads that log and says so in every answer. Treat it as a good lead, not as proof. The same list is on the Audit screen under "Refused lately", and on the API at `/refusals`.

## What an agent cannot do

- Write to the database any way other than a proposal.
- Book a trade with no mandate, or outside any bound of its mandate.
- Give, approve, widen or renew a mandate, or approve the record of its own model.
- Keep trading once its mandate is suspended or has ended, or once its model changes.
- Pass a position limit.
- Choose which actor or organisation it is, or which model it runs on.

Each of these is enforced by the rules in the ledger, not by the MCP server, with one exception: the model a trade names comes from the server's configuration. The server has no way round the rest.

What the ledger cannot know: that an actor is an agent until someone declares it one. Both doors here refuse any actor that is not a declared agent, so software that comes in through them is bound by the agent rules. What no door can stop is a person giving their own credentials to an AI somewhere else: to the ledger, that is the person.

## Limits of this first version

- One trade type: a fixed-price power trade. No amendments yet.
- Instructions cannot be filled yet (see above).
- A token per actor, not a sign-in per person.
- No tools yet for curves, valuations or the list of organisations.
- A trade takes the currency of its market. You do not state one, and a trade in a market that has not been declared is refused.
- A token per actor (`GLASSHOUSE_MCP_TOKENS`), not a sign-in. Everyone holding one token shares one name and one limit, so a class should run local copies.
- A mandate bounds one book and market, its orders, its total in MW and its delivery window. It does not yet bound the hours of the day it may trade, or the net position it may build within the book's limit.
- Mandates are given, approved and suspended on the command line; over MCP an agent can only read its mandate and suspend it. The screens do not show them yet.
- The hosted demo resets every night.
