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

from typing import Literal

from fastapi import APIRouter, Depends, Query

from glasshouse.api import audit as audit_queries
from glasshouse.api.deps import get_client
from glasshouse.api.schemas import AuditEntry
from glasshouse.commit import GlasshouseClient

router = APIRouter(tags=["audit"])


@router.get("/audit")
def audit_log(
    org: str,
    scope: Literal["org", "ledger"] = "org",
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: GlasshouseClient = Depends(get_client),
) -> list[AuditEntry]:
    entries = audit_queries.list_audit(client)
    if scope == "org":
        entries = [e for e in entries if audit_queries.mentions_org(e, org)]
    end = offset + limit if limit is not None else None
    return entries[offset:end]
