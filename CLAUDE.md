# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this is

Glasshouse: the open ETRM core for European power. The architecture in one sentence, which everything must serve: **a governed operational ledger for power trading, where every write is a proposal, every accepted proposal is a transition, every large artefact is hash-anchored, every operational view is replayable, and every number can be explained.**

**Read `DESIGN.md` before doing anything non-trivial.** It is the founding document and the source of truth for scope, architecture and sequencing. `SCOPE.md` states what Glasshouse is not. `docs/morpholog-integration-contract.md` is the coordination record with the substrate.

This is a commercial open-source product, not a demo and not a teaching artefact. The adoption test for every decision: would a 3-20 person power-trading startup, or a BESS/VPP operator, run this? Demonstration and pedagogy are consequences, never the identity.

## Current state and next steps

Pre-v0 scaffold: package skeleton, smoke tests, compose, render.yaml, CI. No domain code yet.

Build order (DESIGN.md section 5): **the vertical needle first** — one trade, one official curve, one MTM that is only admissible against an official curve, one correction that supersedes, one as-of query — then the hosted demo off the needle plus CSV imports, then widen to the full v0 spine. Do not build horizontally (all reference data, then all trades, ...); build the needle end to end.

## The laws (non-negotiable)

1. **Writes to governed state only ever go through the commit layer** (`glasshouse.commit`, wrapping the morpholog binary). No ORM writes, no raw SQL writes to governed state, no exceptions. This rule is the product.
2. **Supersede, never overwrite.** Amendments and corrections retract standing; they never erase history.
3. **The read-side law**: every app-schema table is either a projection (carries its source transition id, rebuilt by replaying the log from zero) or a hash-anchored payload (bulk content whose hash was admitted in a governed claim). Nothing else. This is what makes `glasshouse verify` possible, and `verify` is a headline capability.
4. **Projections are the primary read model; generated per-predicate SQL views are the official inspection model.** Never read governed state via raw positional JSONB.
5. **Authority is governed capability claims in the ledger** (`may_capture_trade(actor, org, book)`, ...). App-side auth maps identity to an actor string and nothing more. Roles are seeded convenience bundles, not first-class law. No RBAC subsystem.
6. **The organisation is the tenancy boundary.** Org on every row, structurally, from day one. No separate tenant concept, no scenario concept.
7. **Product knowledge lives in product packs** (`glasshouse.packs`). The core knows proposals, transitions, payloads, projections, books, counterparties, authority and time — never power periods, PPA structures or BESS legs. v0 ships one pack (power_fixed_price) in-tree; extract the pack interface only when the second pack forces it.
8. **Money and quantities are Decimal from strings, never via float.**
9. **Delivery periods are UTC instants**; DST days (92/100 periods on the continent, 46/50 in GB) fall out of timezone arithmetic and are never special-cased.
10. **Typed outcomes at the commit boundary**: `Committed | Rejected` as a discriminated union; operational failure raises. Rejection is a lawful outcome, not an error.

## Morpholog (the substrate)

- Local repo: `~/dev/morpholog`; built binary at `~/dev/morpholog/target/release/morpholog`. Set `GLASSHOUSE_MORPHOLOG_BIN` accordingly in dev. Embedder docs: `~/dev/morpholog/docs/embedder-integration.md`.
- Integration is via subprocess (per-call now; `run --batch` NDJSON once it lands upstream — each batch row is its own SERIALIZABLE transition, never all-or-nothing).
- Pydantic request models and typed decoders are **generated from `morpholog schema` in a build step, committed to the repo, and drift-checked in CI** — never runtime-dynamic.
- Upstream work in flight for us (in order): model hash + schema manifest, `run --batch`, the per-predicate views generator, the hash-chained audit log. **v0 blocks on none of it**; adopt each surface as it lands. Full verdicts: `docs/morpholog-integration-contract.md`.
- **Friction with Morpholog is reported upstream as an issue, never worked around silently.** New upstream asks must be forced by a concrete Glasshouse scenario (the substrate's own forced-by-example doctrine). The Subject stays opaque: never ask Morpholog to understand power.
- In public-facing copy (README, docs, site), Morpholog is deliberately second-level: "the reason the audit trail is so good", not the headline.

## Stack and commands

Python 3.13+ (CI: 3.13 + 3.14) · uv · ruff (format + lint) · mypy strict, blocking · pytest + Hypothesis · FastAPI + Pydantic v2 · SQLAlchemy 2 Core (no ORM models on governed state) + psycopg3 · Alembic (app schema only) · structlog · PostgreSQL 17+ floor, demo on 18 · TimescaleDB (a dependency, not an option; hypertables only ever in the app schema, never under morpholog's).

```bash
docker compose up -d        # TimescaleDB-enabled Postgres
uv sync --dev
uv run pytest
uv run ruff format . && uv run ruff check . && uv run mypy
uv run uvicorn glasshouse.api.app:app --reload
```

All checks green before any commit. CI must stay green on every push to main.

## UI (when the demo milestone arrives)

Server-rendered Jinja2 + HTMX; Alpine.js only for tiny local interactions. No SPA, no React, no Node build step. CSS is custom properties plus a small hand-written component layer (no Tailwind). The aesthetic is "Glasshouse Control Room": dense, calm, operations-grade, tabular numerals wherever money or quantity appears. Five UI laws in DESIGN.md section 12 — the fifth is that every material number is explainable in place.

## Writing and conventions

- **British English everywhere**: organisation, licence (noun), authorised, behaviour. DD/MM/YYYY in prose; ISO 8601 in code and data.
- **No em-dashes** in documentation or prose; use commas, colons, parentheses or full stops. Plain hyphens for compounds and ranges.
- **Git: single-line commit messages.** No bullet-list bodies, no generated-with footers.
- Match the existing code's comment density and idiom. Docstrings explain *why* a package or rule exists, not just what it does.
- Honest claims only: the readiness ladder is real (the demo is L0 and says so); performance numbers are published, not hidden (~1.6s/commit on a 100k ledger upstream); the market claim is precise ("no mature, audit-first, lifecycle-complete open-source ETRM core for European power"), never inflated.

## What not to do

- Do not add a write path that bypasses the commit layer, even "temporarily".
- Do not add an RBAC table, a tenant concept, a scenario concept, or a second read story.
- Do not introduce React, Node tooling, Tailwind, an ORM on governed state, or MCP servers.
- Do not raise the PostgreSQL floor above 17 or the Python floor above 3.13 without a load-bearing reason.
- Do not work around Morpholog limitations silently, and do not request upstream semantics without a forcing example.
- Do not let simulator/teaching conveniences (fixtures, forgiving flows) into the core. The core stays strict and boring.
- Do not market Glasshouse as compliance-certified, as an optimiser, or as feature-complete against vendor ETRMs.
