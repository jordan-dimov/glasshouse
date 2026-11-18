"""A proof that does not verify where it is made is never handed over,
and the error names the layer that failed; the receipt's answer is read
by field. Against reports the v0.0.20 binary really printed
(tests/fixtures/receipt-*.json), with no database.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from glasshouse import receipts
from glasshouse.commit import envelopes

FIXTURES = Path(__file__).parent / "fixtures"
RECEIPT = FIXTURES / "receipt-business-day-total.json"
DIFFERS = FIXTURES / "receipt-checkpoint-differs.json"


class _Stub:
    """Only what `prove_totals` asks of a client, answering from the
    fixtures: the receipt issues, its verification fails."""

    def __init__(self) -> None:
        self.verified = 0

    def audit_checkpoint(self) -> Any:
        raise AssertionError("a proof at a given size records no checkpoint")

    def audit_export(self, path: str, tree_size: int | None = None) -> None:
        assert tree_size == 78
        Path(path).write_text("")

    def audit_receipt(self, derived: str, pack: str, path: str) -> envelopes.EvaluationReceipt:
        assert derived == receipts.PROVED_READ
        Path(path).write_text(RECEIPT.read_text())
        return envelopes.EvaluationReceipt.from_json(json.loads(RECEIPT.read_text()))

    def audit_verify_receipt(self, receipt: str, pack: str) -> envelopes.ReceiptVerificationReport:
        self.verified += 1
        return envelopes.ReceiptVerificationReport.from_json(json.loads(DIFFERS.read_text()))


def test_a_proof_that_does_not_verify_is_refused_by_its_layer(tmp_path: Path) -> None:
    stub = _Stub()
    with pytest.raises(receipts.ProofError) as refused:
        receipts.prove_totals(stub, tmp_path, tree_size=78)  # type: ignore[arg-type]
    assert stub.verified == 1
    assert "checkpoint: CheckpointDiffers" in str(refused.value)
    assert "evaluation: not_evaluated" in str(refused.value)
    # Nothing that reads as a finished proof is left behind.
    assert not (tmp_path / receipts.README).exists()
    assert not (tmp_path / receipts.RECEIPT).exists()


def test_the_answer_is_read_by_field() -> None:
    receipt = envelopes.EvaluationReceipt.from_json(json.loads(RECEIPT.read_text()))
    rows = [receipts.proved_total(claim) for claim in receipt.answer]
    assert rows == [
        receipts.ProvedTotal("acme-energy", dt.date(2026, 7, 1), "EUR", Decimal("390.5"), 8),
        receipts.ProvedTotal("acme-energy", dt.date(2026, 7, 1), "GBP", Decimal("140"), 2),
    ]


def test_an_answer_of_another_shape_is_refused() -> None:
    wrong = envelopes.ClaimInstance(
        predicate="BusinessDayTotal", args=["o", "not a date", "EUR", 1, 2]
    )
    with pytest.raises(receipts.ProofError, match="not a BusinessDayTotal row"):
        receipts.proved_total(wrong)
