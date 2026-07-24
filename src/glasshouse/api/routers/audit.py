"""The audit read endpoint: the ledger's transition log, newest first -
the JSON twin of the Audit screen (UI law 4).

Deliberately org-less, a documented departure from the org-required
rule on every other read: the audit log belongs to the ledger, not to
one organisation (there is no org column on a transition; organisations
live inside the claims it moved). Presenting a filtered log would
misrepresent the record, so scoping is a display concern, never a query
parameter here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from glasshouse.api import audit as audit_queries
from glasshouse.api.deps import get_client
from glasshouse.api.schemas import AuditEntry
from glasshouse.commit import GlasshouseClient

router = APIRouter(tags=["audit"])


@router.get("/audit")
def audit_log(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    client: GlasshouseClient = Depends(get_client),
) -> list[AuditEntry]:
    entries = audit_queries.list_audit(client)
    end = offset + limit if limit is not None else None
    return entries[offset:end]
