"""The audit read endpoint: the ledger's transition log, newest first -
the JSON twin of the Audit screen (UI law 4).

Scoped by default: `org` is required (the tenancy boundary, law 6) and
the default response carries the transitions whose claims mention that
organisation - a scoped view of the wider ledger, and it says so.
`scope=ledger` returns every tenant's transitions: the system-auditor
view, which at readiness L0 is honesty-labelled rather than
authenticated; a real privileged capability arrives with the identity
work (issue #40).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from glasshouse.api import audit as audit_queries
from glasshouse.api.deps import ClientDep
from glasshouse.api.schemas import AuditEntry

router = APIRouter(tags=["audit"])


@router.get("/audit")
def audit_log(
    org: str,
    client: ClientDep,
    scope: Literal["org", "ledger"] = "org",
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AuditEntry]:
    entries = audit_queries.list_audit(client)
    if scope == "org":
        entries = [e for e in entries if audit_queries.mentions_org(e, org)]
    end = offset + limit if limit is not None else None
    return entries[offset:end]
