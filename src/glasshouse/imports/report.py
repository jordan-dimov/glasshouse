"""Import outcomes, one per row or curve, none silently dropped.

The report is the import's deliverable: every input ends up in exactly
one of four states, each with the detail an operator needs to act on it.
`committed` carries the transition id (the row's place in the ledger),
`rejected` the ledger's reason (a lawful outcome, not an error),
`error` the binary's per-row error receipt, and `quarantined` the local
validation reason for a row that never reached the ledger at all.
"""

from __future__ import annotations

from dataclasses import dataclass

COMMITTED = "committed"
REJECTED = "rejected"
ERROR = "error"
QUARANTINED = "quarantined"

_STATUSES = (COMMITTED, REJECTED, ERROR, QUARANTINED)


@dataclass(frozen=True)
class RowOutcome:
    """One input's fate. `ref` names the input ("line 7" for a CSV row,
    "market/as-of/version" for a curve); `detail` is the transition id,
    rejection reason, error text or quarantine reason."""

    ref: str
    status: str
    detail: str


@dataclass(frozen=True)
class ImportReport:
    """Everything that happened to one file, in file order."""

    outcomes: tuple[RowOutcome, ...]

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def committed(self) -> int:
        return self.count(COMMITTED)

    @property
    def rejected(self) -> int:
        return self.count(REJECTED)

    @property
    def errored(self) -> int:
        return self.count(ERROR)

    @property
    def quarantined(self) -> int:
        return self.count(QUARANTINED)

    def render(self) -> str:
        counts = ", ".join(f"{self.count(status)} {status}" for status in _STATUSES)
        lines = [f"{len(self.outcomes)} processed: {counts}"]
        lines += [
            f"  {outcome.status:<11} {outcome.ref}: {outcome.detail}" for outcome in self.outcomes
        ]
        return "\n".join(lines)
