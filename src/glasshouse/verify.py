"""`glasshouse verify`: prove, on demand, that the operational database
still agrees with the governed ledger. Read-only throughout.

Five independent legs, each its own verdict:

* **model** - the deployed binary names the same rules as the committed
  client (`morpholog hash` vs the generated `MODEL_HASH`);
* **ledger** - `morpholog verify` upstream: the audit log replays to
  the claims table (two independent records of the same history);
* **projections** - the read-side law, checked without writing: the
  whole log replayed through the pure folds in memory and diffed
  against the live projection tables (`projections.accumulate`);
* **payloads** - every registered curve's stored content re-hashed
  against the hash its governed claim admitted; missing payloads are
  divergence, orphaned payloads (content no claim anchors) are
  reported as a warning - detectable garbage, not a lie;
* **views** - the official inspection model (law 4): the generated
  per-predicate SQL views still name the committed programme (the
  `morpholog_views` catalogue's model hash vs `MODEL_HASH`).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from glasshouse.commit import MODEL_HASH, VIEWS_SCHEMA, GlasshouseClient, models, views_model_hash
from glasshouse.compute.store import CurveStore, StoreError, curve_payload_period
from glasshouse.projections import ProjectionError, accumulate
from glasshouse.projections.projector import CURSOR as PROJECTION_CURSOR
from glasshouse.projections.tables import metadata as projection_metadata
from glasshouse.projections.tables import projection_progress


@dataclass(frozen=True)
class Leg:
    """One leg's verdict: `ok` is the law, `detail` is the evidence."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class VerifyReport:
    legs: tuple[Leg, ...]

    @property
    def ok(self) -> bool:
        return all(leg.ok for leg in self.legs)

    def render(self) -> str:
        verdict = "consistent" if self.ok else "DIVERGENT"
        lines = [f"glasshouse verify: {verdict}"]
        lines += [
            f"  {'ok' if leg.ok else 'FAIL':<4} {leg.name:<12} {leg.detail}" for leg in self.legs
        ]
        return "\n".join(lines)


def _model_leg(client: GlasshouseClient) -> Leg:
    deployed = client.hash().hash
    if deployed == MODEL_HASH:
        return Leg("model", True, f"binary and committed client both name {deployed}")
    return Leg("model", False, f"binary names {deployed}, committed client {MODEL_HASH}")


def _ledger_leg(client: GlasshouseClient) -> Leg:
    verdict = client.verify_ledger()
    # The replay verdict: the audit log still replays to the claims
    # table. (The same envelope also carries a `tree` Merkle verdict; the
    # tamper-evidence leg that surfaces it lands with the legitimacy work,
    # which adds the checkpoints that make the tree non-trivial.)
    replay = verdict.get("replay")
    replay = replay if isinstance(replay, dict) else {}
    if replay.get("status") == "consistent":
        return Leg(
            "ledger",
            True,
            f"{replay.get('transitions', '?')} transition(s) replay to "
            f"{replay.get('claims', '?')} claim(s)",
        )
    return Leg("ledger", False, f"replay {replay.get('status', 'unavailable')}")


def _views_leg(engine: sa.Engine) -> Leg:
    # The official inspection model (law 4): the generated per-predicate
    # views still name the committed programme. The catalogue stamps the
    # same hash the binary and client name, so this proves the SQL read
    # surface has not drifted from the rules under it.
    deployed = views_model_hash(engine)
    if deployed is None:
        return Leg("views", False, f"the {VIEWS_SCHEMA} inspection model is not applied")
    if deployed == MODEL_HASH:
        return Leg("views", True, f"inspection model names {deployed}")
    return Leg("views", False, f"inspection model names {deployed}, committed client {MODEL_HASH}")


def _projection_leg(client: GlasshouseClient, engine: sa.Engine) -> Leg:
    # One REPEATABLE READ snapshot for the cursor AND the tables: the
    # projector advances both atomically, so a consistent snapshot of
    # our schema is internally coherent whatever a concurrent catch-up
    # does. The tail is then folded only UP TO that cursor (anything
    # beyond is lag, not divergence), and the cursor's transition is
    # guaranteed visible in any later tail snapshot - committed-row
    # visibility is monotonic - so the comparison is race-free.
    actual: dict[str, set[tuple[object, ...]]] = {}
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        up_to = connection.execute(
            sa.select(projection_progress.c.transition_id).where(
                projection_progress.c.name == PROJECTION_CURSOR
            )
        ).scalar_one_or_none()
        for name, table in projection_metadata.tables.items():
            actual[name] = {tuple(row) for row in connection.execute(sa.select(table))}

    try:
        expected = accumulate(client, up_to=up_to)
    except ProjectionError as corruption:
        return Leg("projections", False, str(corruption))

    problems = []
    for name, expected_rows in expected.items():
        missing = len(expected_rows - actual[name])
        unexpected = len(actual[name] - expected_rows)
        if missing or unexpected:
            problems.append(f"{name}: {missing} missing, {unexpected} unexpected")
    if problems:
        return Leg("projections", False, "; ".join(problems))
    total = sum(len(rows) for rows in expected.values())
    return Leg("projections", True, f"{total} row(s) match a replay up to the cursor")


def _payload_leg(client: GlasshouseClient, store: CurveStore) -> Leg:
    claims = client.read(models.CurveRegisteredClaim)
    mismatched: list[str] = []
    missing: list[str] = []
    for claim in claims:
        # Org-qualified throughout: the organisation is the tenancy
        # boundary, and a bare version is ambiguous across orgs.
        ref = f"{claim.org}/{claim.version}"
        try:
            stored = store.load(org=claim.org, version=claim.version)
        except StoreError:
            missing.append(ref)
            continue
        if stored.payload_hash() != claim.payload_hash:
            mismatched.append(ref)

    claimed = {(claim.org, claim.version) for claim in claims}
    with store.engine.connect() as connection:
        stored_versions = {
            (row.org, row.curve_version)
            for row in connection.execute(
                sa.select(
                    curve_payload_period.c.org, curve_payload_period.c.curve_version
                ).distinct()
            )
        }
    orphans = sorted(f"{org}/{version}" for org, version in stored_versions - claimed)

    if mismatched or missing:
        return Leg(
            "payloads",
            False,
            f"hash mismatch: {', '.join(mismatched) or 'none'}; "
            f"missing payload: {', '.join(missing) or 'none'}",
        )
    detail = f"{len(claims)} payload(s) re-hash to their admitted hashes"
    if orphans:
        detail += f" (warning - orphaned payloads no claim anchors: {', '.join(orphans)})"
    return Leg("payloads", True, detail)


def verify(client: GlasshouseClient, engine: sa.Engine, store: CurveStore) -> VerifyReport:
    """All five legs, in dependency order. Each leg is independent: a
    divergent ledger does not stop the projections being checked."""
    return VerifyReport(
        (
            _model_leg(client),
            _ledger_leg(client),
            _projection_leg(client, engine),
            _payload_leg(client, store),
            _views_leg(engine),
        )
    )
