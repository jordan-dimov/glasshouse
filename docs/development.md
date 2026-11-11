# Developing Glasshouse

The technical companion to the [README](../README.md): setup, tests, the command-line tools, provisioning, configuration and logs.

## Reading order

1. [The coverage table](coverage.md): what a full ETRM does and where Glasshouse stands on each function, with the issue for everything planned.
2. [DESIGN.md](../DESIGN.md): the founding design document. What Glasshouse is, who adopts it, the architecture, the readiness ladder.
3. [SCOPE.md](../SCOPE.md): what Glasshouse is not, stated plainly.
4. [The rule file](../src/glasshouse/commit/glasshouse.morph): every fact the ledger may hold and every way it may change.
5. [Agents over MCP](mcp.md): how an AI agent connects, with two walk-throughs.
6. [The Morpholog integration contract](morpholog-integration-contract.md): the working record of how Glasshouse uses the engine underneath, one section per upgrade or rule change.
7. [Upgrading Morpholog](operations/upgrading-morpholog.md): the database runbook.

This page has two halves. The first is how to run and change Glasshouse: setup, checks, the command line, the API, configuration, provisioning and upgrading. The second is how it behaves: currencies, instructions, refusals and viewing the past.

## Setup

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and Docker (for a PostgreSQL with TimescaleDB, which the migrations need; PostgreSQL 17 is the floor, the compose file and the demo run 18).

