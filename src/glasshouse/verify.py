"""`glasshouse verify`: prove, on demand, that the operational database
still agrees with the governed ledger. Read-only throughout.

Six independent legs, each its own verdict:

* **model** - the deployed binary names the same rules as the committed
  client (`morpholog hash` vs the generated `MODEL_HASH`);
* **ledger** - `morpholog verify`'s replay verdict: the audit log
  replays to the claims table (two independent records of the same
  history), read from the typed `VerifyReport.replay`;
* **tree** - the same `verify` call's Merkle history-tree verdict
  (`VerifyReport.tree`): the checkpointed prefix is internally
  consistent and unrewritten. Trivially intact until `glasshouse
  checkpoint` has anchored something; meaningful once it has;
* **projections** - the read-side law, checked without writing: the
  whole log replayed through the pure folds in memory and diffed
  against the live projection tables (`projections.accumulate`);
* **payloads** - every registered curve's stored content re-hashed
  against the hash its governed claim admitted; missing payloads are
  divergence, orphaned payloads (content no claim anchors) are
  reported as a warning - detectable garbage, not a lie;
* **views** - the official inspection model (law 4): the generated
  per-predicate SQL views still name the committed programme (the
  `morpholog_views` catalogue's model hash vs `MODEL_HASH`), the whole
  inventory is present, and the surface agrees with its seal - the
  binary's `--views-schema` cross-check of each view's live definition
  against the hash recorded when the views were applied, which is what
  catches a view redefined in place under the same name (our #184,
  delivered upstream in #190).

The model, ledger and tree legs are now generated, typed surfaces under
the regenerate-and-diff drift gate - the silent-drift hole that the
hand-bridged `verify` once had is closed.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa

from glasshouse.commit import (
    MODEL_HASH,
    VIEWS_SCHEMA,
    GlasshouseClient,
    MorphologError,
    missing_catalogued_views,
    models,
    views_model_hash,
)
from glasshouse.commit.morpholog_client.envelopes import (
    ReplayConsistent,
    TreeIntact,
    ViewsIntact,
    ViewsNotSealed,
    ViewsVerification,
)
from glasshouse.commit.morpholog_client.envelopes import (
    VerifyReport as LedgerVerifyReport,
)
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


def _ledger_leg(report: LedgerVerifyReport) -> Leg:
    # The replay verdict from the typed `verify` envelope: the audit log
    # still replays to the claims table.
    replay = report.replay
    if isinstance(replay, ReplayConsistent):
        return Leg(
            "ledger",
            True,
            f"{replay.transitions} transition(s) replay to {replay.claims} claim(s)",
        )
    return Leg(
        "ledger",
        False,
        f"{len(replay.only_in_claims_table)} claim(s) only in the claims table, "
        f"{len(replay.only_in_replay)} only in the replay",
    )


def _tree_leg(report: LedgerVerifyReport) -> Leg:
    # The Merkle history-tree verdict from the same `verify` call. Intact
    # with zero checkpoints is honest (nothing anchored, nothing
    # rewritten); once `glasshouse checkpoint` has run, this proves the
    # checkpointed prefix is consistent and unrewritten. Any non-intact
    # verdict names itself (tampered, chain_broken, ...).
    tree = report.tree
    if isinstance(tree, TreeIntact):
        return Leg(
            "tree",
            True,
            f"history tree intact ({tree.checkpoints} checkpoint(s) over {tree.tree_size} row(s))",
        )
    verdict = type(tree).__name__.removeprefix("Tree")
    return Leg("tree", False, f"history tree verdict: {verdict}")


def _views_leg(engine: sa.Engine, seal: ViewsVerification | None) -> Leg:
    # The official inspection model (law 4), three independent checks:
    # the catalogue stamps the same hash the binary and client name; the
    # inventory check guards against a dropped or renamed view the hash
    # alone would miss (the catalogue is itself a view, so a hash read
    # can succeed while a sibling is gone); and the binary's seal verdict
    # catches a view redefined in place under the same name, which both
    # of the local checks are blind to. The local checks are database
    # reads, so they still give evidence when the shared `verify` call
    # failed and no seal verdict exists (`seal is None`) - but the leg
    # cannot claim ok without one, because "ok" now means unredefined.
    deployed = views_model_hash(engine)
    if deployed is None:
        return Leg("views", False, f"the {VIEWS_SCHEMA} inspection model is not applied")
    missing = missing_catalogued_views(engine)
    if missing:
        return Leg("views", False, f"catalogued view(s) missing: {', '.join(missing)}")
    if deployed != MODEL_HASH:
        return Leg(
            "views", False, f"inspection model names {deployed}, committed client {MODEL_HASH}"
        )
    if seal is None:
        return Leg("views", False, "seal unverified: the verify call could not run")
    if isinstance(seal, ViewsNotSealed):
        # The committed script seals at apply time, so an unsealed live
        # surface is not the committed surface: re-apply to seal it.
        return Leg("views", False, "surface is unsealed: re-apply the inspection model")
    if isinstance(seal, ViewsIntact):
        return Leg(
            "views",
            True,
            f"inspection model names {deployed}, full inventory present, "
            f"seal intact over {seal.views_checked} view(s)",
        )
    tampered = []
    if seal.mismatched:
        tampered.append(f"redefined in place: {', '.join(seal.mismatched)}")
    if seal.missing:
        tampered.append(f"seal or view missing: {', '.join(seal.missing)}")
    return Leg("views", False, f"seal broken - {'; '.join(tampered)}")


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
    """All six legs. Each leg is its own verdict: a divergent ledger does
    not stop the projections being checked. The ledger, tree and views
    legs share one `verify` call (replay, tree and the views seal are
    faces of the same typed envelope), so an operational failure of that
    call fails all three - the views leg's local checks still report
    their evidence, but "ok" needs the seal - while the model, projection
    and payload legs still run, giving as much evidence as they can."""
    try:
        report = client.verify(views_schema=VIEWS_SCHEMA)
        ledger, tree = _ledger_leg(report), _tree_leg(report)
        seal = report.views
    except MorphologError as failure:
        unavailable = f"verify could not run: {failure}"
        ledger = Leg("ledger", False, unavailable)
        tree = Leg("tree", False, unavailable)
        seal = None
    return VerifyReport(
        (
            _model_leg(client),
            ledger,
            tree,
            _projection_leg(client, engine),
            _payload_leg(client, store),
            _views_leg(engine, seal),
        )
    )
