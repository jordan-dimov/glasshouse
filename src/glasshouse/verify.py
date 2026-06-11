"""`glasshouse verify`: prove, on demand, that the operational database
still agrees with the governed ledger. Read-only throughout.

Four independent legs, each its own verdict:

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
  reported as a warning - detectable garbage, not a lie.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from glasshouse.commit import MODEL_HASH, GlasshouseClient, models
from glasshouse.compute.store import CurveStore, StoreError, curve_payload_period
from glasshouse.projections import accumulate
from glasshouse.projections.tables import metadata as projection_metadata


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
    if verdict.get("status") == "consistent":
        return Leg(
            "ledger",
            True,
            f"{verdict.get('transitions', '?')} transition(s) replay to "
            f"{verdict.get('claims', '?')} claim(s)",
        )
    only_claims = len(verdict.get("only_in_claims_table", []) or [])
    only_replay = len(verdict.get("only_in_replay", []) or [])
    return Leg(
        "ledger",
        False,
        f"{only_claims} claim(s) only in the claims table, {only_replay} only in the replay",
    )


def _projection_leg(engine: sa.Engine) -> Leg:
    expected = accumulate(engine)
    problems = []
    with engine.connect() as connection:
        for name, expected_rows in expected.items():
            table = projection_metadata.tables[name]
            actual_rows = {tuple(row) for row in connection.execute(sa.select(table))}
            missing = len(expected_rows - actual_rows)
            unexpected = len(actual_rows - expected_rows)
            if missing or unexpected:
                problems.append(f"{name}: {missing} missing, {unexpected} unexpected")
    if problems:
        return Leg("projections", False, "; ".join(problems))
    total = sum(len(rows) for rows in expected.values())
    return Leg("projections", True, f"{total} row(s) match a replay from zero")


def _payload_leg(client: GlasshouseClient, store: CurveStore) -> Leg:
    claims = client.read(models.CurveRegisteredClaim)
    mismatched: list[str] = []
    missing: list[str] = []
    for claim in claims:
        try:
            stored = store.load(org=claim.org, version=claim.version)
        except StoreError:
            missing.append(claim.version)
            continue
        if stored.payload_hash() != claim.payload_hash:
            mismatched.append(claim.version)

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
    orphans = sorted(version for _, version in stored_versions - claimed)

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
    """All four legs, in dependency order. Each leg is independent: a
    divergent ledger does not stop the projections being checked."""
    return VerifyReport(
        (
            _model_leg(client),
            _ledger_leg(client),
            _projection_leg(engine),
            _payload_leg(client, store),
        )
    )
