"""Pydantic models at the HTTP boundary.

These live here, not in the commit zone: DESIGN.md section 7 keeps
Pydantic at the edge, built *from* the generated types and the projection
tables, never under governed state. The read models project the
projection tables (the primary read model, law 4); the explain models
flatten the generated `Explanation` envelope so the same-snapshot "why"
travels over HTTP unchanged.

Money and quantity cross the wire as exact decimal *strings*, never JSON
numbers: a float on the way out would betray law 8 at the last step. The
`ExactDecimal` annotation does that for every numeric business field.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

# Serialise Decimal as its canonical string, so an exact MW or EUR amount
# stays exact across the wire (and reads as a tabular numeral on a screen).
ExactDecimal = Annotated[Decimal, PlainSerializer(str, return_type=str)]


class BlotterTrade(BaseModel):
    """One captured trade with its terms - the blotter row."""

    org: str
    trade: str
    book: str
    counterparty: str
    market: str
    direction: str
    quantity: ExactDecimal  # MW
    price: ExactDecimal  # EUR/MWh
    delivery_start: datetime
    delivery_end: datetime
    captured_at: datetime
    transition_id: str
    actor: str  # who captured it - the evidence trail rides the read


class PositionHour(BaseModel):
    """Net MW for one UTC delivery hour (buy +, sell -)."""

    org: str
    book: str
    market: str
    period_start: datetime
    net_mw: ExactDecimal
    transition_id: str


class TradeValuation(BaseModel):
    """One admitted mark, pinned to the curve version it was struck
    against - both marks survive a correction, exactly as stored."""

    org: str
    trade: str
    curve_version: str
    book: str
    mtm: ExactDecimal  # EUR
    valued_at: datetime
    transition_id: str
    actor: str  # who admitted it


class ExplainRequest(BaseModel):
    """A dry-run question: would this transformation be admissible for
    the calling actor (the `X-Actor` header), and if not, what is
    missing? Args are wire-named (the shape a generated request model's
    `to_args_named()` produces); where a transformation takes an org, it
    rides in `args` and the binary validates it. `default_factory` keeps
    the empty default a fresh dict per request."""

    transformation: str
    args: dict[str, object] = Field(default_factory=dict)


class MissingClaim(BaseModel):
    predicate: str
    rendered: str
    candidate_supplier_transformations: list[str]


class GateRejection(BaseModel):
    kind: Literal["gate"] = "gate"
    gate: str
    statement_kind: str
    directly_missing_claims: list[MissingClaim]


class InvariantRejection(BaseModel):
    kind: Literal["invariant"] = "invariant"
    name: str
    rule: str


class ErrorRejection(BaseModel):
    kind: Literal["error"] = "error"
    message: str


# Discriminated by "kind", mirroring the generated envelope's closed union.
Rejection = Annotated[
    GateRejection | InvariantRejection | ErrorRejection,
    Field(discriminator="kind"),
]


class ExplainResponse(BaseModel):
    """The flattened verdict: admissible, or the one rejection that
    front-ran the rest. `None` rejection iff admissible."""

    admissible: bool
    rejection: Rejection | None = None
