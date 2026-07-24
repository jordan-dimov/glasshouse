"""The audit query layer: the ledger's transition log, display-shaped.

Reads the blessed named audit tail (`client.audit_named()` - typed,
lossless, pinned) and flattens each row for rendering: named claims as
plain field-value pairs, the attestation carried through, counts instead
of raw lists where the screen only needs a number. The audit log belongs
to the ledger, not to one organisation - callers present all of it and
may highlight the rows that mention their org, never silently filter.

The whole tail is fetched and sliced by the caller: the upstream tail
deliberately has no limit (the poll loop is the projector's own), which
is honest at demo scale; a bounded read is part of the filed upstream
ask.
"""

from __future__ import annotations

from glasshouse.api.schemas import AttestationInfo, AuditClaim, AuditEntry
from glasshouse.commit import GlasshouseClient
from glasshouse.commit.morpholog_client.envelopes import AuditRowNamed, NamedClaim


def _claim(named: NamedClaim) -> AuditClaim:
    # Named-read values are wire-true strings by design; str() makes the
    # display shape total over anything the codec may hand through.
    return AuditClaim(
        predicate=named.predicate,
        args={field: str(value) for field, value in named.args.items()},
    )


def _entry(row: AuditRowNamed) -> AuditEntry:
    return AuditEntry(
        transition_id=row.transition_id,
        committed_at=row.committed_at,
        transformation=row.transformation_name,
        actor=row.actor,
        attestation=(
            AttestationInfo(
                mode=row.attestation.mode, authenticated_by=row.attestation.authenticated_by
            )
            if row.attestation is not None
            else None
        ),
        asserted=[_claim(c) for c in row.asserted_claims],
        retracted=[_claim(c) for c in row.retracted_claims],
        invariants_checked=len(row.invariants_checked),
        intents=len(row.emitted_intents),
    )


def list_audit(client: GlasshouseClient) -> list[AuditEntry]:
    """The whole transition log, newest first."""
    return [_entry(row) for row in reversed(client.audit_named())]


def mentions_org(entry: AuditEntry, org: str) -> bool:
    """Whether any claim this transition asserted or retracted names the
    organisation - the soft highlight, never a filter."""
    return any(claim.args.get("org") == org for claim in (*entry.asserted, *entry.retracted))
