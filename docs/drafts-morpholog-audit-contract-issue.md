## The worked example you were waiting for

`docs/embedder-integration.md` deliberately leaves the audit surface open: "the claims shapes (bare and `--named`) are pinned above; `audit`, `outbox`, `derived`, and `guarantees` vary and earn their own contract entries when an embedder leans on them."

Glasshouse now leans on it. The projector (`glasshouse.projections`, shipped 11/06/2026) is the read side of the ETRM: it tails `morpholog.audit` ordered `(committed_at, transition_id)`, folds each transition's asserted/retracted claims into projection tables (blotter, hourly positions, valuations), each row carrying its source transition id, and proves the read-side law by rebuilding from zero and comparing. It decodes audit rows with the same codecs the generated client ships (tagged args via `ClaimInstance`/`decode_tagged`).

## What it relies on today (read directly from the table)

- The causal order `(committed_at, transition_id)` for replay and the cursor (`WHERE (committed_at, transition_id) > (:c, :t)`).
- Row fields: `transition_id`, `asserted_claims`, `retracted_claims`, `committed_at` (it does not currently read `arguments`, `actor`, `invariants_checked` or `emitted_intents`, though a number-defence screen will want `actor` and `transformation_name` soon).
- The claim shape inside those JSONB arrays: `{predicate, args}` with tagged args - the same shape the run envelope carries.

## The ask

Pin the audit read contract, in whichever form fits the substrate's doctrine:

1. A contract entry for the table itself (column names, the claim JSONB shape, the `(committed_at, transition_id)` ordering guarantee, what is stable vs reserved), or
2. A blessed CLI surface, e.g. `inspect audit --after <committed_at>,<transition_id> --named <file.morph>` emitting NDJSON transitions - which would also fold into the generated client.

Either unblocks downstream projectors from depending on an undocumented surface; (2) additionally gives them the named decode for free. No new semantics requested: the data and the ordering already exist and are already what `morpholog verify` replays.

Recorded as section 12 of Glasshouse's `docs/morpholog-integration-contract.md`.
