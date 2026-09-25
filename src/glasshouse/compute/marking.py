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
official curve back from governed state, pick the terms version in
force on the curve's business date (the same rule the ledger's generated
selector applies), load the anchored payload, re-hash it against the
claimed hash (`glasshouse verify` in miniature, on the read path where
it is nearly free), compute the MTM, and propose the result through
`admit_valuation` - where the ledger, not this code, decides whether
the curve used is officially in force and the terms used were in force
on its date. The mark is pinned to both versions.

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
from glasshouse.compute.terms import terms_in_force_on
from glasshouse.compute.valuation import mark_to_market
from glasshouse.logging import get_logger

log = get_logger("glasshouse.marking")


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
    officials = [
        o
        for o in morpholog.read(OfficialCurveClaim)
        if o.org == org and o.market == captured.market
    ]
    _refuse_several_business_dates(officials, org=org, market=captured.market)
    official = _one(officials, f"official curve for {org}/{captured.market}")
    versions = [t for t in morpholog.read(TradeTermsClaim) if t.org == org and t.trade == trade]
    terms = _in_force(versions, trade=trade, as_of=official.as_of)
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
            terms_version=terms.version,
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
    one-by-one flow does to it, and `value_trade` marks it afterwards.
    Each trade is re-marked under the terms in force on the correction's
    business date; a trade none of whose versions is yet in force on it
    is skipped and logged, since the ledger's own rule says it is not
    valuable as of that date, and refusing the whole correction would
    punish it for a trade booked after the curve's date."""
    officials = [
        o for o in morpholog.read(OfficialCurveClaim) if o.org == org and o.market == market
    ]
    _refuse_several_business_dates(officials, org=org, market=market)
    versions: dict[str, list[TradeTermsClaim]] = {}
    for version in morpholog.read(TradeTermsClaim):
        if version.org == org:
            versions.setdefault(version.trade, []).append(version)
    captured = sorted(
        (c for c in morpholog.read(TradeCapturedClaim) if c.org == org and c.market == market),
        key=lambda c: c.trade,
    )
    if missing := [c.trade for c in captured if c.trade not in versions]:
        raise MarkingError(f"captured trades without terms: {', '.join(missing)}")
    in_force: dict[str, TradeTermsClaim] = {}
    for c in captured:
        terms = terms_in_force_on(versions[c.trade], as_of)
        if terms is None:
            log.warning("marking.remark_skipped_not_in_force", org=org, trade=c.trade, as_of=as_of)
            continue
        in_force[c.trade] = terms
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
                    terms_version=in_force[c.trade].version,
                    mtm=_mtm(c, in_force[c.trade], curve),
                ),
                valuation_actor,
            )
            for c in captured
            if c.trade in in_force
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


def _in_force(versions: list[TradeTermsClaim], *, trade: str, as_of: dt.date) -> TradeTermsClaim:
    """The terms version the ledger's selector will name for this date,
    or a named refusal when none is yet effective (the gate would refuse
    the mark; saying so here spares the ledger a doomed proposal)."""
    if not versions:
        raise MarkingError(f"expected at least one terms version for trade {trade!r}, found 0")
    terms = terms_in_force_on(versions, as_of)
    if terms is None:
        earliest = min(v.effective_from for v in versions)
        raise MarkingError(
            f"no terms of trade {trade!r} are in force on {as_of}: the first version is "
            f"effective from {earliest}"
        )
    return terms


def _one[T](rows: list[T], description: str) -> T:
    if len(rows) != 1:
        raise MarkingError(f"expected exactly one {description}, found {len(rows)}")
    return rows[0]