The [morpholog](https://github.com/jordan-dimov/morpholog) binary is a checksummed download, not a build: no Rust toolchain, and it is the exact release CI and the deployed image use. The install script picks the artefact for your machine and covers linux x86_64, linux arm64 and macOS on Apple Silicon. On an Intel Mac, build morpholog from source (its README) and point `GLASSHOUSE_MORPHOLOG_BIN` at the result; nothing else differs.

```bash
./scripts/install-morpholog.sh ~/.local/bin   # the pinned release
export GLASSHOUSE_MORPHOLOG_BIN=~/.local/bin/morpholog
docker compose up -d                          # PostgreSQL 18 with TimescaleDB on localhost:5432
uv sync --dev
uv run python -m glasshouse.cli seed --reset  # the demo book (destructive; local databases only in dev)
uv run uvicorn glasshouse.api.app:app --reload
```

The screens are under <http://localhost:8000/ui> and the API's OpenAPI page is at <http://localhost:8000/docs>.

If you already run a PostgreSQL on port 5432, the compose database cannot bind it. Change the left-hand side of the `ports` entry in `docker-compose.yml` (for example `"5433:5432"`) and set `GLASSHOUSE_DATABASE_URL=postgresql://glasshouse:glasshouse@localhost:5433/glasshouse`.

## Checks

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
uv run pytest
```

`pytest` runs the pure tests always, and the live integration tests when the morpholog binary and a database are reachable (`GLASSHOUSE_TEST_DATABASE_URL`). CI runs both on every pull request, the live leg against the pinned morpholog release.

## The command line

Run as `uv run python -m glasshouse.cli <command>`; every command has `--help`.

| Command | What it does |
|---|---|
| `provision` | Non-destructive first boot, safe on every deploy: migrate the app schema, initialise and migrate the ledger's schema, check the rules fit the ledger, build the managed indexes, apply the inspection views. |
| `seed` | Load the demo book and verify it. `--reset` wipes first; it is refused in production, and in dev on anything but a local database. A plain `seed` refuses a ledger that already has history. |
| `import-trades` | Import a trades CSV (`book,trade,trade_date,counterparty,market,direction,quantity,price,delivery_start,delivery_end`). Bad rows are quarantined and explained, never coerced. `--preview` commits nothing. |
| `import-curves` | Import a curves CSV (`market,as_of,version,period_start,price`). Prices are in the currency of the market named. |
| `set-limit` | Set or replace a book's cap on its net position in any delivery hour. |
| `project` | Bring the read tables up to date with the ledger. `--follow` runs it as a worker. |
| `verify` | Check that the operational database still agrees with the ledger (six independent checks). The exit code says whether it does. |
| `checkpoint` | Record a tamper-evidence checkpoint over the audit log. |
| `mcp` | Serve the book to an AI agent over MCP, on standard input and output. `--actor` names who the agent acts as. See [mcp.md](mcp.md). |
| `evidence-export` | Write the audit history to a file a third party can verify. |
| `evidence-verify` | Verify such a file offline, with no database. |
| `apply-views` | Apply the SQL inspection views on their own (`provision` already does). |

## The API

Interactive documentation is served at `/docs` on a running server. Every read takes `org`, because every answer is about one organisation.

| Address | What it returns |
|---|---|
| `GET /orgs`, `/overview` | The organisations, and one organisation's books, values per currency and system state. |
| `GET /trades`, `/trades/{trade}/terms` | Trades as they stand, and every version of one trade. |
| `GET /positions` | Net MW per book, market and delivery hour. |
| `GET /valuations` | Each valuation, naming the curve and trade version it used. |
| `GET /markets`, `/curves`, `/curves/diff` | Markets, curve versions, and what changed between two versions. |
| `GET /instructions` | Open orders and what each holds under its book's limit. |
| `GET /audit` | Every accepted change, newest first. |
| `GET /refusals` | Recent refused proposals. Not complete; see below. |
| `POST /explain` | Whether a change would be accepted, and why not. Records nothing. |
| `/mcp` | The same book for AI agents, when a token is configured. See [mcp.md](mcp.md). |

`/trades`, `/positions` and `/valuations` also take `as_of`. There is no JSON address that records a trade yet: trades arrive by import, by the command line or through an agent.

## Configuration and logs

Configuration is environment-only, prefixed `GLASSHOUSE_`:

- `GLASSHOUSE_DATABASE_URL` (default: the compose database on localhost).
- `GLASSHOUSE_MORPHOLOG_BIN`: path to the morpholog binary.
- `GLASSHOUSE_ENVIRONMENT`: `dev`, `demo` or `production`.
- `GLASSHOUSE_DEMO_PASSWORD`: unset means no login, for local development. Set, it puts the shared demo login (HTTP Basic, username `demo`) in front of everything but the health probes, and in the demo environment browser writes refuse to run without it.
- `GLASSHOUSE_MCP_TOKEN`: unset means agents cannot connect over HTTP at all. Set (12 characters or more), the MCP server is served at `/mcp` to callers who send it as a bearer token. See [mcp.md](mcp.md).
- `GLASSHOUSE_MCP_ACTOR`: the actor a caller with that token acts as (default `agent`).
- `GLASSHOUSE_MCP_TOKENS`: more callers, each its own named actor, as a JSON object of actor and token: `{"risk-manager": "a-long-secret"}`. Each token must be 12 characters or more and belong to one actor only. A token does not prove who holds it.

Operational logs go to stderr, leaving stdout as the command line's data channel (an import report, a projector count). `dev` renders a readable console line; `demo` and `production` render JSON lines for a log aggregator. The operational log is separate from the audit history in the ledger.

## Provisioning

`glasshouse provision` is the whole sequence and is idempotent; the deployed service runs it as its pre-deploy step. By hand the order is: the Alembic migration (the app schema), `morpholog init` then `morpholog migrate` (the ledger's schema), `morpholog check --strict --database-url` (the rules fit the claims the ledger holds), `morpholog provision indexes` (the indexes and planner statistics the ledger's reads use; `init` does not build them), then `glasshouse apply-views`.

`glasshouse verify`'s `views` check fails until the views step has run, by design: the inspection views are part of what it checks.

## Upgrading

Pull, then run `glasshouse provision`. It applies any schema change and is safe to run every time.

A change to the rule file is different. If it changes the shape of a fact the ledger already holds, an existing ledger no longer fits the rules and `provision` refuses it by name. One such change has been made so far, as a clean break, because no ledger was being kept anywhere: the only one was the demo's, which is re-seeded every night.

- **Currencies (November 2026).** A valuation gained a `currency` field and every market must now be declared with its currency. A database from before this has no upgrade path. Re-seed it: `uv run python -m glasshouse.cli seed --reset`.

- **Instructions (November 2026).** New facts only; nothing existing changed shape, so an older database still fits. But the right to place instructions is granted by the seed, so re-seed to try them.

A clean break will not be acceptable once someone keeps a ledger. From the first kept ledger onwards, a change to the shape of a recorded fact needs an upgrade path that keeps the old record readable, and a change without one should not be merged.

## Currencies

A market is declared once, with the currency it is priced in (`declare_market` in the rule file). That declaration is the only place a currency comes from. A trade's price and a curve's prices are per MWh in their market's currency, and a valuation states its currency, which the ledger checks against the market's. A trade or a curve in a market that has not been declared is refused.

Every price and valuation is returned with a `currency` field, by the API, the MCP tools and the screens. Nothing is added across currencies: the overview gives one total per currency. There is no conversion and no exchange rate.

The list of currencies is closed and lives in the rule file: EUR, GBP, NOK, SEK, DKK, PLN and RON, the currencies European power markets are priced in. Adding one is a change to the rule file.

The CSV files have no currency column. A row takes the currency of the market it names.

## Instructions

An instruction is an intention to buy or sell that has not happened yet, such as an order sent to market. From the moment it is placed it holds room under its book's position limit, so a trade or another instruction cannot take that room. `/instructions?org=...` lists them; the Positions screen shows what is held; agents place and cancel them through MCP ([mcp.md](mcp.md)).

- Open buys count towards the bought side of the limit and open sells towards the sold side. They are never set against each other.
- A trade refused because instructions hold the room is refused by `room_not_held_by_instructions`, not by `hour_within_limit`, and its figures are the quantity instructed and the room the trades leave.
- An instruction holds its room until it is cancelled, or until its lapse is recorded after `valid_until`. Time passing does not free the room by itself.
- A book can require approval (`require_approval`). An instruction there is approved or rejected by someone who holds `MayApprove` in the book and did not place it. The approval is recorded; it is not enforced on the market.
- It is not connected to fills yet.

## Refusals

`/refusals?org=...` returns the most recent proposals the rules refused for an organisation, with the rule and the figures. The Audit screen shows the same list. It is not the audit history and it is not complete: the engine writes a refusal at most once, after the proposal is rolled back, so one can be missing. Each answer says so and says how many it reaches back (`limit`, 20 unless asked, 200 at most).

## Viewing the past

`/trades`, `/positions` and `/valuations` take `as_of`, an ISO 8601 instant (no offset means UTC), and answer as the book stood then. The Trades and Positions screens have the same control in the top bar, and changing the time keeps the filters you had. The other screens show current data and say so.

The answer is computed by replaying the ledger's history up to that instant through the same code that maintains the live tables. Three things follow:

- An answer, once given, does not change. A moment is refused with a 409 while a write that started before it is still in progress, because that write's rows could still land before it. Morpholog makes that call, not a timer here. Ask again shortly. A future moment is a 422.
- Read at the present, it matches the live tables whenever the projector has caught up with the ledger. If the projector is behind, the as-of read is the more recent of the two.
- It shows the past as today's code reads it. If the way positions are computed ever changes, an old moment will be recomputed the new way.

Each as-of request replays the whole history, and the work grows with the number of delivery hours as well as the number of transitions. That is fine for the demo book and has not been measured on a large one.
