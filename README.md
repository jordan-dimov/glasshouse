# Glasshouse

**The open ETRM core for European power. Trades, curves, positions, P&L and audit, without the black box.**

Glasshouse is an open-source system of record for European power operations: books, counterparties, trades, amendments, official curves, positions, P&L, corrections, and an audit history good enough to hand to an auditor. Apache 2.0. PostgreSQL. A documented HTTP API. CSV in, CSV out.

The architecture in one sentence: **a governed operational ledger for power trading, where every write is a proposal, every accepted proposal is a transition, every large artefact is hash-anchored, every operational view is replayable, and every number can be explained.**

> **Status: pre-v0, the vertical needle is built.** One trade, one official curve, one MTM only admissible against the official curve, one correction that supersedes, one as-of query - running end to end through a governed ledger, with CSV imports (quarantine per row), replayable projections (blotter, hourly positions, valuations) and CI that proves all of it live on every PR. Not yet deployable as a product: the HTTP API and the hosted demo are the next milestones.

## Why this exists

A modern power-trading team (a 3-20 person startup, a BESS or VPP operator, a renewables portfolio manager) lives in a gap: spreadsheets and a hand-rolled trade table on one side, heavyweight vendor ETRMs and black-box SaaS on the other. There is no mature, audit-first, lifecycle-complete open-source ETRM core for European power. Glasshouse is that core.

What you get instead of building it in-house:

- positions and P&L by delivery period, by book, against the **official** curve as of any business date;
- corrections that **supersede, never overwrite**: "what did we know at 10:00 last Tuesday, and under which curve?" is a query, not a forensic project;
- lawful rejection as a first-class outcome: the system refuses to commit state that breaks the rules, and explains why in business terms;
- `glasshouse verify`: prove, on demand, that the operational database still agrees with the governed ledger;
- native 15-minute Market Time Units alongside GB half-hours, DST handled correctly;
- CSV import with quarantine (never silent coercion), tests, CI, OpenAPI, Docker.

## Reading order

1. [DESIGN.md](DESIGN.md) — the founding design document: what Glasshouse is, who adopts it, the four primitives, the architecture, the readiness ladder.
2. [SCOPE.md](SCOPE.md) — what Glasshouse is **not**, stated plainly.
3. [docs/morpholog-integration-contract.md](docs/morpholog-integration-contract.md) — the contract with the governed commit layer.

## Development

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker (for Postgres), and a [morpholog](https://github.com/jordan-dimov/morpholog) binary (`cargo build --release`, then point `GLASSHOUSE_MORPHOLOG_BIN` at it).

```bash
docker compose up -d          # TimescaleDB-enabled Postgres
uv sync --dev
uv run pytest                 # live integration legs run when the binary and a database are reachable
uv run uvicorn glasshouse.api.app:app --reload
```

The operator CLI exists ahead of the API:

```bash
uv run python -m glasshouse.cli import-trades trades.csv --org acme --actor alice --project
uv run python -m glasshouse.cli import-curves curves.csv --org acme --actor carol
uv run python -m glasshouse.cli project --follow   # the projector as a worker
```

## Licence

Apache 2.0. See [LICENSE](LICENSE).
