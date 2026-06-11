"""The verify legs' failure shapes, pure: fake binaries play back the
upstream verdicts; the live consistent/divergent paths are proven in
tests/test_verify_integration.py."""

import json
from pathlib import Path

from glasshouse.commit import MODEL_HASH, GlasshouseClient
from glasshouse.verify import Leg, VerifyReport, _ledger_leg, _model_leg
from tests.support import fake_binary


def client_with(tmp_path: Path, stdout: str) -> GlasshouseClient:
    return GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(fake_binary(tmp_path, stdout))
    )


def test_the_model_leg_names_both_hashes_on_divergence(tmp_path: Path) -> None:
    drifted = json.dumps({"program": "glasshouse", "hash": "sha256:0000"})
    leg = _model_leg(client_with(tmp_path, drifted))
    assert not leg.ok
    assert "sha256:0000" in leg.detail
    assert MODEL_HASH in leg.detail


def test_the_model_leg_passes_on_agreement(tmp_path: Path) -> None:
    agreed = json.dumps({"program": "glasshouse", "hash": MODEL_HASH})
    assert _model_leg(client_with(tmp_path, agreed)).ok


def test_the_ledger_leg_counts_both_divergence_buckets(tmp_path: Path) -> None:
    divergent = json.dumps(
        {
            "status": "divergent",
            "only_in_claims_table": [{"predicate": "TradeCaptured", "args": []}],
            "only_in_replay": [],
        }
    )
    leg = _ledger_leg(client_with(tmp_path, divergent))
    assert not leg.ok
    assert "1 claim(s) only in the claims table, 0 only in the replay" in leg.detail


def test_the_report_renders_verdict_first() -> None:
    report = VerifyReport((Leg("model", True, "fine"), Leg("ledger", False, "broken")))
    assert not report.ok
    rendered = report.render()
    assert rendered.splitlines()[0] == "glasshouse verify: DIVERGENT"
    assert "FAIL ledger" in rendered
