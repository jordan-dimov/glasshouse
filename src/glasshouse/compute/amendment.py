"""The amendment round trip: a trade's economic terms, superseded.

`amend_trade` reads the trade's versions and lineage back from governed
state, names the tip (the latest version, which the no-fork lineage
must not yet have superseded), mints the next version id by the app's
convention, and proposes `amend_trade`. There is no payload to store,
so unlike a curve registration there is nothing to discard on refusal.
A tip that another amendment beat this one to is a lawful `Rejected`
from the ledger's no-fork gate, not an error here: the caller re-reads
and decides again.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from glasshouse.commit import GlasshouseClient, Outcome
from glasshouse.commit.morpholog_client.models import (
    AmendTradeRequest,
    TradeTermsClaim,
    TradeTermsSupersedesClaim,
)
from glasshouse.compute.terms import current_terms, terms_version_id


class AmendmentError(RuntimeError):
    """The inputs do not admit an honest amendment (no terms to amend, or
    a lineage that disagrees with the timeline)."""


def amend_trade(
    morpholog: GlasshouseClient,
    *,
    actor: str,
    org: str,
    trade: str,
    quantity: Decimal,
    price: Decimal,
    delivery_start: dt.datetime,
    delivery_end: dt.datetime,
    effective_from: dt.date,
    new_version: str | None = None,
) -> Outcome:
    """Propose a new version of the trade's terms effective from
    `effective_from`, superseding the current one. Returns the ledger's
    verdict; raises `AmendmentError` when there is nothing to amend."""
    versions = [t for t in morpholog.read(TradeTermsClaim) if t.org == org and t.trade == trade]
    tip = current_terms(versions)
    if tip is None:
        raise AmendmentError(f"trade {trade!r} in {org} has no terms to amend: capture it first")
    superseded = {s.prior_version for s in morpholog.read(TradeTermsSupersedesClaim)}
    if tip.version in superseded:
        # Unreachable under the gate (an amendment is effective strictly
        # after its prior, so the latest version is never a prior), named
        # anyway: a lineage that disagrees with the timeline is not a
        # state to propose against.
        raise AmendmentError(
            f"the latest terms of trade {trade!r} ({tip.version}) are already superseded: "
            "lineage and timeline disagree"
        )
    return morpholog.submit(
        AmendTradeRequest(
            org=org,
            trade=trade,
            prior_version=tip.version,
            new_version=new_version or terms_version_id(trade, len(versions) + 1),
            quantity=quantity,
            price=price,
            delivery_start=delivery_start,
            delivery_end=delivery_end,
            effective_from=effective_from,
        ),
        actor=actor,
    )
