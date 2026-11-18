"""Proof of a number: what the trades are worth, stated as an audit
receipt anyone can check offline.

The Overview's total per currency is the ledger's own derived read,
`BusinessDayTotal`, not a sum the screen makes. Morpholog can state such a
read as a receipt: under this programme (named by its hash), over the
history a checkpoint commits to, the read gives this answer. A third party
holding the receipt, the evidence pack it was taken over and the rule file
recomputes the answer with no database (`morpholog audit verify-receipt`),
and learns that the figures follow from that history under those rules,
or exactly which layer does not hold.

What a proof is, honestly:

- It is taken at a checkpoint, the one recorded when the proof is asked
  for (or an earlier one, by its size). A moment never checkpointed cannot
  be proved later, and a write landing after the checkpoint is not in it.
- The pack is the whole record up to that checkpoint, every organisation
  in the ledger included: receipts need a complete prefix, since a pack
  of selected rows proves its rows genuine but not that none are missing
  (per-key completeness is morpholog#380). It is confidential data, as
  the Audit screen's evidence pack is.
- The answer covers every organisation and every currency in the ledger;
  nothing narrows a derived read. The reader picks out the row they need.

Each proof is verified here before it is handed over, so a proof that
would not verify is an error, never a download.
"""

from __future__ import annotations

import datetime as dt
import shutil
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from glasshouse.commit import MODEL_FILE, GlasshouseClient, envelopes
from glasshouse.commit.morpholog_client import MORPHOLOG_VERSION

# The derived read a proof states.
PROVED_READ = "BusinessDayTotal"

PACK = "evidence-pack.ndjson"
RECEIPT = "receipt.json"
RULES = "glasshouse.morph"
README = "README.txt"


class ProofError(RuntimeError):
    """A proof that did not verify where it was made: nothing is handed
    over, and the layer that failed is named."""


@dataclass(frozen=True)
class ProvedTotal:
    """One row of the receipt's answer: an organisation's marks for its
    business date in one currency, and how many marks that is."""

    org: str
    business_date: dt.date
    currency: str
    total: Decimal
    valued: int


@dataclass(frozen=True)
class Proof:
    """A verified receipt and where its files are."""

    directory: Path
    tree_size: int
    checkpoint_hash: str
    program_hash: str
    totals: list[ProvedTotal]

    def for_org(self, org: str) -> list[ProvedTotal]:
        return [row for row in self.totals if row.org == org]


def proved_total(claim: envelopes.ClaimInstance) -> ProvedTotal:
    """One row of a receipt's answer, read by field."""
    # The answer arrives decoded, in the predicate's field order: org,
    # business_date, currency, total, valued.
    org, business_date, currency, total, valued = claim.args
    if not (
        isinstance(org, str)
        and isinstance(business_date, dt.date)
        and isinstance(currency, str)
        and isinstance(total, Decimal)
        and isinstance(valued, Decimal)
    ):
        raise ProofError(f"the receipt's answer is not a {PROVED_READ} row: {claim.args!r}")
    return ProvedTotal(org, business_date, currency, total, int(valued))


def _failed_layers(report: envelopes.ReceiptVerificationReport) -> str:
    layers = {
        "receipt": report.receipt,
        "completeness": report.completeness,
        "checkpoint": report.checkpoint,
        "program": report.program,
        "evaluation": report.evaluation,
    }
    fine = {"well_formed", "complete", "matches", "reproduced"}
    # A layer is a status word, or an object carrying the detail; its
    # class name is the status in that case (CheckpointDiffers, ...).
    failed = [
        f"{name}: {value if isinstance(value, str) else type(value).__name__}"
        for name, value in layers.items()
        if value not in fine
    ]
    if not report.evidence.passes:
        failed.insert(0, f"evidence: {type(report.evidence.verdict).__name__}")
    return "; ".join(failed) or "the binary's verdict was a fail"


def _readme(proof: Proof) -> str:
    rows = "\n".join(
        f"  {row.org}  {row.business_date.isoformat()}  {row.currency}  "
        f"{row.total}  ({row.valued} mark{'s' if row.valued != 1 else ''})"
        for row in proof.totals
    )
    return f"""\
Glasshouse: proof of what the trades are worth on the business date

The receipt ({RECEIPT}) states that, under the rules in {RULES}
(programme {proof.program_hash}), over the record up to checkpoint
{proof.checkpoint_hash} ({proof.tree_size} transitions, in {PACK}),
the ledger's read {PROVED_READ} gives:

  organisation  business date  currency  total  (marks)
{rows or "  (no organisation has a business date with a mark yet)"}

To check it with no database, using Morpholog {MORPHOLOG_VERSION}:

  morpholog audit verify-receipt {RULES} --receipt {RECEIPT} --pack {PACK}

It passes only if the pack is intact and complete, its checkpoint and the
rules are the receipt's, and recomputing the read gives the same answer.
Add --anchor <checkpoint.json> to tie the pack to a checkpoint you hold
yourself.

The pack is the whole record up to the checkpoint, every organisation
included: treat it as confidential. Writes after the checkpoint are not
in this proof.
"""


def prove_totals(
    client: GlasshouseClient, directory: Path, *, tree_size: int | None = None
) -> Proof:
    """Write a verified proof of the business-day totals into `directory`:
    the evidence pack, the receipt, the rule file and a README. With no
    `tree_size`, a checkpoint is recorded now (or the unchanged head
    reused) and the proof is taken there; with one, at that existing
    checkpoint."""
    directory.mkdir(parents=True, exist_ok=True)
    if tree_size is None:
        tree_size = client.audit_checkpoint().checkpoint.tree_size
    pack, receipt_file = directory / PACK, directory / RECEIPT
    client.audit_export(str(pack), tree_size=tree_size)
    receipt = client.audit_receipt(PROVED_READ, str(pack), str(receipt_file))
    shutil.copyfile(MODEL_FILE, directory / RULES)
    report = client.audit_verify_receipt(str(receipt_file), str(pack))
    if not report.passes:
        # A receipt left beside its pack would read as a proof: remove it.
        receipt_file.unlink()
        raise ProofError(f"the proof did not verify: {_failed_layers(report)}")
    proof = Proof(
        directory=directory,
        tree_size=receipt.checkpoint.tree_size,
        checkpoint_hash=receipt.checkpoint.checkpoint_hash,
        program_hash=receipt.program_hash,
        totals=sorted(
            (proved_total(claim) for claim in receipt.answer),
            key=lambda row: (row.org, row.currency),
        ),
    )
    (directory / README).write_text(_readme(proof), encoding="utf-8")
    return proof


def bundle(proof: Proof, archive: Path) -> Path:
    """The proof's four files as one zip, for a download."""
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as out:
        for name in (README, RECEIPT, RULES, PACK):
            out.write(proof.directory / name, arcname=name)
    return archive
