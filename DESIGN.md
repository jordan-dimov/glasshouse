# Glasshouse

**The open ETRM core for European power. Trades, curves, positions, P&L and audit — without the black box.**

Design document, rev 3, 07/06/2026. (Rev 1 framed this as an audit-first *reference implementation*; rev 2 reframed it around commercial adoption, which is the priority: Glasshouse must be something a power-trading startup or scaleup would actually adopt and run; demonstration and pedagogy are consequences, not the identity. Rev 3 separated core laws from accelerators: the four primitives, self-verification as a pillar, capabilities over roles, product packs, and a single-process demo profile.)

---

## 1. The problem, and what Glasshouse is

A modern power-trading team — a 3-20 person startup, a BESS or VPP operator, a renewables portfolio manager — lives in a gap. On one side: spreadsheets, a hand-rolled trade table, a P&L notebook, a Streamlit dashboard, and growing dread about what happens when an investor, lender, auditor or regulator asks how a number was produced. On the other side: heavyweight vendor ETRMs with multi-year implementations, customisation lock-in, opaque internals and pricing to match, or vendor-lite SaaS that is still a black box with an API. The research behind this document found the complaints about legacy systems are proxy complaints about architecture; it also found the buying pattern has shifted to modular, hub-and-spoke stacks, which means a well-bounded open core does not need to displace anything to be adopted.

**Glasshouse is an open-source ETRM core**: a production-shaped, self-hostable, inspectable, extensible system of record for European power operations — books, counterparties, trades, amendments, official curves, positions, P&L, corrections, settlement, and an audit history good enough to hand to an auditor. Apache 2.0. PostgreSQL. A documented HTTP API. CSV in, CSV out. No vendor lock-in, no black box.

The promise to the adopter, in one sentence: *you get the six months of ETRM skeleton you were about to build in-house, plus an audit and corrections regime you would not have built — and you can read every line of it.*

The architecture, in one sentence: **a governed operational ledger for power trading, where every write is a proposal, every accepted proposal is a transition, every large artefact is hash-anchored, every operational view is replayable, and every number can be explained.** Everything else in this document is implementation of that sentence.

**What it is not**: an optimiser, a forecasting system, a tick store, a data platform, a compliance-certified reporting system, or (initially) a gas/multi-commodity system. Heavy data lives in purpose-built stores downstream; Glasshouse is the book of record.

## 2. Who adopts it, and why

- **The power-trading startup CTO** (3-20 people, strong Python team, messy spreadsheets, intraday/forward exposure). Adopts because it beats the internal hack they were about to write: a sane domain model, migrations, tests, OpenAPI, import templates, Docker deployment — and audit discipline before institutional capital or the regulator asks for it.
- **The BESS / VPP operator** — probably the strongest wedge. They already have optimisers, telemetry, settlement files and revenue reports, but no clean book-of-record layer between the spreadsheets and a vendor ETRM. Glasshouse is the open operational ledger for asset-backed power trading: the optimiser stays external; the lifecycle events that make its numbers legally meaningful land here.
- **The PPA / renewables portfolio manager**: contract structures, merchant-vs-contracted attribution, pay-as-produced logic, curve versions, and the auditability their investors and lenders increasingly demand.
- **The consultancy / system integrator**: an open core to base client implementations on. The client buys an inspectable foundation plus implementation services, not a black box — a shape that fits how this market actually buys.
- **The risk / finance controller** (the internal champion): the one who has to defend the numbers, and for whom "every number can defend itself" is not a slogan but a job description.

## 3. Why adopt instead of build?

Any good Python team can build a trade table, a curve table, and a P&L notebook. What they get from Glasshouse instead:

- a canonical domain model for European power (market areas, products, delivery periods at native 15/30-minute granularity, books, counterparties) that has already absorbed the boring decisions;
- positions and P&L by delivery period, by book, against the **official** curve as of any business date — the killer query, available on day one;
- a corrections regime they would not have built: amendments and curve corrections **supersede, never overwrite**; the original stays on the books with its standing retracted; "what did we know at 10:00 last Tuesday, and under which curve?" is a query, not a forensic project;
- lawful rejection as a first-class outcome: the system refuses to commit state that breaks the rules, and can *explain* why, in business terms;
- import/export that respects reality (CSV trades and curves in; positions, P&L and audit evidence out);
- tests, CI, OpenAPI, Docker — the boring reliability that makes Monday morning work.

