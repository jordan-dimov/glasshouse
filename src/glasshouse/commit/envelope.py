"""The pinned morpholog wire contract, typed.

Both directions of the value codec live here. Reading: morpholog emits
tagged values (`{"type": "decimal", "value": "82.50"}`) whose tags map
one-to-one onto Python types, so `untag` decodes straight to bare values
and no tagged-value class hierarchy exists. Writing: `--args-named` takes
bare values, so `named_wire` only renders Decimal and date to their wire
strings. Decimals ride as strings end to end (law 8: never via float).

The envelope models mirror morpholog's `docs/embedder-integration.md`,
verified against the real binary on 07/06/2026. `extra="forbid"` is
deliberate: an upstream envelope change breaks loudly here, before any
integration test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue, TypeAdapter

type BareValue = str | bool | Decimal | dt.date | list[BareValue]

# What `--args-named` accepts, bare: Subjects as opaque strings, Decimals
# as Decimal, Dates as date, Bools as bool. Collection parameters need the
# tagged `--args` codec; the type excludes them until a transformation
# forces it.
type NamedArg = str | bool | Decimal | dt.date
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
            case dt.date():
                return value.isoformat()

    return {name: wire(value) for name, value in args.items()}


type Bare = Annotated[BareValue, BeforeValidator(untag)]


class _Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Claim(_Envelope):
    """One governed fact: predicate name and positional bare args. Naming
    the positions goes through the declared vocabulary
    (`MorphologAdapter.read_claims`), never hard-coded indices."""

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
    refused by the rules (law 10)."""

    status: Literal["rejected"]
    reason: str


type Outcome = Annotated[Committed | Rejected, Field(discriminator="status")]

OUTCOME: TypeAdapter[Outcome] = TypeAdapter(Outcome)


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
    """`morpholog explain --json`: a question answered, never an action
    taken. The transition echo is diagnostic passthrough; the verdict is
    the payload."""

    transition: JsonValue
    verdict: Literal["admissible"] | RejectedVerdict

    @property
    def is_admissible(self) -> bool:
        return self.verdict == "admissible"


class PredicateArg(_Envelope):
    name: str
    kind: str


class PredicateDecl(_Envelope):
    """A predicate's declared vocabulary: the read-side analogue of
    `x-morpholog-arg-order`, and the only sanctioned source of field
    names for positional claim args."""

    name: str
    args: tuple[PredicateArg, ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(arg.name for arg in self.args)


CLAIMS: TypeAdapter[list[Claim]] = TypeAdapter(list[Claim])
PREDICATE_DECLS: TypeAdapter[list[PredicateDecl]] = TypeAdapter(list[PredicateDecl])
