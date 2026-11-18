"""Proof of the business-day totals against the real stack, on the seeded
book: the receipt states what the Overview shows, it verifies offline,
a tampered pack fails it, a pack at another checkpoint is told apart
from a wrong answer, an earlier checkpoint proves the earlier figure, and
the CLI and the Overview's download hand over the same four files.
"""

from __future__ import annotations

import io
import json
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from glasshouse import cli
from glasshouse.api import business_day
from glasshouse.api.app import create_app
from glasshouse.commit import GlasshouseClient, envelopes, models
from glasshouse.receipts import PACK, README, RECEIPT, RULES, prove_totals
from glasshouse.seed import ORG
from tests.support import BINARY, DB, committed, needs_live_stack, seeded_ledger

pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def ledger() -> Any:
    client, engine = seeded_ledger()
    engine.dispose()
    return client


def test_the_receipt_states_what_the_overview_shows(
    ledger: GlasshouseClient, tmp_path: Path
) -> None:
    proof = prove_totals(ledger, tmp_path)
    proved = {(r.currency, r.total, r.valued) for r in proof.for_org(ORG)}
    assert proved == {("EUR", Decimal("390.5"), 8), ("GBP", Decimal("140"), 2)}
    shown = business_day.read(ledger, org=ORG).totals()
    assert {(s.currency, s.total_mtm, s.trade_count) for s in shown} == proved
    # The ledger's totals are the sum of the marks the per-trade read
    # calls today's: the two derived reads share one definition.
    rows = business_day.read(ledger, org=ORG).rows
    for currency, total, valued in proved:
        today = [r for r in rows if r.valued and r.currency == currency]
        assert (sum(r.mtm for r in today), len(today)) == (total, valued)
    for name in (PACK, RECEIPT, RULES, README):
        assert (tmp_path / name).is_file()
    readme = (tmp_path / README).read_text()
    assert "morpholog audit verify-receipt glasshouse.morph" in readme
    assert "390.5" in readme
    assert "confidential" in readme


def test_a_tampered_pack_fails_the_receipt(ledger: GlasshouseClient, tmp_path: Path) -> None:
    prove_totals(ledger, tmp_path)
    pack = tmp_path / PACK
    lines = pack.read_text().splitlines(keepends=True)
    # Change one recorded figure in the history, keeping the line valid
    # JSON: the Merkle root no longer matches its checkpoint.
    target = next(i for i, line in enumerate(lines) if '"25"' in line)
    lines[target] = lines[target].replace('"25"', '"2500"', 1)
    pack.write_text("".join(lines))
    report = ledger.audit_verify_receipt(str(tmp_path / RECEIPT), str(pack))
    assert report.passes is False
    assert report.evidence.passes is False


def test_another_checkpoint_is_told_apart_from_a_wrong_answer(
    ledger: GlasshouseClient, tmp_path: Path
) -> None:
    earlier = prove_totals(ledger, tmp_path / "earlier")
    # The book changes after the proof: a new cap, then a new checkpoint.
    committed(
        ledger,
        models.SetPositionLimitRequest(org=ORG, book="spec-de", cap=Decimal("26")),
        "risk-manager",
    )
    later = prove_totals(ledger, tmp_path / "later")
    assert later.tree_size > earlier.tree_size
    report = ledger.audit_verify_receipt(
        str(tmp_path / "earlier" / RECEIPT), str(tmp_path / "later" / PACK)
    )
    assert report.passes is False
    assert isinstance(report.checkpoint, envelopes.CheckpointDiffers)
    # And the earlier checkpoint can still be proved, by its size.
    again = prove_totals(ledger, tmp_path / "again", tree_size=earlier.tree_size)
    assert again.checkpoint_hash == earlier.checkpoint_hash
    assert again.totals == earlier.totals


def test_the_cli_and_the_overview_hand_over_the_same_proof(
    ledger: GlasshouseClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = CliRunner().invoke(
        cli.app, ["prove-totals", str(tmp_path / "cli"), "--database-url", DB]
    )
    assert written.exit_code == 0, written.output
    assert "EUR 390.5 (8 mark(s))" in written.output
    receipt = json.loads((tmp_path / "cli" / RECEIPT).read_text())
    assert receipt["query"] == {"kind": "derived", "predicate": "BusinessDayTotal"}

    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    with TestClient(create_app()) as client:
        page = client.get("/ui", params={"org": ORG}).text
        download = client.post("/ui/overview/proof")
    assert "Download a proof of these totals" in page
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert "glasshouse-totals-proof-" in download.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert sorted(archive.namelist()) == sorted([README, RECEIPT, RULES, PACK])
        archive.extractall(tmp_path / "web")
    report = ledger.audit_verify_receipt(
        str(tmp_path / "web" / RECEIPT), str(tmp_path / "web" / PACK)
    )
    assert report.passes is True
