"""The compute-to-commit round trips of the needle.

`register_curve_version` and `correct_curve_version` store the payload
first and propose the identity claim second, so a committed claim never
anchors missing content. Whenever the ledger provably did NOT admit the
claim, the payload this call just stored is discarded again - otherwise
the version id would be consumed forever (the store refuses overwrites)
and a later legitimate correction could never reuse it. Provably means a
lawful rejection, or an operational failure the substrate classifies as
a known non-commit (since morpholog v0.0.11 every `MorphologError` from
a proposal is one, except `MorphologOutcomeUnknown`). An unknown outcome
keeps the payload: the claim may have committed, and a claim without its
payload would be a lie. A payload orphaned by a crash between store and
proposal remains detectable garbage for `glasshouse verify`.

`value_trade` is the killer query's write side: read the trade and the
official curve back from governed state, load the anchored payload,
re-hash it against the claimed hash (`glasshouse verify` in miniature,
on the read path where it is nearly free), compute the MTM, and propose
the result through `admit_valuation` - where the ledger, not this code,
decides whether the curve used is officially in force.

Single-row lookups here are licensed by the model's invariants (one
capture per trade, one official curve per org/market/as-of), the same
reasoning the worked embedder documents: governed state is not
untrusted input to be defensively re-checked.
"""

from __future__ import annotations

import datetime as dt

from glasshouse.commit import (
    Committed,
    GlasshouseClient,
    MorphologError,
    MorphologOutcomeUnknown,
    Outcome,
)
from glasshouse.commit.morpholog_client.models import (
    AdmitValuationRequest,
    CorrectCurveRequest,
    CurveRegisteredClaim,
    OfficialCurveClaim,
    RegisterCurveRequest,
    TradeCapturedClaim,
    TradeTermsClaim,
)
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.store import CurveStore
from glasshouse.compute.valuation import mark_to_market


class MarkingError(RuntimeError):
    """The marking flow cannot proceed honestly (missing governed
    state, or payload/claim divergence)."""


def register_curve_version(
    morpholog: GlasshouseClient,
    store: CurveStore,
    *,
    actor: str,
    org: str,
    market: str,
    as_of: dt.date,
    version: str,
    curve: HourlyCurve,
) -> Outcome:
    store.save(org=org, version=version, curve=curve)
    return _propose_anchored(
        morpholog,
        store,
        RegisterCurveRequest(
            org=org, market=market, as_of=as_of, version=version, payload_hash=curve.payload_hash()
        ),
        actor=actor,
        org=org,
        version=version,
    )


def correct_curve_version(
    morpholog: GlasshouseClient,
    store: CurveStore,
    *,
    actor: str,
    org: str,
    market: str,
    as_of: dt.date,
    prior_version: str,
    new_version: str,
    curve: HourlyCurve,
) -> Outcome:
    store.save(org=org, version=new_version, curve=curve)
    return _propose_anchored(
        morpholog,
        store,
        CorrectCurveRequest(
            org=org,
            market=market,
            as_of=as_of,
            prior_version=prior_version,
            new_version=new_version,
            payload_hash=curve.payload_hash(),
        ),
        actor=actor,
        org=org,
        version=new_version,
    )


def _propose_anchored(
    morpholog: GlasshouseClient,
    store: CurveStore,
    request: RegisterCurveRequest | CorrectCurveRequest,
    *,
    actor: str,
    org: str,
    version: str,
) -> Outcome:
    """Propose the claim anchoring a payload this call just stored, and
    give the version id back whenever the claim provably did not land
    (this call stored the payload, so this call may discard it)."""
    try:
        outcome = morpholog.submit(request, actor=actor)
    except MorphologOutcomeUnknown:
        raise  # it may have committed: the payload stays
    except MorphologError:
        store.discard(org=org, version=version)  # a known non-commit
        raise
    if not isinstance(outcome, Committed):
        store.discard(org=org, version=version)  # a decided rejection
    return outcome


def value_trade(
    morpholog: GlasshouseClient,
    store: CurveStore,
    *,
    actor: str,
    org: str,
    book: str,
    trade: str,
) -> Outcome:
    """Mark one trade against the official curve for its market and
    propose the result. Returns the ledger's verdict; raises
    `MarkingError` when the inputs do not admit an honest number."""
    captured = _one(
        [
            c
            for c in morpholog.read(TradeCapturedClaim)
            if c.org == org and c.book == book and c.trade == trade
        ],
        f"captured trade {trade!r} in {org}/{book}",
    )
    terms = _one(
        [t for t in morpholog.read(TradeTermsClaim) if t.org == org and t.trade == trade],
        f"terms for trade {trade!r}",
    )
    officials = [
        o
        for o in morpholog.read(OfficialCurveClaim)
        if o.org == org and o.market == captured.market
    ]
    if len(officials) > 1:
        # Lawful state the compute path cannot yet consume: one official
        # curve may stand per (org, market, AS-OF DATE), so several dates
        # can carry one at once. Selecting between them needs the
        # governed business date (glasshouse#44); until it exists this is
        # a named refusal, never a guess.
        dates = ", ".join(sorted(o.as_of.isoformat() for o in officials))
        raise MarkingError(
            f"{len(officials)} official curves stand for {org}/{captured.market} "
            f"(as-of dates: {dates}); choosing between business dates is not yet "
            "modelled - see glasshouse#44"
        )
    official = _one(officials, f"official curve for {org}/{captured.market}")
    registered = _one(
        [
            r
            for r in morpholog.read(CurveRegisteredClaim)
            if r.org == org and r.version == official.version
        ],
        f"registration of curve version {official.version!r}",
    )

    curve = store.load(org=org, version=official.version)
    if curve.payload_hash() != registered.payload_hash:
        raise MarkingError(
            f"payload for curve version {official.version!r} does not match its admitted "
            f"hash: stored {curve.payload_hash()}, claimed {registered.payload_hash}. "
            "The app schema disagrees with the ledger; refusing to compute from it."
        )

    value = mark_to_market(
        direction=captured.direction,
        quantity_mw=terms.quantity,  # declared Decimal[MW]; bare amount on the wire
        price=terms.price,
        delivery_start=terms.delivery_start,
        delivery_end=terms.delivery_end,
        curve=curve,
    )
    return morpholog.submit(
        AdmitValuationRequest(
            org=org, book=book, trade=trade, curve_version=official.version, mtm=value
        ),
        actor=actor,
    )


def _one[T](rows: list[T], description: str) -> T:
    if len(rows) != 1:
        raise MarkingError(f"expected exactly one {description}, found {len(rows)}")
    return rows[0]
