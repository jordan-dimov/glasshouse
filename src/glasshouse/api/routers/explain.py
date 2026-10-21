"""The `/explain` endpoint: the workbench's validate step over HTTP.

A dry run through the commit layer (`morpholog explain`) for the calling
actor (the `X-Actor` header, the same L0 identity the write path will
use; identity only, never authorisation, which stays governed by
capability claims in the ledger). It reads the same pre-state the gates
would evaluate and reports whether the transformation would be
admissible, and if not, the one rejection that front-ran the rest -
missing capability claims with their candidate suppliers, a tripped
invariant, or a codec error. Nothing is committed and no payload is
stored; this is a question, not an action, so even a rejection is a 200.
Only an operational failure of the binary itself is an error (502: the
commit subprocess is an upstream dependency); its message is logged
server-side, never reflected to the client (it can carry the database
URL).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from glasshouse.api.deps import ClientDep
from glasshouse.api.schemas import (
    Compared,
    ErrorRejection,
    ExplainRequest,
    ExplainResponse,
    GateRejection,
    InvariantRejection,
    MissingClaim,
    Rejection,
    WitnessBinding,
)
from glasshouse.commit import MorphologError
from glasshouse.commit.morpholog_client import envelopes
from glasshouse.imports.report import wire_text
from glasshouse.logging import get_logger

router = APIRouter(tags=["explain"])

log = get_logger("glasshouse.api")


def _compared(compared: envelopes.Compared | None) -> Compared | None:
    if compared is None:
        return None
    return Compared(op=compared.op, left=wire_text(compared.left), right=wire_text(compared.right))


def _witness(witness: list[envelopes.WitnessBinding]) -> list[WitnessBinding]:
    return [WitnessBinding(var=binding.var, value=wire_text(binding.value)) for binding in witness]


def _flatten(rejection: object) -> Rejection | None:
    """The generated envelope's frozen dataclasses into the wire models."""
    match rejection:
        case None:
            return None
        case envelopes.GateRejection():
            return GateRejection(
                gate=rejection.gate,
                rule=rejection.rule,
                statement_kind=rejection.statement_kind,
                directly_missing_claims=[
                    MissingClaim(
                        predicate=claim.predicate,
                        rendered=claim.rendered,
                        candidate_supplier_transformations=list(
                            claim.candidate_supplier_transformations
                        ),
                    )
                    for claim in rejection.directly_missing_claims
                ],
                compared=_compared(rejection.compared),
                witness=_witness(rejection.witness),
            )
        case envelopes.InvariantRejection():
            return InvariantRejection(
                name=rejection.name,
                rule=rejection.rule,
                compared=_compared(rejection.compared),
                witness=_witness(rejection.witness),
            )
        case envelopes.ErrorRejection():
            return ErrorRejection(message=rejection.message)
        case _:  # pragma: no cover - the envelope's union is closed
            raise HTTPException(status_code=502, detail="unknown rejection shape")


@router.post("/explain")
def explain(
    body: ExplainRequest,
    actor: Annotated[str, Header(alias="X-Actor")],
    client: ClientDep,
) -> ExplainResponse:
    try:
        result = client.explain(body.transformation, actor, body.args)
    except MorphologError as exc:
        # The binary failed or refused the request operationally (a bad
        # transformation name, a codec error, a dead database): an upstream
        # dependency failure, not a 500. Log the detail (redacted at the
        # source) but never reflect it - it can carry the database URL.
        log.warning("api.explain_failed", transformation=body.transformation, error=str(exc))
        raise HTTPException(status_code=502, detail="explain could not be evaluated") from exc
    return ExplainResponse(admissible=result.admissible, rejection=_flatten(result.rejection))
