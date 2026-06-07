"""The pinned morpholog wire contract, typed.

Both directions of the value codec live here. Writing: `--args-named`
takes bare values, so `named_wire` only renders Decimal and date to their
wire strings. Reading: claims inside run envelopes and intent payloads
are tagged (`{"type": "decimal", "value": "82.50"}`), and the tags map
one-to-one onto Python types, so `untag` decodes straight to bare values
and no tagged-value class hierarchy exists. The targeted read
(`inspect claims --named`) is bare and named by the substrate itself;
its values are wire-true strings, and *typed* reads belong to the
generated per-predicate models, which know each field's kind from the
schema manifest. Decimals ride as strings end to end (law 8: never via
float).

The envelope models mirror morpholog's `docs/embedder-integration.md`,
verified against the real binary on 07/06/2026 (post PR #125).
`extra="forbid"` is deliberate: an upstream envelope change breaks
loudly here, before any integration test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, TypeAdapter

type BareValue = str | bool | Decimal | dt.date | dt.datetime | list[BareValue]

# What `--args-named` accepts, bare: Subjects as opaque strings, Decimals
# as Decimal, Dates as date, Timestamps as aware datetime (law 9:
# delivery periods are UTC instants; a naive datetime is refused at this
# boundary), Bools as bool. Collection and Duration parameters need the
# tagged `--args` codec; the types join here when a transformation
# forces them.
type NamedArg = str | bool | Decimal | dt.date | dt.datetime
type NamedArgs = Mapping[str, NamedArg]


def untag(wire: object) -> BareValue:
    """Decode one tagged wire value to its bare Python value."""
    match wire:
        case {"type": "subject", "value": str(value)}:
            return value
        case {"type": "decimal", "value": str(value)}:
            return Decimal(value)
        case {"type": "date", "value": str(value)}:
            return dt.date.fromisoformat(value)
        case {"type": "timestamp", "value": str(value)}:
            return dt.datetime.fromisoformat(value)  # RFC 3339; `Z` parses aware
        case {"type": "bool", "value": bool(value)}:
            return value
        case {"type": "collection", "value": list(items)}:
            return [untag(item) for item in items]
        case _:
            raise ValueError(f"not a tagged morpholog value: {wire!r}")


def named_wire(args: NamedArgs) -> dict[str, str | bool]:
    """Render bare named arguments to the `--args-named` JSON object."""

    def wire(value: NamedArg) -> str | bool:
        match value:
            case bool() | str():
                return value
            case Decimal():
                return format(value, "f")  # plain form, inside the schema pattern
            case dt.datetime():  # before date: datetime is a date subclass
                if value.tzinfo is None:
                    raise ValueError(f"timestamp must be timezone-aware (law 9): {value!r}")
                return value.isoformat()  # RFC 3339; `+00:00` verified accepted
            case dt.date():
                return value.isoformat()

    return {name: wire(value) for name, value in args.items()}


type Bare = Annotated[BareValue, BeforeValidator(untag)]


class _Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MissingClaim(_Envelope):
    """A claim whose absence failed a gate, and the transformations that
    could supply it: the raw material for "what would make this
    admissible?"."""

    predicate: str
    rendered: str
    candidate_supplier_transformations: tuple[str, ...]


class GateRejection(_Envelope):
    kind: Literal["gate"]
    gate: str
    statement_kind: str
    directly_missing_claims: tuple[MissingClaim, ...]


class InvariantRejection(_Envelope):
    kind: Literal["invariant"]
    name: str
    rule: str


class RejectedVerdict(_Envelope):
    rejected: Annotated[GateRejection | InvariantRejection, Field(discriminator="kind")]


class Explanation(_Envelope):
    """`explain --json`, and the `explanation` a rejection carries under
    `run --explain-on-reject` (same shape, same-snapshot semantics). The
    transition echo is diagnostic passthrough; the verdict is the
    payload."""

    transition: JsonValue
    verdict: Literal["admissible"] | RejectedVerdict

    @property
    def is_admissible(self) -> bool:
        return self.verdict == "admissible"


class Claim(_Envelope):
    """One governed fact as run envelopes carry it: predicate name and
    positional bare args. For reads decoded by field name, use
    `MorphologAdapter.read_claims` (the substrate's `--named` surface)."""

    predicate: str
    args: tuple[Bare, ...]


class EmittedIntent(_Envelope):
    name: str
    args: tuple[Bare, ...]


class Committed(_Envelope):
    status: Literal["committed"]
    transition_id: UUID
    actor: Bare
    asserted_claims: tuple[Claim, ...]
    retracted_claims: tuple[Claim, ...]
    emitted_intents: tuple[EmittedIntent, ...]


class Rejected(_Envelope):
    """A lawful outcome, not an error: the proposal was understood and
    refused by the rules (law 10). Under `--explain-on-reject` it carries
    the explanation computed against the exact pre-state that refused."""

    status: Literal["rejected"]
    reason: str
    explanation: Explanation | None = None


type Outcome = Annotated[Committed | Rejected, Field(discriminator="status")]

OUTCOME: TypeAdapter[Outcome] = TypeAdapter(Outcome)


class NamedClaim(_Envelope):
    """`inspect claims --named`: args decoded to declared field names by
    the substrate, values wire-true (decimals and dates stay strings)."""

    predicate: str
    args: dict[str, JsonValue]


CLAIMS: TypeAdapter[list[Claim]] = TypeAdapter(list[Claim])
NAMED_CLAIMS: TypeAdapter[list[NamedClaim]] = TypeAdapter(list[NamedClaim])
