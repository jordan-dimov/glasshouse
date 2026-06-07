"""The commit zone: the only write path to governed state.

This package wraps the morpholog binary (subprocess; `run --batch` once it
lands upstream) behind a typed adapter. The adapter returns
``Committed | Rejected`` as a discriminated union and raises on operational
failure, so rejection-vs-failure confusion is unrepresentable.

The one absolute rule of the codebase: writes to governed state only ever
go through this package. No ORM writes, no raw SQL writes, no exceptions.
"""

from pathlib import Path

from glasshouse.commit.adapter import MorphologAdapter, MorphologOperationalError
from glasshouse.commit.bases import ClaimRow, CommitRequest
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
    NamedClaim,
    Outcome,
    Rejected,
    RejectedVerdict,
)

# The rule model ships inside the package; the adapter, the codegen and
# the deployment all point at this one file.
MODEL_FILE = Path(__file__).parent / "glasshouse.morph"

__all__ = [
    "MODEL_FILE",
    "BareValue",
    "Claim",
    "ClaimRow",
    "CommitRequest",
    "Committed",
    "EmittedIntent",
    "Explanation",
    "GateRejection",
    "InvariantRejection",
    "MissingClaim",
    "MorphologAdapter",
    "MorphologOperationalError",
    "NamedArgs",
    "NamedClaim",
    "Outcome",
    "Rejected",
    "RejectedVerdict",
]