The first six items are the spine. The third and fourth are the part nobody builds in-house and no vendor makes inspectable — Glasshouse's unfair advantage (see §6).

## 4. Position in the 2026 market

From the market research behind this document (June 2026): the ~USD 2bn/yr ETRM market is consolidated incumbents (ION's Endur/Allegro rollup, FIS, SAP) and a small set of cloud challengers (Molecule the only genuine multi-tenant SaaS archetype). Glasshouse does not compete on feature-completeness with any of them and should never claim to. Its wedge is what none of them are: **open, self-hostable, inspectable, extensible, European-power-native, and audit-first by architecture**. The precise claim, worded carefully: *there is no mature, audit-first, lifecycle-complete open-source ETRM core for European power* (adjacent artefacts exist — an R analytics package, Open Source Risk Engine, vendors' OSS tooling — none is this).

The 2026 feature bar the research established, and which Glasshouse treats as table stakes, not differentiators: native 15-minute Market Time Units (SDAC since 01/10/2025; 96 periods/day) alongside GB's 48 half-hours; PPA structures with merchant-vs-contracted attribution; BESS as asset-linked trading where the ETRM is the book of record and the optimiser stays external; corrections and as-of reporting; structured, exportable auditability (the direction REMIT II — Regulation (EU) 2024/1106 and its 2026 implementing layer — is pushing the whole industry; Glasshouse ships a REMIT-*shaped* export as an integration aid, explicitly not compliance-certified).

## 5. Product scope

### Core v0 — the Power Trading Core (the first real release; a small desk can run a small book on it)

- Organisations and **books** — the only partitioning concepts, and **the organisation is the tenancy boundary**: every row carries its org, isolation is structural from day one, and demo orgs, student sandboxes (v3) and client environments are all just organisations. No second "tenant" layer; legal-entity structure inside a group is later domain modelling, not partitioning. There is no separate "scenario": the simulator layer is an org factory, one organisation per student. Desks are deferred (a book-grouping label, added when someone asks). The business date is a per-organisation governed claim.
- Counterparties and minimal contract reference data (legal entity, contract type, broker/platform, settlement currency, delivery area).
- Market areas, products (base/peak/quarter-hour/half-hour blocks), delivery calendars, DST-correct period handling (periods are UTC instants; 92/100 and 46/50-period days fall out correctly).
- Trade capture and effective-dated amendment; CSV trade import with quarantine of bad rows (never silent coercion).
- Curve versions with **official standing**: registering a curve version admits its identity (market, as-of, version, input hash, shape-profile version) and grants `official`; correction supersedes without erasing. Bulk price arrays live in the app schema keyed by the registered identity.
- Positions by delivery period, by book; unrealised P&L against the official curve; recompute on correction, with both versions reconstructable.
- Audit and as-of: every lifecycle event attributable (who, under what authority, when); as-of reconstruction by timestamp; per-number evidence export (the "evidence pack": the claims, the curve identities, the rejections, the lineage for any figure).
- Authority without an RBAC subsystem: app-side auth is identity only (login maps a user to an actor string); what an actor may do is **governed capability claims in the ledger** (`may_capture_trade(actor, org, book)`, `may_correct_curve(actor, org, market)`, ...), so permission changes are audited like trades and "who could approve corrections last March?" is an as-of query. **The ledger governs capabilities; roles are convenience bundles, not first-class law** -- trader / risk / market-data operator / admin / read-only auditor ship as seeded capability bundles in app setup, and a BESS operator or PPA manager can bundle differently without touching the rule model. Maker-checker is then a two-step transformation pattern, deferred cleanly to v1.
- HTTP API with OpenAPI; CSV/JSON export; Docker Compose deployment; seed data and import templates; settle-grade CI.

