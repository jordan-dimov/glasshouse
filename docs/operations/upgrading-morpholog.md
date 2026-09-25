# Upgrading Morpholog: the database runbook

How a new morpholog release reaches each database Glasshouse uses. The code half of an upgrade (pin, regenerate, drift gates) is in the contract doc's standing rules and each re-pin's section. This page covers the data half. It was written for v0.0.10 → v0.0.11 (contract section 24) and is kept general where the procedure does not depend on the release.

## The rule that shapes everything

**A binary and the governed schema it writes must move together.** Migrations are forward-only and embedded in the binary (`morpholog migrate`). `init` provisions a missing schema and never alters an existing one. The skew fails differently in each direction, as measured at v0.0.11:

| Binary | Schema | What happens |
|---|---|---|
| new | old (unmigrated) | `propose` refuses by name ("the database schema is behind this binary ... run `morpholog migrate`"). `audit verify` and the audit tail fail with a raw SQL error (`column "arguments_hash" does not exist`). |
| old | new (migrated) | `propose` fails with a raw SQL error (`no unique or exclusion constraint matching the ON CONFLICT specification`). The old generated client also refuses new audit rows (`unknown key(s) ['parameters']`), so the projector stops. `migrate` refuses by name ("this database records migrations this binary does not know ... upgrade the binary"), so an old `glasshouse provision` fails too. |

Both directions stop **governed operations**: writes and every ledger read (the audit tail, `verify`, the curves and audit screens). They do not stop the whole service. Projection-backed screens (overview, blotter, positions) keep serving from the tables as they last stood, which is stale data rather than an error, so `/readyz` is the signal to trust, not whether pages load. No process may run an older binary against a database a newer one has migrated, and a window in which one does is an outage to declare, not a detail to hide.

## The databases

| Database | Owner | Lifetime | Upgrade path |
|---|---|---|---|
| Local dev DB (the compose `db` service, port 5432) | the developer | disposable | Run `glasshouse provision` with the new binary (it migrates), or drop the volume and start again. |
| Test DB (whatever `GLASSHOUSE_TEST_DATABASE_URL` names) | the developer | disposable | None. The live suite drops and re-`init`s the governed schema on every run. |
| CI `services:` Postgres (integration job, canary) | GitHub Actions | per run | None. Every run starts empty and `init`s with the pinned binary. |
| Render `glasshouse-db` (demo) | the maintainer | persistent, rebuilt nightly by `seed --reset` | The procedure below. **Ask before touching it.** |

The Render database is written by two services from the **same image**: `glasshouse-web`, whose `preDeployCommand` runs `glasshouse provision` (`init` if absent, then `migrate`, then the sealed views), and the `glasshouse-seed-reset` cron at 02:30 UTC. The cron drops the governed schema and re-`init`s it with **its own** binary. So if only one of the two is redeployed, the next reset or the next deploy puts the database and one of its writers on opposite sides of the table above.

## Procedure (demo database)

This is an **owned outage**, not a rolling upgrade. The demo is L0, and engineering zero downtime around a forward-only migration is not worth it here. Declare the window, keep every old consumer off the migrated schema as far as the platform allows, and say plainly where it does not.

Render's deploy history for both services shows no GitHub commit trigger ever firing (every deploy is a Blueprint sync, a manual API call or a platform restart), so both services are deployed by hand. Do it well clear of 02:30 UTC.

1. **Merge** the re-pin PR once CI is green on main.
2. **Look before changing anything.** From a machine with the new binary and the database URL:
   ```sh
   morpholog migrate --check --database-url "$DATABASE_URL"   # exit 1 lists what is pending
   ```
