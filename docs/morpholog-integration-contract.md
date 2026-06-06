# The Morpholog integration contract

Glasshouse depends on [Morpholog](https://github.com/jordan-dimov/morpholog) as its governed commit layer. This document records the integration surfaces Glasshouse needs, the upstream verdicts on each, and the coordination agreements. It is the working coordination record; the product-level summary lives in `DESIGN.md` §6.

Asks filed 06/06/2026; upstream verdicts received same day. Upstream delivery order: (2+3) as one PR, then 1, then the views generator (in place of 4), then the hash-chained audit log — all after the in-flight time-arc PR (Timestamp/Duration + laytime example) lands. **Glasshouse v0 does not block on any of these**: it builds against current Morpholog (per-call CLI, N `schema` calls, projection-primary reads with typed decoders) and adopts each surface as it lands.

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

## Coordination agreements

- **Evidence-pack extension contract pinned now** (no waiting for full WP5): a content-addressed JSON manifest, entries `{role, hash, media_type, locator?}`, chained to the ledger by transition ids.
- **Hash-chained audit log moved up Morpholog's hardening queue**: Glasshouse's offline `verify-pack` is its forcing pressure; full offline verifiability of packs strengthens when it lands.
- **PG18**: rides upstream as a CI matrix entry `{17, 18}`.
- **TimescaleDB boundary**: Timescale sits **beside Morpholog, never under it**. Hypertables live in Glasshouse's app schema only; the morpholog schema stays plain PG primitives (replay correctness rides on SSI + `(committed_at, transition_id)` ordering — exactly the interaction hypertable chunking must not touch; TSL licensing is also a consideration for an Apache-2 substrate). Operational coupling: a shared instance means a shared extension-upgrade cadence; if BESS telemetry volume grows serious, a separate Timescale instance fed by the outbox keeps the governed core's operational profile boring.

## Standing rules

- Friction discovered by Glasshouse is reported upstream as issues, never worked around silently.
- No new semantics are requested casually: every ask must be forced by a concrete Glasshouse scenario (forced-by-example, the substrate's own doctrine).
- The Subject stays opaque; Glasshouse never asks Morpholog to understand power.
