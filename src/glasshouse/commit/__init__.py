"""The commit zone: the only write path to governed state.

This package wraps the morpholog binary (subprocess; `run --batch` once it
lands upstream) behind a typed adapter. The adapter returns
``Committed | Rejected`` as a discriminated union and raises on operational
failure, so rejection-vs-failure confusion is unrepresentable.

The one absolute rule of the codebase: writes to governed state only ever
go through this package. No ORM writes, no raw SQL writes, no exceptions.
"""

from glasshouse.commit.adapter import MorphologAdapter, MorphologOperationalError
from glasshouse.commit.envelope import (
    BareValue,
    Claim,
    Committed,
    EmittedIntent,
    Explanation,
    GateRejection,
    InvariantRejection,
    MissingClaim,
    NamedArgs,
    Outcome,
    PredicateDecl,
    Rejected,
    RejectedVerdict,
)

__all__ = [
    "BareValue",
    "Claim",
    "Committed",
    "EmittedIntent",
    "Explanation",
    "GateRejection",
    "InvariantRejection",
    "MissingClaim",
    "MorphologAdapter",
    "MorphologOperationalError",
    "NamedArgs",
    "Outcome",
    "PredicateDecl",
    "Rejected",
    "RejectedVerdict",
]
