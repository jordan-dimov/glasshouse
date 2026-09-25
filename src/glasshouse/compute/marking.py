"""The compute-to-commit round trips of the needle.

`register_curve_version` and `correct_curve_version` store the payload
first and propose the identity claim second, so a committed claim never
anchors missing content. Whenever the ledger provably did NOT admit the
claim, the payload this call just stored is discarded again - otherwise
the version id would be consumed forever (the store refuses overwrites)
and a later legitimate correction could never reuse it. Provably means a
lawful rejection, or an operational failure the substrate classifies as
a known non-commit (since morpholog v0.0.12 the binary states one by
published code, and every other ending of a proposal - a timeout, a
crash, a reply that does not decode - is `MorphologOutcomeUnknown`). An
unknown outcome keeps the payload: the claim may have committed, and a
claim without its payload would be a lie. A payload orphaned by a crash between store and
proposal remains detectable garbage for `glasshouse verify`.

`correct_and_remark` is the correction as the desk means it: the new
version supersedes the old AND every trade on the market is re-marked
against it, proposed through `transact` as ONE decision. Proposed one by
one, the correction lands first and each mark after it, so for a window
(or for good, if the flow dies halfway) a trade's latest mark names the
superseded curve. Atomically there is no such state to observe: every
act commits or none does.

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
from decimal import Decimal

from glasshouse.commit import (
    Committed,
    GlasshouseClient,
    MorphologError,
    MorphologOutcomeUnknown,
    Outcome,
)
from glasshouse.commit.morpholog_client.envelopes import AtomicCommitted, AtomicRejected
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
    _refuse_several_business_dates(officials, org=org, market=captured.market)
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

    return morpholog.submit(
        AdmitValuationRequest(
            org=org,
            book=book,
            trade=trade,
            curve_version=official.version,
            mtm=_mtm(captured, terms, curve),
        ),
        actor=actor,
    )


def correct_and_remark(
    morpholog: GlasshouseClient,
    store: CurveStore,
    *,
    curve_actor: str,
    valuation_actor: str,
    org: str,
    market: str,
    as_of: dt.date,
    prior_version: str,
    new_version: str,
    curve: HourlyCurve,
) -> AtomicCommitted | AtomicRejected:
    """Correct a curve version and re-mark every trade captured on its
    market against the correction, as one decision. Each act carries its
    own actor, and `admit_valuation`'s gate reads the official pointer
    the correction act staged. Returns the ledger's verdict (a refusal
    names the refusing act, and nothing is written); raises
    `MarkingError` when the inputs do not admit an honest number.

    The trades are read before the decision, so a trade captured while
    this runs is corrected under but not re-marked - exactly what the
    one-by-one flow does to it, and `value_trade` marks it afterwards."""
    officials = [
        o for o in morpholog.read(OfficialCurveClaim) if o.org == org and o.market == market
    ]
    _refuse_several_business_dates(officials, org=org, market=market)
    terms = {t.trade: t for t in morpholog.read(TradeTermsClaim) if t.org == org}
    captured = sorted(
        (c for c in morpholog.read(TradeCapturedClaim) if c.org == org and c.market == market),
        key=lambda c: c.trade,
    )
    if missing := [c.trade for c in captured if c.trade not in terms]:
        raise MarkingError(f"captured trades without terms: {', '.join(missing)}")
    acts = [
        _act(
            CorrectCurveRequest(
                org=org,
                market=market,
                as_of=as_of,
                prior_version=prior_version,
                new_version=new_version,
                payload_hash=curve.payload_hash(),
            ),
            curve_actor,
        ),
        *(
            _act(
                AdmitValuationRequest(
                    org=org,
                    book=c.book,
                    trade=c.trade,
                    curve_version=new_version,
                    mtm=_mtm(c, terms[c.trade], curve),
                ),
                valuation_actor,
            )
            for c in captured
        ),
    ]
    store.save(org=org, version=new_version, curve=curve)
    try:
        outcome = morpholog.transact(acts)
    except MorphologOutcomeUnknown:
        raise  # it may have committed: the payload stays
    except MorphologError:
        store.discard(org=org, version=new_version)  # a known non-commit
        raise
    if isinstance(outcome, AtomicRejected):
        store.discard(org=org, version=new_version)  # nothing was written
    return outcome


def _act(request: CorrectCurveRequest | AdmitValuationRequest, actor: str) -> dict[str, object]:
    """One `transact` act in the batch row shape. The generated
    `transact` takes raw rows where `submit` takes the typed request;
    the request's own codec still validates every value."""
    return {
        "transformation": request.TRANSFORMATION,
        "actor": actor,
        "args_named": request.to_args_named(),
    }


def _mtm(captured: TradeCapturedClaim, terms: TradeTermsClaim, curve: HourlyCurve) -> Decimal:
    return mark_to_market(
        direction=captured.direction,
        quantity_mw=terms.quantity,  # declared Decimal[MW]; bare amount on the wire
        price=terms.price,
        delivery_start=terms.delivery_start,
        delivery_end=terms.delivery_end,
        curve=curve,
    )


def _refuse_several_business_dates(
    officials: list[OfficialCurveClaim], *, org: str, market: str
) -> None:
    if len(officials) > 1:
        # Lawful state the compute path cannot yet consume: one official
        # curve may stand per (org, market, AS-OF DATE), so several dates
        # can carry one at once. Selecting between them needs the
        # governed business date (glasshouse#44); until it exists this is
        # a named refusal, never a guess.
        dates = ", ".join(sorted(o.as_of.isoformat() for o in officials))
        raise MarkingError(
            f"{len(officials)} official curves stand for {org}/{market} "
            f"(as-of dates: {dates}); choosing between business dates is not yet "
            "modelled - see glasshouse#44"
        )


def _one[T](rows: list[T], description: str) -> T:
    if len(rows) != 1:
        raise MarkingError(f"expected exactly one {description}, found {len(rows)}")
    return rows[0]
