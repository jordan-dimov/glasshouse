"""The curve query layer: versions, officialness, lineage, payload
verdicts and the two-version diff.

This sits BESIDE `glasshouse.api.queries` (the projection-query layer)
rather than inside it: curve versions have no projection table, and
their honest source is the pinned typed ledger surface - one
`claims_named` call over the four predicates, so every screen renders
ONE ledger snapshot (separate reads could straddle a concurrent
correction) - plus the hash-anchored payload store. The same functions
serve the JSON endpoints and the Curves screen (UI law 4). Operational
failure of the binary surfaces as `MorphologError`, mapped app-wide to
the same 503 discipline as a dead database.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from glasshouse.api.schemas import CurveDiff, CurveDiffPeriod, CurveVersion, VersionMark
from glasshouse.commit import GlasshouseClient, models
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.store import CurveStore, StoreError

_PREDICATES = ("CurveRegistered", "OfficialCurve", "CurveSupersedes", "TradeValued")


class UnknownCurveVersionError(ValueError):
    """A requested version is not registered for this org and market."""


class IncomparableCurvesError(ValueError):
    """The selected versions are not economically comparable: a curve
    correction comparison needs one org, market and as-of date (which,
    under the model's one-official-per-date rule, also means one
    supersession chain). Comparing different dates would juxtapose two
    unrelated timelines, not show a correction."""


class CurveIntegrityError(RuntimeError):
    """A registered version's payload is missing from the store: the
    application schema disagrees with the governed ledger. This is an
    integrity break, never a not-found - run `glasshouse verify`."""


class _Snapshot:
    """The four curve-relevant predicates decoded from ONE named read -
    one ledger snapshot, whatever commits mid-render."""

    def __init__(self, client: GlasshouseClient) -> None:
        rows = client.claims_named(*_PREDICATES)
        self.registered = [
            models.CurveRegisteredClaim.from_named(row.args)
            for row in rows
            if row.predicate == "CurveRegistered"
        ]
        self.official = [
            models.OfficialCurveClaim.from_named(row.args)
            for row in rows
            if row.predicate == "OfficialCurve"
        ]
        self.supersedes = [
            models.CurveSupersedesClaim.from_named(row.args)
            for row in rows
            if row.predicate == "CurveSupersedes"
        ]
        self.valued = [
            models.TradeValuedClaim.from_named(row.args)
            for row in rows
            if row.predicate == "TradeValued"
        ]


def list_markets(client: GlasshouseClient, *, org: str) -> list[str]:
    """Markets with at least one registered curve version in this org."""
    return sorted({c.market for c in _Snapshot(client).registered if c.org == org})


def list_curve_versions(
    client: GlasshouseClient, store: CurveStore, *, org: str, market: str
) -> list[CurveVersion]:
    """Every registered version for the market: status (official /
    superseded / registered), the admitted payload hash beside a live
    re-hash verdict (the `glasshouse verify` payload leg in miniature),
    the supersession lineage, and which marks were struck on it."""
    snapshot = _Snapshot(client)
    registered = [c for c in snapshot.registered if c.org == org and c.market == market]
    known = {c.version for c in registered}
    official = {o.version for o in snapshot.official if o.org == org and o.market == market}
    # CurveSupersedes carries no org (versions are unique per org by
    # discipline); edges are joined via the versions registered here, so
    # a version id colliding across orgs would alias - a documented
    # limitation of the current model, not of this screen.
    edges = [e for e in snapshot.supersedes if e.new_version in known or e.prior_version in known]
    supersedes = {e.new_version: e.prior_version for e in edges}
    superseded_by = {e.prior_version: e.new_version for e in edges}
    marks = [m for m in snapshot.valued if m.org == org]

    versions = []
    for claim in sorted(registered, key=lambda c: c.version):
        verdict: Literal["ok", "mismatch", "missing"]
        try:
            stored = store.load(org=org, version=claim.version)
            verdict = "ok" if stored.payload_hash() == claim.payload_hash else "mismatch"
        except StoreError:
            verdict = "missing"
        struck = [m for m in marks if m.curve_version == claim.version]
        status: Literal["official", "superseded", "registered"]
        if claim.version in official:
            status = "official"
        elif claim.version in superseded_by:
            status = "superseded"
        else:
            status = "registered"
        versions.append(
            CurveVersion(
                version=claim.version,
                as_of=claim.as_of,
                status=status,
                payload_hash=claim.payload_hash,
                payload=verdict,
                supersedes=supersedes.get(claim.version),
                superseded_by=superseded_by.get(claim.version),
                mark_count=len(struck),
                books=sorted({m.book for m in struck}),
            )
        )
    return versions


def _diff_periods(base: HourlyCurve, compare: HourlyCurve) -> list[CurveDiffPeriod]:
    """Period-by-period price comparison. Curves are hour-aligned by
    construction, so alignment is `period_start` equality; a period
    absent from one side carries no delta (the function stays total even
    though the comparability rule keeps same-date curves together)."""
    base_prices: dict[dt.datetime, Decimal] = dict(base.periods)
    compare_prices: dict[dt.datetime, Decimal] = dict(compare.periods)
    rows = []
    for period_start in sorted({*base_prices, *compare_prices}):
        base_price: Decimal | None = base_prices.get(period_start)
        compare_price: Decimal | None = compare_prices.get(period_start)
        delta = (
            compare_price - base_price
            if base_price is not None and compare_price is not None
            else None
        )
        rows.append(
            CurveDiffPeriod(
                period_start=period_start,
                base_price=base_price,
                compare_price=compare_price,
                delta=delta,
            )
        )
    return rows


def curve_diff(
    client: GlasshouseClient,
    store: CurveStore,
    *,
    org: str,
    market: str,
    base: str,
    compare: str,
) -> CurveDiff:
    """What changed between two versions of one curve, and which trades'
    marks were struck on each - the money interaction of the Curves
    screen. Refuses versions that are not economically comparable (they
    must share the as-of date, which the one-official-per-date rule ties
    to one supersession chain)."""
    snapshot = _Snapshot(client)
    by_version = {c.version: c for c in snapshot.registered if c.org == org and c.market == market}
    for version in (base, compare):
        if version not in by_version:
            raise UnknownCurveVersionError(
                f"version {version!r} is not registered for {org}/{market}"
            )
    if by_version[base].as_of != by_version[compare].as_of:
        raise IncomparableCurvesError(
            f"versions {base!r} (as of {by_version[base].as_of.isoformat()}) and "
            f"{compare!r} (as of {by_version[compare].as_of.isoformat()}) describe "
            "different business dates; a correction comparison needs one date"
        )
    try:
        base_curve = store.load(org=org, version=base)
        compare_curve = store.load(org=org, version=compare)
    except StoreError as absent:
        raise CurveIntegrityError(
            f"a registered payload is missing for {org}/{market}: {absent}. "
            "The app schema disagrees with the ledger; run glasshouse verify."
        ) from absent
    marks = [m for m in snapshot.valued if m.org == org and m.curve_version in (base, compare)]
    return CurveDiff(
        org=org,
        market=market,
        base=base,
        compare=compare,
        periods=_diff_periods(base_curve, compare_curve),
        base_marks=sorted(
            (
                VersionMark(trade=m.trade, book=m.book, mtm=m.mtm)
                for m in marks
                if m.curve_version == base
            ),
            key=lambda m: m.trade,
        ),
        compare_marks=sorted(
            (
                VersionMark(trade=m.trade, book=m.book, mtm=m.mtm)
                for m in marks
                if m.curve_version == compare
            ),
            key=lambda m: m.trade,
        ),
    )
