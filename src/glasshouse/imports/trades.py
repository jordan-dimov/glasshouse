"""Trades CSV in, one `run --batch` invocation out, per-row receipts back.

The column contract is exact and validation never coerces: a file whose
header does not match refuses whole; a row that cannot honestly become a
`capture_trade` proposal is quarantined with its reason and the rest
proceed. The ledger's per-row verdicts come back as receipts (each row
its own SERIALIZABLE transition, never all-or-nothing), so re-importing
a file is safe and visibly so: duplicate trade ids return as lawful
rejections in the report.

`org` and `actor` are run parameters, not columns: one operator imports
one file into their organisation, and their capability claims gate every
row. A per-row actor column waits for a real mixed-provenance file to
force it (the batch wire already supports it).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from glasshouse.commit import GlasshouseClient, envelopes, models, values
from glasshouse.imports.report import (
    ADMISSIBLE,
    COMMITTED,
    ERROR,
    QUARANTINED,
    REFUSED,
    REJECTED,
    ImportReport,
    RowOutcome,
    why,
)

COLUMNS = frozenset(
    {
        "book",
        "trade",
        "counterparty",
        "market",
        "direction",
        "quantity",
        "price",
        "delivery_start",
        "delivery_end",
    }
)

# The ledger's Subject is opaque, but the compute zone's MTM only knows
# these two; anything else is quarantined rather than coerced.
DIRECTIONS = frozenset({"buy", "sell"})


class ImportFormatError(ValueError):
    """The file as a whole does not match the column contract."""


def _check_header(fieldnames: Sequence[str] | None) -> None:
    found = set(fieldnames or [])
    if found != COLUMNS:
        missing = ", ".join(sorted(COLUMNS - found)) or "none"
        unknown = ", ".join(sorted(found - COLUMNS)) or "none"
        raise ImportFormatError(
            f"header does not match the trades contract (missing: {missing}; unknown: {unknown})"
        )


def _request(row: dict[str, str], org: str) -> models.CaptureTradeRequest:
    """One CSV row to one typed proposal, or a ValueError naming what
    could not be honestly converted."""
    if any(value is None for value in row.values()):
        raise ValueError("row has fewer fields than the header")
    if row.get(None) is not None:  # type: ignore[call-overload]
        raise ValueError("row has more fields than the header")
    if row["direction"] not in DIRECTIONS:
        raise ValueError(f"direction must be buy or sell, got {row['direction']!r}")
    try:
        quantity = Decimal(row["quantity"])
        price = Decimal(row["price"])
    except InvalidOperation:
        raise ValueError(
            f"quantity/price must be exact decimals, got {row['quantity']!r}/{row['price']!r}"
        ) from None
    try:
        delivery_start = dt.datetime.fromisoformat(row["delivery_start"])
        delivery_end = dt.datetime.fromisoformat(row["delivery_end"])
    except ValueError:
        raise ValueError(
            "delivery_start/delivery_end must be RFC 3339 instants, got "
            f"{row['delivery_start']!r}/{row['delivery_end']!r}"
        ) from None
    request = models.CaptureTradeRequest(
        org=org,
        book=row["book"],
        trade=row["trade"],
        counterparty=row["counterparty"],
        market=row["market"],
        direction=row["direction"],
        quantity=quantity,
        price=price,
        delivery_start=delivery_start,
        delivery_end=delivery_end,
    )
    request.to_args_named()  # the codec is the final validator (naive instants, ...)
    return request


def parse_trades(
    text: str, *, org: str
) -> tuple[list[tuple[int, models.CaptureTradeRequest]], list[tuple[int, RowOutcome]]]:
    """Split a trades CSV into typed proposals and quarantined rows,
    both keyed by their CSV line number (the header is line 1)."""
    reader = csv.DictReader(io.StringIO(text))
    _check_header(reader.fieldnames)
    accepted: list[tuple[int, models.CaptureTradeRequest]] = []
    quarantined: list[tuple[int, RowOutcome]] = []
    for row in reader:
        line = reader.line_num
        try:
            accepted.append((line, _request(row, org)))
        except (ValueError, values.CodecError) as reason:
            quarantined.append((line, RowOutcome(f"line {line}", QUARANTINED, str(reason))))
    return accepted, quarantined


def _receipt_outcome(ref: str, outcome: object) -> RowOutcome:
    match outcome:
        case envelopes.Committed(transition_id=transition_id):
            return RowOutcome(ref, COMMITTED, transition_id)
        case envelopes.Rejected(reason=reason, explanation=explanation):
            detail = f"{reason} - {why(explanation)}" if explanation else reason
            return RowOutcome(ref, REJECTED, detail)
        case envelopes.BatchError(error=error):
            return RowOutcome(ref, ERROR, error)
        case _:
            raise TypeError(f"not a batch outcome: {outcome!r}")


def import_trades(
    client: GlasshouseClient, text: str, *, org: str, actor: str, explain: bool = True
) -> ImportReport:
    """Import one trades CSV: quarantine locally, batch the rest, and
    report every row's fate in file order. With `explain` (the default -
    the report is for humans), a rejected row's detail carries the
    same-snapshot why."""
    accepted, quarantined = parse_trades(text, org=org)
    outcomes = list(quarantined)
    if accepted:
        rows = [
            {
                "transformation": req.TRANSFORMATION,
                "actor": actor,
                "args_named": req.to_args_named(),
            }
            for _, req in accepted
        ]
        for receipt in client.run_batch(rows, explain_on_reject=explain):
            # receipt.row indexes the batch, which excludes quarantined
            # CSV rows; map it back to the file's own line number.
            line, _ = accepted[receipt.row - 1]
            outcomes.append((line, _receipt_outcome(f"line {line}", receipt.outcome)))
    return ImportReport(tuple(outcome for _, outcome in sorted(outcomes, key=lambda o: o[0])))


def preview_trades(client: GlasshouseClient, text: str, *, org: str, actor: str) -> ImportReport:
    """The workbench's validate step: parse and quarantine exactly as an
    import would, then dry-run every surviving row through `explain` -
    the ledger's own admissibility verdict, against current state, with
    nothing committed."""
    accepted, quarantined = parse_trades(text, org=org)
    outcomes = list(quarantined)
    for line, req in accepted:
        explanation = client.explain(req.TRANSFORMATION, actor, req.to_args_named())
        if explanation.admissible:
            outcomes.append((line, RowOutcome(f"line {line}", ADMISSIBLE, "would commit")))
        else:
            outcomes.append((line, RowOutcome(f"line {line}", REFUSED, why(explanation))))
    return ImportReport(tuple(outcome for _, outcome in sorted(outcomes, key=lambda o: o[0])))
