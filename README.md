# Glasshouse

**The open ETRM core for European power. Trades, curves, positions, P&L and audit, without the black box.**

Glasshouse is an open-source system of record for European power operations: books, counterparties, trades, amendments, official curves, positions, P&L, corrections, and an audit history good enough to hand to an auditor. Apache 2.0. PostgreSQL. A documented HTTP API. CSV in, CSV out.

The architecture in one sentence: **a governed operational ledger for power trading, where every write is a proposal, every accepted proposal is a transition, every large artefact is hash-anchored, every operational view is replayable, and every number can be explained.**

> **Status: pre-v0, the vertical needle is built and the first screens exist.** One trade, one official curve, one MTM only admissible against the official curve, one correction that supersedes, one as-of query - running end to end through a governed ledger, with CSV imports (quarantine per row, and a dry-run preview that explains refusals in business terms), replayable projections (blotter, hourly positions, valuations), generated per-predicate SQL views as the official inspection model over governed state, `glasshouse verify` (six legs) proving the database still agrees with the ledger, a read API over the projections (blotter, positions, valuations, orgs, overview) plus a dry-run `/explain`, three server-rendered read-only screens (Overview, Blotter, Positions & P&L under `/ui` - the first slice of the six-screen demo, readiness L0 stated on screen), a deterministic `glasshouse seed --reset` for the demo dataset, and CI that proves all of it live on every PR. Not yet deployable as a product: the HTTP write path and the hosted demo are the next milestones.

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

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker (for the TimescaleDB Postgres 18 the migrations need), and a [morpholog](https://github.com/jordan-dimov/morpholog) binary (`cargo build --release`, then point `GLASSHOUSE_MORPHOLOG_BIN` at it).

```bash
docker compose up -d          # TimescaleDB-enabled Postgres 18 (the floor)
uv sync --dev
uv run pytest                 # live integration legs run when the binary and a database are reachable
uv run uvicorn glasshouse.api.app:app --reload
```

The operator CLI exists ahead of the API:

```bash
uv run python -m glasshouse.cli seed --reset         # the Monday-morning demo dataset (destructive; refused in production; a plain `seed` refuses any ledger with history)
uv run python -m glasshouse.cli apply-views          # the SQL inspection model, after `morpholog init`
uv run python -m glasshouse.cli import-trades trades.csv --org acme --actor alice --project
uv run python -m glasshouse.cli import-curves curves.csv --org acme --actor carol
uv run python -m glasshouse.cli project --follow     # the projector as a worker
uv run python -m glasshouse.cli verify               # all six legs agree, or the exit code says not
uv run python -m glasshouse.cli checkpoint           # anchor the audit log's tamper-evidence tree
uv run python -m glasshouse.cli evidence-export pack.json   # a portable pack a third party verifies offline
uv run python -m glasshouse.cli evidence-verify pack.json   # recompute the Merkle root, no database needed
```

Provisioning a fresh database is `morpholog init` (the governed schema), the Alembic migration (the app schema), then `glasshouse apply-views` (the official inspection model). `glasshouse verify`'s `views` leg fails until that last step has run, by design: the inspection model is part of the operational surface it checks.

### Configuration and logs

Configuration is environment-only (twelve-factor), prefixed `GLASSHOUSE_`: `GLASSHOUSE_DATABASE_URL`, `GLASSHOUSE_MORPHOLOG_BIN`, and `GLASSHOUSE_ENVIRONMENT` (`dev`, `demo` or `production`). Operational logs go to **stderr**, leaving stdout as the CLI's data channel (the import report, the projector count); `dev` renders a readable console line, `demo` and `production` render JSON lines for a log aggregator. The operational log is separate from the governed audit log in the ledger, which stays the product.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