**Build order within v0** (engineering sequence, not scope): the vertical needle first — one trade, one official curve, one MTM that is only admissible against an official curve, one correction that supersedes, one as-of query — then widen to the full spine around it. The needle proves the core regime early; the spine makes it adoptable. **The hosted demo ships off the needle plus imports, not off the full spine**: org/book/counterparty, one product family (fixed-price physical power), CSV trade and curve import, positions, MTM, correction, as-of, evidence drawer. Everything else in the v0 list above — richer products, amendments polish, the full capability seeding, exports — widens *after* the demo is live, so the first public artefact does not wait on the last reference-data decision.

### The hosted demo milestone (on the needle, inside v0)

A public demo on Render (§13) telling **exactly one operational story** — the Monday-morning workflow: import trades and curves, view positions and P&L by delivery period by book, correct a curve, and inspect *why* the P&L changed, down to the evidence. Server-rendered UI (§12), six screens, demo login, nightly seed/reset. The hard rule that protects scope: everything in the demo serves that one story; anything that does not, waits.

### v1 — operate a real book
Settlement support and confirmation workflow; maker-checker enforced; fees/costs; PPA pay-as-produced as a first-class trade type with attribution; BESS asset-linked trades (asset registry, charge/discharge legs as trades; no optimisation); richer permissions; reconciliation primitives (statement import, match/break states).

### v2 — surfaces and integrations
UI build-out beyond the demo's six screens (corrections workbench, counterparty/book admin, reconciliation views); REMIT-shaped export (clearly labelled non-certified); adapters where licensing permits (exchange/broker file formats, ENTSO-E data); optimiser-output and EMS-data connectors (the BESS/VPP wedge made concrete); anomaly-detection hook on the event stream.

### v3 — the layers above
The Educational ETRM Simulator (etrmsim) rewritten as a thin layer over Glasshouse's API (auth, pedagogy, presentation; student sandboxes map onto books/orgs); multi-commodity extensions if and when pulled; plug-in valuation engines.

**Extensibility doctrine: product packs, not commodity expansion.** The core knows about the four primitives plus books, counterparties, authority and time — and nothing about power periods, PPA structures, BESS legs, gas days or certificate vintages. Product knowledge (vocabulary, capture fields, delivery-period expansion, valuation inputs, projection folds, import formats, evidence templates) lives in a clearly bounded module per product family: a **product pack**. v0 ships exactly one, `power-fixed-price`, built in-tree with the boundary observed but no plugin machinery; the pack *interface* is extracted when the second pack (PPA pay-as-produced or BESS asset-linked, v1) forces it — the same forced-by-example discipline the substrate uses. The boundary is doctrine from day one; the framework waits for the second customer of it.

## 6. Architecture

**Four primitives.** Glasshouse has four primitives: **proposals**, **transitions**, **payloads**, and **projections**. Proposals ask for state to change (capture a trade, amend it, register a curve, correct one, admit an MTM, grant authority, advance the business date). Accepted proposals become transitions: the atomic historical facts — who proposed what, under which rules, at which time, with which result. Large or bulk content (curve arrays, import files, statements, evidence artefacts) is not governed row-by-row; it is hash-anchored as payloads. And everything users query — blotter, positions, P&L, curve views, audit views, import status — is a projection, rebuildable from the transition log and the payload store. That is the whole system; the zones below are how it is implemented.

Three zones, one law: the unit of governance is the lifecycle event, not the data volume.

```
                +---------------------------------------------------+
  HTTP / CSV    |  COMPUTE (Python)                                  |
 +-----------+  |  curve store - shaping - validation - MTM - P&L    |
 |  FastAPI  |->|  results proposed back as governed claims          |
 |  adapter  |  +-------------------------+-------------------------+
 +-----+-----+                            |
       v                                  v
 +-----------------------------------------------------+
 |  COMMIT (the governed core)                          |
 |  lifecycle admission: trades, amendments, authority, |
 |  curve officialness, MTM admissibility, corrections  |
 |  that supersede; full audit; as-of reconstruction    |
 +-------------------------+---------------------------+
              | transition log (tailed)       | outbox (leased)
              v                               v
 +-------------------------------------+  +-------------------------+
 |  READ (PostgreSQL app schema)       |  |  EXTERNAL EFFECTS       |
 |  projections: blotter, positions,   |  |  webhooks, settlement   |
 |  P&L (a pure fold over the log);    |  |  instructions (later)   |
 |  analytics platforms downstream,    |  +-------------------------+
 |  never the control plane            |
 +-------------------------------------+
```

