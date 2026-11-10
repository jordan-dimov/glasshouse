"""Recent refusals: the proposals the rules said no to (glasshouse#52).

The audit history holds what was accepted. A refusal changes nothing, so
it is not there. The substrate keeps refusals in a log of their own and
is plain about what that log is: an operational floor, written at most
once and only after the refused proposal has been rolled back, so a
storm or a failed insert can leave one unrecorded. A row is a lead to
follow, never proof of what did or did not happen.

Every answer from here therefore says two things with the rows. The
caveat, in those terms. And the bound: this is the most recent `limit`
refusals that name the organisation, not all of them, so an empty or a
short answer is never a claim that nothing else was refused.

One read for every surface: the JSON API, the Audit screen and the MCP
tool all come through `list_refusals`.
"""

from __future__ import annotations

from glasshouse.api.schemas import Compared, RecordedRefusal, RefusalLog, WitnessBinding
from glasshouse.commit import GlasshouseClient
from glasshouse.imports.report import comparison, wire_text

CAVEAT = (
    "Best-effort log, not the audit history. A refusal is written at most once, after the "
    "proposal is rolled back, so one can be missing. Accepted changes are in the audit history."
)
DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def list_refusals(
    client: GlasshouseClient, *, org: str, limit: int = DEFAULT_LIMIT, actor: str | None = None
) -> RefusalLog:
    """The most recent refusals naming `org`, newest first, at most
    `limit` of them (clamped to 1..MAX_LIMIT). `actor` narrows the rows
    read, after the bound: it filters what was fetched, it does not
    reach further back."""
    bound = max(1, min(limit, MAX_LIMIT))
    rows = client.rejections(limit=bound, mentions=org)
    return RefusalLog(
        caveat=CAVEAT,
        limit=bound,
        refusals=[
            RecordedRefusal(
                rejected_at=row.rejected_at,
                actor=row.actor,
                transformation=row.transformation_name,
                kind=row.kind,
                rule=row.rule,
                reason=row.reason,
                # Led by the rule's name, with the figures when the kernel
                # pinned them: the same line an import receipt shows.
                summary=f"{row.rule}: {comparison(row.compared)}"
                if row.compared is not None
                else f"{row.rule}: {row.reason}",
                compared=None
                if row.compared is None
                else Compared(
                    op=row.compared.op,
                    left=wire_text(row.compared.left),
                    right=wire_text(row.compared.right),
                ),
                # None is "not captured" and stays None: an empty list
                # would claim the rule was reading nothing.
                witness=None
                if row.witness is None
                else [
                    WitnessBinding(var=binding.var, value=wire_text(binding.value))
                    for binding in row.witness
                ],
            )
            for row in rows
            if actor is None or row.actor == actor
        ],
    )
