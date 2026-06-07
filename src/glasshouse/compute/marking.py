"""The compute-to-commit round trips of the needle.

`register_curve_version` and `correct_curve_version` store the payload
first and propose the identity claim second, so a committed claim never
anchors missing content (an orphaned payload from a rejected proposal
is detectable garbage; a claim without its payload would be a lie).

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

from glasshouse.commit import MorphologAdapter, Outcome
from glasshouse.commit.generated import (
    AdmitValuation,
    CorrectCurve,
    CurveRegisteredClaim,
    OfficialCurveClaim,
    RegisterCurve,
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
    morpholog: MorphologAdapter,
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
    return morpholog.propose(
        RegisterCurve(
            org=org, market=market, as_of=as_of, version=version, payload_hash=curve.payload_hash()
        ),
        actor=actor,
    )


def correct_curve_version(
    morpholog: MorphologAdapter,
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
    return morpholog.propose(
        CorrectCurve(
            org=org,
            market=market,
            as_of=as_of,
            prior_version=prior_version,
            new_version=new_version,
            payload_hash=curve.payload_hash(),
        ),
        actor=actor,
    )


def value_trade(
    morpholog: MorphologAdapter,
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
    official = _one(
        [
            o
            for o in morpholog.read(OfficialCurveClaim)
            if o.org == org and o.market == captured.market
        ],
        f"official curve for {org}/{captured.market}",
    )
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
    return morpholog.propose(
        AdmitValuation(org=org, book=book, trade=trade, curve_version=official.version, mtm=value),
        actor=actor,
    )


def _one[T](rows: list[T], description: str) -> T:
    if len(rows) != 1:
        raise MarkingError(f"expected exactly one {description}, found {len(rows)}")
    return rows[0]