**The read-side law (the design's quiet superpower).** Every table in the app schema is one of exactly two things: a **projection** -- derived state carrying the transition id it came from, rebuilt at any time by replaying the log from zero -- or a **hash-anchored payload** -- bulk content (the curve price arrays) whose hash was admitted in a governed claim. Projection schema changes are therefore handled by rebuild and backfill rather than manual historical mutation; deploying one is still a deploy (DDL for the new shape, a replay, a verified cutover), but it is never an archaeology project, because the ledger, not the projection, is the history. (TimescaleDB continuous aggregates are simply projections maintained by the database instead of the projector -- derived and rebuildable, so they obey the same law.)

### Self-verification

Because both read-side categories are mechanically checkable, the law yields a product pillar, not just a discipline: **`glasshouse verify`** (six independent legs) replays the projections and compares checksums, re-hashes the payloads against their admitted hashes, checks the deployed model hash against what the ledger was committed under, checks the **Merkle history tree** (Morpholog's tamper-evidence: the checkpointed prefix is consistent and unrewritten, anchored by `glasshouse checkpoint`), confirms the official inspection views still name the programme, and reports orphans and missing payloads. A portable evidence pack (`glasshouse evidence-export`) recomputes the same Merkle root **offline, with no database** (`glasshouse evidence-verify`). The promise it makes is concrete and rare: *you can prove, on demand, that the operational database still agrees with the governed ledger.* No ETRM ships that; here it falls out of one sentence of discipline, and it sits alongside import, P&L, corrections and audit as a headline capability.

**Projections tail the transition log, not the outbox.** Projection is an internal, idempotent fold; the outbox's lease-and-retry machinery exists for *external* delivery and is reserved for it. The projector is a library with three run modes chosen by config -- inline after each write, background thread, or separate worker -- so local dev and the demo can run **single-process plus Postgres** (`docker compose up` and you are running), while real deployments split the worker out.

**Projections are the primary read model; generated per-predicate SQL views are the official inspection model.** Operational screens and the API read projections. For direct lookups into governed facts -- audit, reference, BI -- Morpholog generates `CREATE VIEW` per predicate with the declared column names, versioned with the model and stamped with its hash, so `SELECT * FROM current_official_price WHERE trade = 't1'` is officially supported and breaks loudly (at view regeneration) rather than silently when a predicate changes shape. Never raw JSONB. One nuance keeps the build unblocked: the views generator is upstream work in flight, and v0 does not depend on it -- typed decoders from the schema manifest carry the inspection role until the views land.

**The governed commit layer is [Morpholog](https://github.com/jordan-dimov/morpholog)** — and this is deliberately an implementation detail, not the headline. Users interact with a normal HTTP API, PostgreSQL projections, CSV imports and documented workflows. Morpholog is the reason the audit trail is unusually good: invalid state is structurally uncommittable; every committed event records the actor and authority; corrections supersede with full history; as-of is native. Adopters do not need to learn it on day one; their developers find it when they ask "why is this part so trustworthy?", and extend the rule model when their business demands it. (One rule is absolute and inspectable in the code: **writes to governed state only ever go through the commit layer** — no ORM writes, no exceptions.)

**The killer query** the architecture exists to serve: *show me my position and P&L by delivery period, by book, using the official curve as of this business date* — and, when challenged, *show me the evidence for that number*. P&L is why teams adopt; the audit regime is why they trust it.

### The Morpholog integration surfaces (summary; full contract in `docs/morpholog-integration-contract.md`)

Glasshouse depends on Morpholog for governed commits, and needs five integration surfaces, all agreed with upstream (06/06/2026): **batch proposal execution** (`run --batch` -- each row its own transition, never all-or-nothing), a **canonical model hash** (rules-identity over canonical source), a **schema manifest** (one artefact feeds all codegen and drift-checks), **generated per-predicate SQL views** as the inspection surface (upstream's improvement on our filtering ask), and eventually **pattern-based authority** (parked; our capability model is the forcing example). Delivered and adopted since: the model hash and schema manifest (07/06/2026), claim disciplines (09/06/2026), and `run --batch` plus the generated stdlib Python client -- codecs, envelopes, the subprocess adapter and typed models emitted by the binary itself, replacing Glasshouse's whole hand-rolled integration layer (11/06/2026). Evidence-pack manifest extension points and the hash-chained audit log are coordinated; PG18 is in upstream CI. **v0 blocks on none of the rest** -- it builds against current Morpholog and adopts each surface as it lands.

## 7. The API contract

- Friendly, conventional REST surface (`POST /trades`, `POST /curves`, `GET /positions`...), because adopters want a normal API. The *semantics* are admission, and the docs teach them: a write is a proposal; acceptance commits an event; rejection is a lawful, documented outcome (with a structured reason and an `explain` endpoint that answers "what would make this admissible?"), not an error.
- **Generated, typed, checked -- not magically dynamic**: the morpholog binary generates the whole typed client (`morpholog generate python-client`: codecs, envelope models, the subprocess adapter, a request model per transformation, a read model per predicate), stdlib-only, committed to the repo and drift-checked by regenerating against the live binary. Pydantic lives at the HTTP boundary, where API request models are built *from* the generated types (org from auth context, actor from session, never from a request body). The API, its validation, its OpenAPI docs and the governed rule contract come from one source; runtime-dynamic generation is deferred until ergonomics clearly demand it.
- The generated client's codecs handle the wire format both directions, including outbox intent payloads, validating and never coercing. Money and quantities are Decimal from strings, never via float.
- Typed outcomes: the client returns `Committed | Rejected` as typed envelopes and raises on operational failure — the two most likely integration bugs (rejection-vs-failure, gate-vs-question) made unrepresentable.

## 8. Stack

Python **3.13+** (CI matrix 3.13 and 3.14; the floor is adoption-friendliness, the ceiling is dogfooding) · uv (locked) · ruff · mypy (blocking) · pytest + Hypothesis · FastAPI + Pydantic v2 · SQLAlchemy 2 Core (governed state read only via the generated per-predicate views; full on app schema) + psycopg3 · Alembic (app schema only) · structlog (JSON, run-id via contextvars) · **PostgreSQL 18 floor; CI, compose and the demo run the same TimescaleDB image** -- 18's async I/O and B-tree skip scan help the period-windowed position reads, and data checksums (on by default at initdb) complement `glasshouse verify` at the storage layer; with the `position_hour` hypertable, TimescaleDB is now load-bearing, so the floor is simply the substrate the app actually uses. + **TimescaleDB (a dependency, not an option)**: `position_hour` is a hypertable today, using core chunk exclusion only. Compression for superseded curve versions (the supersede-never-overwrite discipline creates exactly the storage growth compression solves) and continuous aggregates for period-to-day rollups are future optimisations, not used yet -- they are TSL "community" features outside the Apache-2 subset, so they stay deliberately unused (see the Render note below) until a workload and a Timescale-capable host justify them. One code path, one set of migrations; the storage *law* (projection-or-payload) is independent of Timescale -- it is how the law is implemented efficiently, which is exactly why a single code path beats a dual "profile" nobody tests. Render managed Postgres supports the extension on PG18, but only its Apache-2 subset ("Community features are not available" per Render's docs), so the app uses *core* hypertables only -- chunk exclusion, no native compression or continuous aggregates; those TSL features, when a workload forces them, want a Timescale-capable host (Timescale Cloud or self-hosted). The Alembic migration runs `CREATE EXTENSION timescaledb`; self-hosters use the timescaledb image. Boundary agreed with upstream: Timescale sits **beside Morpholog, never under it** -- hypertables live in the app schema only; the morpholog schema stays plain PG primitives (its replay correctness rides on SSI + transition ordering, exactly the interaction chunking must not touch). One operational coupling to note: a shared instance means a shared extension-upgrade cadence; if BESS telemetry volume grows serious, a separate Timescale instance fed by the outbox keeps the governed core's operational profile boring · the `morpholog` binary via multi-stage Docker (Rust builder stage; `GLASSHOUSE_MORPHOLOG_BIN` for dev) · outbox worker + projector as a second process · docker-compose for the full stack · GitHub Actions running format, lint, types, tests, plus an integration job that builds a pinned morpholog ref against the compose TimescaleDB image and runs the full suite live (the client drift contract, the needle lifecycle, the projector's rebuild-from-zero) under a coverage gate.

## 9. Production readiness ladder

Honest, and part of the product: adopters need to know where they stand.

- **L0** — local dev / evaluation (compose up, seed data, run the workflows).
- **L1** — internal pilot (your data, your team, no reliance).
- **L2** — shadow book: Glasshouse runs alongside the spreadsheets or incumbent, reconciled daily. *This is the designed adoption path*, and the product supports it explicitly: import the incumbent's or the spreadsheet's exports, compare Glasshouse P&L against the existing P&L, show the breaks, resolve the mapping issues, export the reconciliation report. A startup may adopt directly; a scaleup will shadow first — the imports workbench and the reconciliation primitives (v1) exist for the scaleup.
- **L3** — operational book of record for a small portfolio.
- **L4** — controlled production with independent review of the deployment and the rule model.
- **L5** — supported enterprise deployment (with an implementation partner).

A `SCOPE.md` in the repo states plainly what Glasshouse is not (not certified for regulatory reporting; not an optimiser; not a price source; not advice) and maps features to ladder levels, so nobody mistakes ambition for certification.

## 10. Commercial engines

- **Engine B — implementation and services** (the realistic early path): A115 and partner consultancies sell implementation, migration, customisation, rule-model extension and support around the open core. Every engagement leaves the core better and a referenceable deployment behind.
- **Engine C — embedded platform**: A115 runs its own commercial products on Glasshouse — its BESS revenue verification work uses Glasshouse as the operational ledger underneath. Dogfooding is the strongest signal an open core can send: the maintainers bet their own deliverables on it.
- **Engine A — hosted SaaS**: deliberately *not* initially. Self-hostable and implementation-friendly first; a managed offering only if pull demands it.

Secondary benefits that cost nothing and are not the identity: Glasshouse is also the living companion to the ETRM System Architecture guide and the A115 curriculum, the substrate for the educational simulator (v3), and Morpholog's first real embedder. These are consequences of building it well.

## 11. Demo assets (sales, not ceremony)

- **The golden evidence pack**: a directory any developer, auditor or investor can open — admitted claims, rejected proposals, curve identities v1/v2, MTM results, the as-of report, the lineage graph. More persuasive than any UI, shipped from v0. The format is a versioned, content-addressed manifest whose hashes chain to the ledger and the payload store, so a pack is verifiable offline (`glasshouse verify-pack`) -- and it is designed once, in coordination with Morpholog's WP5 audit-pack work, because the same format is the deliverable shape of A115's verification engagements (engine C made concrete: one pack format, three products). The extension contract is pinned with upstream (06/06/2026): entries of `{role, hash, media_type, locator?}` chained to the ledger by transition ids; full offline verifiability strengthens further when Morpholog's hash-chained audit log lands (Glasshouse is its forcing pressure, and it has moved up the upstream queue). One pack, two faces: the **machine-verifiable bundle** (manifest, hashes, transition ids, model hash, payload locators) is the source of truth, and the **human-readable report** (HTML/PDF: the trades, the curve versions, the valuation explanation, what changed, the exceptions, a sign-off section) is a rendering of it -- auditors and investment committees consume the report; `verify-pack` consumes the manifest. Nobody is handed a content-addressed manifest and told it is the deliverable.
- **The Tuesday curve correction** (as an executable example): trade captured; curve v1 official; MTM admitted; curve corrected to v2; v1 superseded but not erased; MTM against v1 now rejected, with the explanation; as-of 10:00 yesterday returns the v1 world; as-of now returns v2. Ten minutes, four audiences (trader, developer, auditor, student), one story.
- **The Monday morning script**: import trades.csv and curves.csv, get positions and P&L by period by book, correct a curve, watch the P&L move *and be able to say exactly why*.

## 12. UI and operator experience

Server-rendered **Jinja2 + HTMX** (Alpine.js permitted only for tiny local interactions), no SPA, no React, no Node build step. CSS: **custom properties + a small hand-written component layer** (tokens, base, layout, and per-component files for table/badge/drawer/forms), not Tailwind -- a control room needs a small disciplined design system kept coherent in one place, not utility classes scattered through templates. For a product that is tables, filters, forms, workflow states, drill-downs and evidence views, server-rendered HTML is not the compromise; it is the correct architecture, and it keeps the UI an honest projection of the backend workflows. The product is API-first but not API-only.

**The aesthetic, named: Glasshouse Control Room.** Dense but calm; operations-grade, not SaaS-dashboard; light mode first; neutral palette with one restrained accent; compact rows, tabular numerals (`font-variant-numeric: tabular-nums`) everywhere money or quantity appears; negative values and breaks unmistakable; no gradients, mascots or decoration. The UI should whisper *you can trust me under pressure*, not *look how modern I am*.

**The six screens** (demo scope; everything else waits):
1. **Overview** — the operational landing page, answering "can I start my day?": business date, books, latest official curves, open import quarantines, recent corrections, latest P&L run, system health.
2. **Blotter** — the centrepiece: dense filterable table, sticky header, row drawer with the full amendment trail, export-current-view. No editable cells.
3. **Curves** — market-data operations, not a generic list: version, official status, input hash, shape profile, supersedes chain; the money interaction is *select v1 and v2, see what changed and which books' P&L it touched*.
4. **Positions & P&L** — the killer query as a screen: book x delivery period x net MWh x curve used x P&L, groupable, as-of selectable, every material number one click from its evidence drawer.
5. **Imports** — the adoption workbench: upload CSV, preview, validate, quarantine with reasons, accept the good rows, download a corrected template. A prospect should be able to bring their own spreadsheet to the demo.
6. **Audit / Evidence** — a **number-defence page**, not a raw event log: for any figure, the value, the trades, the curve version, the source imports, the lifecycle events, any relevant rejections, the actor and authority, and the evidence-pack export.

**Four UI laws.** (1) **As-of mode is global and visible**: the top bar always shows `Viewing: Current` or `Viewing as of: …`, with a persistent banner in historical mode — most systems bury as-of; Glasshouse leads with it. (2) **Every write surfaces one of three outcomes** — Committed, Rejected (lawful, with the explanation one click away), Failed (operational) — and the distinction is visible everywhere. (3) **No silent grid mutation in a governed system**: explicit proposal forms, server-side filters and pagination, CSV export; a heavyweight grid component only if real users later demand it, and locally, never as an excuse to go SPA. (4) **Every screen is a rendering of a public API query** -- UI views and the JSON API share one query layer, no private UI queries; the UI is the standing proof that the API suffices. (5) **Every material number is explainable in place** -- not merely clickable through to an audit page somewhere else: hover or drawer gives the quick explanation (curve version used, trade count, last correction time), one click gives the full evidence, export gives the pack. The number defends itself where it stands.

A tiny internal design system from day one (`PageHeader`, `FilterBar`, `DataTable`, `StatusBadge`, `Money`/`Quantity`/`PeriodLabel`, `AsOfBanner`, `EvidenceDrawer`, `ProposalResult`, `ImportPreview`, `CorrectionDiff`), with one restrained, consistent status palette (committed / rejected / superseded / official / draft / quarantined / break / reconciled).

## 13. Hosted demo deployment (Render)

Deployed from a **`render.yaml` Blueprint from day one**, but the demo profile is deliberately minimal — the projector's run modes (§6) exist precisely so the demo does not have to prove distributed deployment:

```
Demo profile (public demo, and local dev):
  glasshouse-web      Docker web service (FastAPI + UI + background-thread projector)
  glasshouse-db       Render Postgres 18 (managed)
  seed-reset          nightly cron job

Production-like profile (documented, exercised in CI, not the demo):
  glasshouse-web + glasshouse-worker (outbox + projector) + Postgres
```

The worker joins a deployment when external effects or projection lag force it; the demo has neither. Fewer moving parts, fewer failure modes, and consistent with projection being an internal idempotent fold.

- One multi-stage Docker image (Rust builder stage produces the `morpholog` binary; Python runtime copies it in); web and worker run the same image with different commands.
- Migrations and the Morpholog schema bootstrap run in a **pre-deploy command**, never at app startup.
- Internal/private-network database URL for app-to-Postgres traffic; environment groups for config shared across web and worker.
- `/healthz` (liveness) and `/readyz` (database connectivity + morpholog binary + a commit-layer round-trip).
- Seed/reset as a one-off or cron job: the demo resets nightly to a known book.
- **Demo login** (simple auth): public financial-looking demos attract junk traffic and accidental misuse.
- Postgres 18 is the floor; the full suite runs green against the PG18 TimescaleDB image, in CI and locally.
- The demo is **readiness-ladder L0** and says so on screen; no production claims anywhere.

## 14. Guardrails

- **Commercial pull beats elegance, with one exception**: the writes-only-through-the-commit-layer rule and the supersede-never-overwrite rule are non-negotiable; they are the product.
- **Simulator pull**: the educational layer (v3) consumes Glasshouse; its conveniences (fixtures, forgiving flows, scenario magic) must never leak into the core. Core stays strict and boring.
- **Morpholog stays second-level publicly** — which also serves Morpholog's own doctrine (its identity is the general evidence-regime substrate, not "the ETRM engine"); the morpholog repo's next worked example remains the EU AI Act one. Friction discovered by Glasshouse is reported upstream as issues, never worked around silently.
- **Regulatory precision**: REMIT II is Regulation (EU) 2024/1106 with a 2026 implementing layer; secondary-sourced dates (e.g. quarterly forward-exposure reporting timing) are not made load-bearing anywhere without the primary text.
- **Performance honesty**: the commit layer is measured (~1.6s/commit on a 100k ledger; latency harness in-tree upstream) and right for book-of-record volumes; the concurrency law (govern lifecycle events, not ticks) is the stated answer to "will it scale", and the numbers are published, not hidden.
- **No graveyard**: the first release is narrow but genuinely usable (the Power Trading Core), not a demo, not an architecture sample. Estimated honestly: v0 is 2-3 weeks of focused pair-programming, not one.

## 15. Open items

- ~~Smoke-test Morpholog against PostgreSQL 18~~ -- resolved upstream: PG18 joins Morpholog's CI matrix as `{17, 18}`.
- ~~Smoke-test `CREATE EXTENSION timescaledb` on Render PG18~~ -- Render managed Postgres supports it on PG18 (Apache-2 subset only); the migration provisions it and `position_hour` is a hypertable. One live confirmation that `create_hypertable` succeeds on the chosen plan stays worthwhile before the demo leans on it at scale.
- TimescaleDB licensing note for SCOPE.md: the community (TSL) features Glasshouse uses (compression, continuous aggregates) are free for self-hosting and for running your own product, not for offering TimescaleDB itself as a service; some managed Postgres providers offer only the Apache-2 subset or none. Render supports it; adopters on hosts without it self-host the timescaledb image.
- Subject/ID conventions (UUIDv7 minted app-side; symbolic identities for market vocabulary, e.g. `curve:DE_POWER:2026-06-02:v2`) — fixed early; they are the public vocabulary.
- ~~Organisation-level multi-tenancy~~ -- resolved: the organisation IS the tenancy boundary, structural from day one; single-org deployments are the assumption until pulled.
- ~~CSS approach~~ -- resolved: custom properties + small hand-written component layer, no Tailwind, no Node.
- Name the repo's docs set from day one: `docs/00-what-glasshouse-is.md`, `01-domain-model.md`, `02-api-contract.md`, `03-corrections-and-audit.md`, `04-evidence-pack.md`, `morpholog-integration-contract.md` (exists), `SCOPE.md`, `CONTRIBUTING.md` (aimed at senior energy technologists, programme alumni, integrators, and embedder authors — not drive-by OSS traffic).
