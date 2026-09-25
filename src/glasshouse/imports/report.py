"""Import outcomes, one per row or curve, none silently dropped.

The report is the import's deliverable: every input ends up in exactly
one state, each with the detail an operator needs to act on it.
`committed` carries the transition id (the row's place in the ledger),
`rejected` the ledger's reason (a lawful outcome, not an error),
`error` the binary's per-row error receipt, and `quarantined` the local
validation reason for a row that never reached the ledger at all. A
batch that stops adds two more, so that even then no row goes
unaccounted for: `unknown` for the row in flight when it stopped (it may
have committed: read the record before re-submitting) and `not
attempted` for the rows behind it, which never ran.
"""

from __future__ import annotations

from dataclasses import dataclass

from glasshouse.commit import envelopes

COMMITTED = "committed"
REJECTED = "rejected"
ERROR = "error"
QUARANTINED = "quarantined"
# A batch that stopped: the row in flight, then the rows never run.
UNKNOWN = "unknown"
NOT_ATTEMPTED = "not attempted"
# Preview statuses: the ledger's dry-run verdicts, nothing committed.
ADMISSIBLE = "admissible"
REFUSED = "refused"

_STATUSES = (
    COMMITTED,
    REJECTED,
    ERROR,
    QUARANTINED,
    UNKNOWN,
    NOT_ATTEMPTED,
    ADMISSIBLE,
    REFUSED,
)


def why(explanation: envelopes.Explanation | None) -> str:
    """One line of business-terms why, from the same-snapshot
    explanation: what is missing and which transformations could supply
    it, or which invariant the proposal would break."""
    if explanation is None:
        return ""
    match explanation.rejection:
        case None:
            return "admissible"
        case envelopes.GateRejection(directly_missing_claims=missing, gate=gate):
            parts = [
                f"missing {claim.rendered}"
                + (
                    f" (supplied by {', '.join(claim.candidate_supplier_transformations)})"
                    if claim.candidate_supplier_transformations
                    else ""
                )
                for claim in missing
            ]
            return "; ".join(parts) or f"gate {gate} refused"
        case envelopes.InvariantRejection(name=name):
            return f"would break invariant {name}"
        case envelopes.ErrorRejection(message=message):
            return message


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

    @property
    def unknown(self) -> int:
        return self.count(UNKNOWN)

    @property
    def not_attempted(self) -> int:
        return self.count(NOT_ATTEMPTED)

    def render(self) -> str:
        present = [status for status in _STATUSES if self.count(status)]
        counts = ", ".join(f"{self.count(status)} {status}" for status in present) or "nothing"
        lines = [f"{len(self.outcomes)} processed: {counts}"]
        lines += [
            f"  {outcome.status:<13} {outcome.ref}: {outcome.detail}" for outcome in self.outcomes
        ]
        return "\n".join(lines)
