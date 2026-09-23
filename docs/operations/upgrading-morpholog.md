# Upgrading Morpholog: the database runbook

How a new morpholog release reaches each database Glasshouse uses. The code half of an upgrade (pin, regenerate, drift gates) is in the contract doc's standing rules and each re-pin's section. This page covers the data half. It was written for v0.0.10 → v0.0.11 (contract section 24) and is kept general where the procedure does not depend on the release.

## The rule that shapes everything

**A binary and the governed schema it writes must move together.** Migrations are forward-only and embedded in the binary (`morpholog migrate`). `init` provisions a missing schema and never alters an existing one. The skew fails differently in each direction, as measured at v0.0.11:

| Binary | Schema | What happens |
|---|---|---|
| new | old (unmigrated) | `propose` refuses by name ("the database schema is behind this binary ... run `morpholog migrate`"). `audit verify` and the audit tail fail with a raw SQL error (`column "arguments_hash" does not exist`). |
| old | new (migrated) | `propose` fails with a raw SQL error (`no unique or exclusion constraint matching the ON CONFLICT specification`). The old generated client also refuses new audit rows (`unknown key(s) ['parameters']`), so the projector stops. `migrate` refuses by name ("this database records migrations this binary does not know ... upgrade the binary"), so an old `glasshouse provision` fails too. |

Both directions stop the service, so no process may run an older binary against a database a newer one has migrated.

## The databases

| Database | Owner | Lifetime | Upgrade path |
|---|---|---|---|
| Local dev DB (the compose `db` service, port 5432) | the developer | disposable | Run `glasshouse provision` with the new binary (it migrates), or drop the volume and start again. |
| Test DB (whatever `GLASSHOUSE_TEST_DATABASE_URL` names) | the developer | disposable | None. The live suite drops and re-`init`s the governed schema on every run. |
| CI `services:` Postgres (integration job, canary) | GitHub Actions | per run | None. Every run starts empty and `init`s with the pinned binary. |
| Render `glasshouse-db` (demo) | the maintainer | persistent, rebuilt nightly by `seed --reset` | The procedure below. **Ask before touching it.** |

The Render database is written by two services from the **same image**: `glasshouse-web`, whose `preDeployCommand` runs `glasshouse provision` (`init` if absent, then `migrate`, then the sealed views), and the `glasshouse-seed-reset` cron at 02:30 UTC. The cron drops the governed schema and re-`init`s it with **its own** binary. So if only one of the two is redeployed, the next reset or the next deploy puts the database and one of its writers on opposite sides of the table above.

## Procedure (demo database)

Render's deploy history for both services shows no GitHub commit trigger ever firing (every deploy is a Blueprint sync, a manual API call or a platform restart), so both services are deployed by hand. Do it well clear of 02:30 UTC.

1. **Merge** the re-pin PR once CI is green on main.
2. **Look before changing anything.** From a machine with the new binary and the database URL:
   ```sh
   morpholog migrate --check --database-url "$DATABASE_URL"   # exit 1 lists what is pending
   ```
3. **Back up.** The demo is rebuilt nightly, so the backup protects the rehearsal, not irreplaceable data. Take one anyway: `pg_dump -Fc "$DATABASE_URL" > demo-$(date -u +%F).dump`. Render's own point-in-time backups depend on the plan and are not relied on here.
4. **Deploy web**, pinned to the exact commit: `render deploys create <web-id> --commit "$(git rev-parse origin/main)" --wait --confirm`. The pre-deploy `provision` runs `migrate`. Check its log for `migrated (...)`. While the old web instance keeps serving until the swap, its writes (the imports workbench) fail. The window is the length of the deploy.
5. **Deploy the cron immediately after**, in the same sitting: `render deploys create <cron-id> --wait --confirm`. Cron services refuse `--commit` (HTTP 400) and build the branch head, so confirm the head is the commit web took.
6. **Verify, read-only**: `morpholog audit verify --database-url "$DATABASE_URL" --views-schema morpholog_views` must report replay `consistent`, tree `intact` and views `intact`. Then run `glasshouse verify` (all six legs) and `/readyz` (all verdicts `ok`), and confirm the deployed commit with `render deploys list <service-id>` for both services. `/healthz` does not name the commit yet.
7. **Optionally re-seed now** instead of waiting for 02:30: `render jobs create <cron-id> --start-command "uv run python -m glasshouse.cli seed --reset"`. It reports only after all six verify legs pass.

`render deploys create` exits 0 on an API 404/400, so read its output rather than trusting the exit code.

## Rollback

Migrations are forward-only, so rolling back is a rebuild of the schema, never a downgrade. The order matters. The old web's pre-deploy `provision` runs the old binary's `migrate`, which refuses a migrated schema, so Render aborts the web deploy while the schema is still new. Roll back the database first:

1. **Deploy the previous commit to the cron** (it has no pre-deploy step, so nothing refuses) and trigger its reset job. The old binary drops the governed schema and re-`init`s it at its own version, then seeds and verifies. Or restore the step-3 dump.
2. **Then deploy the previous commit to web.** Its `provision` now meets a schema it knows.

Between the two steps the new web is running against an old schema, which fails as the table above says. Keep the gap short. The demo has no traffic worth fencing, but a real deployment would put a maintenance page in front first.

## Release-specific notes

- **v0.0.11 (migrations 012-015)**: 012 rekeys `claims` on an argument digest. It rewrites the table, so on a large ledger plan a window. On the demo's ~30-transition ledger it took 0.08 s in rehearsal. 013 adds checkpoint witnesses, 014 audit parameter names (rows written before it carry none, which is expected and decoded as `None`), and 015 the managed-index registry. `provision indexes` has nothing to do for this model, because its invariants are interpreted (contract section 24), so it is not part of the procedure until a model compiles.
