"""Curves CSV in, one registration per curve out, payload anchored first.

Rows group by (market, as_of, version) into whole curves; a curve is
atomic, so any row that cannot honestly join its curve (bad date, bad
decimal, gaps, misalignment - `HourlyCurve` is the validator) quarantines
the whole curve with the reason while the others proceed. Each surviving
curve goes through `register_curve_version`: payload stored before the
claim is proposed, so a committed claim never anchors missing content.

Register-only by design: a curve already official for its (org, market,
as-of) comes back as a lawful rejection telling the operator the honest
move is a correction - corrections are deliberate operator actions,
never imports.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from glasshouse.commit import GlasshouseClient, envelopes
from glasshouse.compute.curves import CurveError, HourlyCurve
from glasshouse.compute.marking import register_curve_version
from glasshouse.compute.store import CurveStore, StoreError
from glasshouse.imports.report import (
    COMMITTED,
    ERROR,
    QUARANTINED,
    REJECTED,
    ImportReport,
    RowOutcome,
)
from glasshouse.imports.trades import ImportFormatError

COLUMNS = frozenset({"market", "as_of", "version", "period_start", "price"})


def _check_header(fieldnames: Sequence[str] | None) -> None:
    found = set(fieldnames or [])
    if found != COLUMNS:
        missing = ", ".join(sorted(COLUMNS - found)) or "none"
        unknown = ", ".join(sorted(found - COLUMNS)) or "none"
        raise ImportFormatError(
            f"header does not match the curves contract (missing: {missing}; unknown: {unknown})"
        )


def _curve(rows: list[dict[str, str]]) -> tuple[dt.date, HourlyCurve]:
    """One group of rows to one validated curve, or a ValueError naming
    what could not be honestly converted."""
    if any(None in row.values() or row.get(None) is not None for row in rows):  # type: ignore[call-overload]
        raise ValueError("a row has the wrong number of fields")
    try:
        as_of = dt.date.fromisoformat(rows[0]["as_of"])
    except ValueError:
        raise ValueError(f"as_of must be an ISO date, got {rows[0]['as_of']!r}") from None
    try:
        periods = sorted(
            (dt.datetime.fromisoformat(row["period_start"]), Decimal(row["price"])) for row in rows
        )
    except ValueError:
        raise ValueError("period_start must be RFC 3339 instants") from None
    except InvalidOperation:
        raise ValueError("price must be an exact decimal") from None
    return as_of, HourlyCurve(tuple(periods))


def parse_curves(
    text: str,
) -> tuple[list[tuple[str, str, dt.date, str, HourlyCurve]], list[RowOutcome]]:
    """Group a curves CSV into whole curves in first-appearance order:
    (ref, market, as_of, version, curve) per survivor, a quarantined
    outcome per curve that could not be honestly built."""
    reader = csv.DictReader(io.StringIO(text))
    _check_header(reader.fieldnames)
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in reader:
        key = (str(row["market"]), str(row["as_of"]), str(row["version"]))
        groups.setdefault(key, []).append(row)

    curves: list[tuple[str, str, dt.date, str, HourlyCurve]] = []
    quarantined: list[RowOutcome] = []
    for (market, as_of_text, version), rows in groups.items():
        ref = f"{market}/{as_of_text}/{version}"
        try:
            as_of, curve = _curve(rows)
        except (ValueError, TypeError, CurveError) as reason:
            # TypeError covers a curve mixing naive and aware instants,
            # which cannot even be ordered.
            quarantined.append(RowOutcome(ref, QUARANTINED, str(reason)))
            continue
        curves.append((ref, market, as_of, version, curve))
    return curves, quarantined


def import_curves(
    client: GlasshouseClient, store: CurveStore, text: str, *, org: str, actor: str
) -> ImportReport:
    """Import one curves CSV: quarantine unbuildable curves, register the
    rest (payload first), and report every curve's fate."""
    curves, quarantined = parse_curves(text)
    outcomes = list(quarantined)
    for ref, market, as_of, version, curve in curves:
        try:
            outcome = register_curve_version(
                client,
                store,
                actor=actor,
                org=org,
                market=market,
                as_of=as_of,
                version=version,
                curve=curve,
            )
        except StoreError as immutable:
            # The payload store refuses overwrites, so a re-imported
            # version stops here, before the ledger is even asked.
            outcomes.append(RowOutcome(ref, ERROR, str(immutable)))
            continue
        match outcome:
            case envelopes.Committed(transition_id=transition_id):
                outcomes.append(RowOutcome(ref, COMMITTED, transition_id))
            case envelopes.Rejected(reason=reason):
                outcomes.append(RowOutcome(ref, REJECTED, reason))
    return ImportReport(tuple(outcomes))
