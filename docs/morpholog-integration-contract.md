# The Morpholog integration contract

Glasshouse depends on [Morpholog](https://github.com/jordan-dimov/morpholog) as its governed commit layer. This document records the integration surfaces Glasshouse needs, the upstream verdicts on each, and the coordination agreements. It is the working coordination record; the product-level summary lives in `DESIGN.md` §6.

Asks filed 06/06/2026; upstream verdicts received same day. Upstream delivery order: (2+3) as one PR, then 1, then the views generator (in place of 4), then the hash-chained audit log — all after the in-flight time-arc PR (Timestamp/Duration + laytime example) lands. **Glasshouse v0 does not block on any of these**: it builds against current Morpholog (per-call CLI, N `schema` calls, projection-primary reads with typed decoders) and adopts each surface as it lands.

Binary surface check, 07/06/2026 (morpholog-cli 0.0.1): none of surfaces 1-4 had landed yet; `morpholog verify` (audit-log replay diffed against the claims table, exit 0 consistent / 1 divergent) already exists, and is the upstream leg `glasshouse verify` composes with for ledger self-consistency.

Updated later the same day: surfaces 2+3 (`morpholog hash`, `schema --all`) merged via upstream PR #124, and asks 6-8 below merged via PR #125; all five verified against the rebuilt binary and adopted in `glasshouse.commit`. Still pending upstream: `run --batch` (1), the views generator (4), the hash-chained audit log.

## 1. `run --batch` — accepted, narrowed from the `--stdio` ask

NDJSON proposals in, one per line: `{transformation, actor, args_named}` (actor *per row*, not a flag — an import file carries mixed provenance); the pinned per-proposal outcome envelope out, order-preserving; continue-on-rejection by default with `--fail-fast`; one parse+validate and one connection pool per batch.

**Semantics pinned explicitly: each row is its own SERIALIZABLE transition.** A batch is N independent proposals with amortised transport, never an all-or-nothing import. Atomic multi-trade admission would be one governed transition with many statements — a semantics question, to be raised separately if ever genuinely forced. Glasshouse's Imports workbench is designed to this: partial success is the normal outcome, surfaced per row.

Acceptance: the upstream embedder-latency harness.

## 2. `morpholog hash` — accepted, mechanism corrected

The hash is over the *canonical source* (`format(parse(source))`, the round-trip property as canonicaliser), not internal IR — so the hashed artefact is a human-inspectable `.morph` file, and the hash is **rules-identity, not file-identity** (formatting and comments vanish). Exactly the right semantics for `ruleset_version` in curve identities and evidence packs, and for a deployment pinning "built against model hash X".

## 3. `schema --all` manifest — accepted, merged with 2, improved

One JSON artefact: `{model_hash, program, transformations, intents, predicates}` — predicate declarations included, so the read side's field-name decoding is fed by the same manifest. One codegen input for the generated Pydantic models and typed decoders; one CI drift-check artefact.

## 4. `inspect claims --where` — deferred in favour of generated per-predicate SQL views

Upstream generates `CREATE VIEW` per predicate with declared column names, versioned with the model and stamped with its hash. This replaces what would have been raw positional-JSONB reads on Glasshouse's side (which break silently when a predicate changes shape): **the generated views are the official inspection surface for governed state** — audit, reference lookups, and the adopter BI story in one move. Glasshouse is the forcing example for the generator. Projections remain the primary read model; the views are the inspection model.

## 5. Higher-order authority (WP2) — enthusiastically flagged, parked

Glasshouse's ledger-resident capability model ("who could approve corrections last March" as an as-of query) is the worked example that must arrive *before* the WP2 design: patterns over transformation names are claims-about-rules — constitutionally novel territory, example-first. The per-capability grant boilerplate Glasshouse accumulates is precisely the pressure pattern-based authority needs to see.

## Asks 6-8: drafted and delivered 07/06/2026 (upstream PR #125, merged)

Surfaced by building `glasshouse.commit` against the real binary; each named the business shape that forced it, per the substrate's own doctrine. All three were delivered the same day in PR #125, verified against the rebuilt binary, and adopted in `glasshouse.commit`.

### 6. Schema provisioning from the binary (`morpholog init`) — delivered

The ask: the binary could not initialise its own database; a fresh database needed `crates/morpholog-core/sql/schema.sql` from a source checkout at the matching commit, and the Glasshouse Docker image (binary only) had no drift-checked provisioning path. Delivered better than asked: the canonical schema travels inside the binary (`include_str!`), day-zero only, refuse-or-skip on an existing schema (`--skip-if-exists` for re-runnable entrypoints), never drops, never migrates. Adapter surface: `MorphologAdapter.init()`.

### 7. Named claim args on the read surface (`inspect claims --named`) — delivered

The ask: `--args-named` made the write side bare and named while the read side stayed positional and tagged, so every embedder re-implemented zip-by-declared-order plus an arity guard. Delivered with a deliberate authority flip: the bare read keeps the claims table as authority (unknown predicate = empty); under `--named <file.morph>` the programme is the authority, so a requested-but-undeclared predicate fails before any database read and programme/database skew is a hard error naming both sides. Values are wire-true (decimals and dates stay strings); typing belongs to the generated per-predicate models fed by the `schema --all` manifest. Adapter surface: `read_claims`, now a thin pass-through; the hand-rolled decode is deleted (upstream deleted the worked embedder's identical helper in the same PR). The positional shape still recurs in run envelopes and the audit log; whether the projector wants the same treatment is a separate, separately-forced question.

### 8. Same-snapshot explanation on rejection (`run --explain-on-reject`) — delivered

The ask: the API promises every rejection a structured reason and an answer to "what would make this admissible?", and run-then-explain is two snapshots that can disagree under concurrent commits. Delivered with exactly the right semantics: the rejecting proposal hands back the scoped pre-state its gates evaluated, and the pure explanation engine runs against it in memory; rejection envelopes gain an `explanation` field in the `explain --json` shape; committed envelopes and exit codes unchanged; kernel errors and serialization failures excluded (no admissibility story). Adapter surface: `run(..., explain_on_reject=True)`, surfaced as `Rejected.explanation`.

## 9. The generated Python client lives in the binary (`morpholog generate python-client`) — filed 07/06/2026 as [morpholog#126](https://github.com/jordan-dimov/morpholog/issues/126)

### The forcing example, quantified

Glasshouse, the first external embedder, has now built the complete Python integration twice (once hand-rolled, once after adopting asks 6-8) and the remaining surface measures as follows: 612 lines of hand-written interface Python (codecs, envelope models, subprocess adapter, model generator) plus 665 lines of tests and golden captures defending them, around a 174-line `.morph` that is the only genuinely Glasshouse artefact at the boundary. Roughly 7x as much Python protects the boundary as there is content crossing it, and **none of those 612 lines is embedder-specific**: every one is mechanically derivable from artefacts Morpholog already owns.

- The tagged-value codecs (both directions) derive from the pinned codec contract; they vary only with the binary version.
- The envelope models (`Committed | Rejected`, the Explanation shape, named claims) derive from the pinned envelope contract; same.
- The adapter (command construction, the empty-stdout discrimination rule, `init`/`run`/`explain`/`inspect`/`hash`) derives from the pinned CLI contract; same.
- The typed request and read models derive from the `schema --all` manifest; they vary only with the `.morph`.

The two worked Python embedders (upstream's own and Glasshouse) have therefore converged on writing the same client - the bar that forced `inspect claims --named`.

### The ask

`morpholog generate python-client --out <dir>` (taking the `.morph`): the binary emits a complete, self-contained, typed Python client package - codecs, envelope models, the subprocess adapter, request models per transformation, read models per predicate - stamped with the binary version and the model hash. The same move as `init`: the schema travelled into the binary so a deployment provisions exactly what its build expects; here the *client* travels with the binary so an embedder talks exactly the contract its binary speaks. No PyPI release treadmill, no version skew by construction, no FFI, the subprocess contract unchanged underneath.

The embedder's whole integration becomes: generate, commit the package, drift-check in CI by regenerating (pure, given the committed manifest) plus one `hash` comparison against the live binary. Glasshouse's existing layer is, almost verbatim, a prototype of the output (and is offered as the seed); its golden envelope captures become the generator's own contract tests.

### What this forces alongside

- **`schema --result`** (currently reserved upstream, awaiting "a real consumer that needs to discriminate dynamically"): the client generator is that consumer - it wants to generate the outcome models from a machine-readable contract rather than have them hand-pinned from documentation.
- Nothing else: batch, views, the audit-log shape are all orthogonal and the generated adapter adopts them as they land.

### Non-goals, stated to keep the ask small

In-process bindings (PyO3/FFI) are explicitly not asked for: the ~9ms subprocess tax is not the pain, the hand-maintained glue is. Other languages (TypeScript) follow whenever their worked example arrives; the command name leaves room.

### Targeting (amended on the issue, 07/06/2026)

The generated client is **stdlib-only** (frozen dataclasses, Decimal/datetime from stdlib, validation emitted as plain code), so the dependency question vanishes rather than being answered, and the worked embedder keeps its stdlib-only property. The Python version is a **declared, enforced, CI-tested floor**: stated in the generated header, checked at import time, exercised in upstream CI at the floor, emitted as a conservative subset so the floor moves only deliberately. Consequence for Glasshouse at adoption: Pydantic moves to the HTTP boundary, where API request models are built *from* the generated types (org from auth context, actor from session, never from a request body) - DESIGN.md section 7's "generated Pydantic request models" sentence gets re-pointed at that boundary then.

Minor nit to bundle: PR #124's text promises manifest entries in declaration order; the binary emits the transformation and intent maps alphabetically. Byte-stable either way; the docs and behaviour should agree.

## Coordination agreements

- **Evidence-pack extension contract pinned now** (no waiting for full WP5): a content-addressed JSON manifest, entries `{role, hash, media_type, locator?}`, chained to the ledger by transition ids.
- **Hash-chained audit log moved up Morpholog's hardening queue**: Glasshouse's offline `verify-pack` is its forcing pressure; full offline verifiability of packs strengthens when it lands.
- **PG18**: rides upstream as a CI matrix entry `{17, 18}`.
- **TimescaleDB boundary**: Timescale sits **beside Morpholog, never under it**. Hypertables live in Glasshouse's app schema only; the morpholog schema stays plain PG primitives (replay correctness rides on SSI + `(committed_at, transition_id)` ordering — exactly the interaction hypertable chunking must not touch; TSL licensing is also a consideration for an Apache-2 substrate). Operational coupling: a shared instance means a shared extension-upgrade cadence; if BESS telemetry volume grows serious, a separate Timescale instance fed by the outbox keeps the governed core's operational profile boring.

## Standing rules

- Friction discovered by Glasshouse is reported upstream as issues, never worked around silently.
- No new semantics are requested casually: every ask must be forced by a concrete Glasshouse scenario (forced-by-example, the substrate's own doctrine).
- The Subject stays opaque; Glasshouse never asks Morpholog to understand power.