3. **Back up**, and know the restore works (see [Restore](#restore)): `pg_dump -Fc "$DATABASE_URL" > demo-$(date -u +%F).dump`.
4. **Open the window: suspend the cron** (Render API `POST /v1/services/<cron-id>/suspend`; the CLI has no suspend command, and this route has not yet been exercised on this project), so no reset can run with the old binary mid-upgrade.
5. **Deploy web**, pinned to the exact commit: `render deploys create <web-id> --commit "$(git rev-parse origin/main)" --wait --confirm`. The pre-deploy `provision` runs `migrate`; check its log for `migrated (...)`. **The one skew this procedure accepts**: on Render the migration runs while the old web instance still serves, so for the length of the deploy an old binary and its projector sit on the migrated schema. Governed writes and ledger reads fail and projection screens go stale, as the table above says. For the demo that is the declared outage. A deployment that cannot accept it must stop the old web before migrating and run the migration as its own step from the new image, then start the new consumers.
6. **Deploy the cron** and resume it: `render deploys create <cron-id> --wait --confirm`, then `POST /v1/services/<cron-id>/resume`. Cron services refuse `--commit` (HTTP 400) and build the branch head, so confirm the head is the commit web took. That check is a **manual L0 guard**, not a guarantee that both services run the same image. The production shape is build once and deploy the identical image digest to every consumer, which matters more than any migration tooling.
7. **Verify, read-only, then close the window**: `morpholog audit verify --database-url "$DATABASE_URL" --views-schema morpholog_views` must report replay `consistent`, tree `intact` and views `intact`. Then run `glasshouse verify` (all six legs) and `/readyz` (all verdicts `ok`), and confirm the deployed commit with `render deploys list <service-id>` for both services (`/healthz` does not name the commit yet).
8. **Optionally re-seed now** instead of waiting for 02:30: `render jobs create <cron-id> --start-command "uv run python -m glasshouse.cli seed --reset"`. It reports only after all six verify legs pass.

`render deploys create` exits 0 on an API 404/400, so read its output rather than trusting the exit code.

## Rollback

Migrations are forward-only, so rolling back rebuilds the schema at the old version, never downgrades it. It keeps the same no-skew rule as the upgrade, in reverse: take consumers off the new schema, rebuild the schema compatible with the old binary, then start the old consumers. It is also forced by the platform: the old web's pre-deploy `provision` runs the old binary's `migrate`, which refuses a migrated schema by name, so Render aborts an old web deploy while the schema is still new.

1. **Suspend the cron**, and accept the web outage from here on (the new web is about to lose its schema).
2. **Rebuild the schema with the old version.** Either restore the step-3 dump (see [Restore](#restore)), or deploy the previous commit to the cron and run its reset job: the old binary drops the governed schema, re-`init`s it at its own version, seeds and verifies. For the demo the reset is the simpler path, because nothing in it is irreplaceable.
3. **Deploy the previous commit to web.** Its `provision` now meets a schema it knows.
4. **Resume the cron** on the previous commit, then verify as in step 7.

## Restore

A backup counts only if the restore has been run. This one was rehearsed locally on 23/09/2026 against a v0.0.10 ledger (30 transitions): the dump was taken with `pg_dump -Fc` and restored into a fresh database with

```sh
createdb restored
pg_restore --no-owner -d restored demo-YYYY-MM-DD.dump
```

It came back with all 30 audit rows, and the v0.0.11 binary refused to `propose` against it by name until `migrate` ran. `pg_dump` warns about circular foreign keys in TimescaleDB's own catalogue (`continuous_agg`). That warning concerns data-only dumps and did not affect this full-format restore. What has **not** been rehearsed is restoring onto Render itself (into the live database, or a new one swapped in by URL). Until it has, the reset job is the rollback path for the demo, and the dump is the safety net for anything the reset cannot rebuild.

## Release-specific notes

- **v0.0.12 (migrations 016-017)**: 016 adds a function that orders stored instants, 017 the equality key every stored value compares by. Neither rewrites `claims` (0.08 s on the rehearsal ledger). **The managed indexes are a step of their own**: `morpholog provision indexes <programme>.morph --database-url <url> --prune` after `migrate`, because 017 rekeys every managed index and `init` never builds any. `glasshouse provision` and `seed --reset` both run it now (contract section 25), so on the demo the web pre-deploy and the nightly reset cover it; a database provisioned any other way needs the command by hand, or every keyed read scans its predicate under the lock the old whole-predicate read held. **The skew for this release is silent, not sharp**: a v0.0.11 binary commits against the migrated schema and its rows stay visible to v0.0.12's keyed loads; a v0.0.12 binary on an unmigrated schema commits some proposals and fails others (`function morpholog.value_key_v1(jsonb) does not exist`, reported as `not_committed`). Nothing refuses (upstream #393), so the table above does not describe this release; the rule still does.
- **v0.0.11 (migrations 012-015)**: 012 rekeys `claims` on an argument digest. It rewrites the table, so on a large ledger plan a window. On the demo's ~30-transition ledger it took 0.08 s in rehearsal. 013 adds checkpoint witnesses, 014 audit parameter names (rows written before it carry none, which is expected and decoded as `None`), and 015 the managed-index registry. `provision indexes` has nothing to do for this model, because its invariants are interpreted (contract section 24), so it is not part of the procedure until a model compiles.
