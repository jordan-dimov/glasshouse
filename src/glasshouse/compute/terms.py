"""Which version of a trade's terms governs a date.

The ledger declares `TradeTerms` `effective by (trade) on (effective_from)`,
and morpholog generates the selector `trade_terms_in_force_on(trade,
as_of, ..)`: the version whose effective date is the latest not after
`as_of`. The compute zone needs the same answer before it proposes (to
know which quantity and price to value), so the rule is written here
once, in Python, and pinned by property tests against a brute-force
reading. The gate is the authority: if the two ever disagreed - an
amendment committed between this read and the proposal - the ledger
refuses the mark and the caller re-runs. Nothing here decides; it only
predicts what the ledger will decide.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from glasshouse.commit.morpholog_client.models import TradeTermsClaim


class TermsError(RuntimeError):
    """Two versions of one trade share an effective date - a state the
    generated `trade_terms_unique_by_trade_effective_from` forbids, so
    seeing it means the read is not of one ledger."""


def terms_in_force_on(
    versions: Iterable[TradeTermsClaim], as_of: dt.date
) -> TradeTermsClaim | None:
    """The version in force on `as_of`: the latest `effective_from` on or
    before it. `None` when no version is yet effective (the selector
    matches nothing); the caller names its own refusal."""
    candidates = [v for v in versions if v.effective_from <= as_of]
    if not candidates:
        return None
    latest = max(v.effective_from for v in candidates)
    (governing, *ties) = [v for v in candidates if v.effective_from == latest]
    if ties:
        raise TermsError(
            f"{1 + len(ties)} versions of trade {governing.trade!r} are effective from "
            f"{latest}: the ledger forbids this, so these rows are not one ledger's"
        )
    return governing


def current_terms(versions: Iterable[TradeTermsClaim]) -> TradeTermsClaim | None:
    """The latest version overall, whatever the date: the terms the desk
    means by "the trade" today, and what the blotter projection shows."""
    rows = list(versions)
    if not rows:
        return None
    return terms_in_force_on(rows, max(v.effective_from for v in rows))


def terms_version_id(trade: str, ordinal: int) -> str:
    """The app's convention for a terms version id: a per-trade sequence
    the desk can read (`T-001/v1`, `T-001/v2`), caller-supplied to the
    ledger exactly like a curve version id. The ledger never mints
    identifiers; it only refuses a reused one."""
    return f"{trade}/v{ordinal}"


def next_version_id(trade: str, versions: Iterable[TradeTermsClaim]) -> str:
    """The next id in the convention that no existing version of the
    trade already carries. Versions may have been named by a caller
    outside the convention (any subject is lawful), so this is the
    first free ordinal above every version present, not the count plus
    one: a collision would only be a lawful refusal, but a flow should
    not propose one it can see coming."""
    taken = {v.version for v in versions}
    ordinal = 1 + sum(1 for _ in taken)
    while terms_version_id(trade, ordinal) in taken:
        ordinal += 1
    return terms_version_id(trade, ordinal)
