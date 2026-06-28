"""The verify legs' failure shapes, pure: fake binaries play back the
upstream verdicts; the live consistent/divergent paths are proven in
tests/test_verify_integration.py."""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from glasshouse import verify as verify_module
from glasshouse.commit import (
    MODEL_HASH,
    GlasshouseClient,
    missing_catalogued_views,
    views_model_hash,
)
from glasshouse.verify import Leg, VerifyReport, _ledger_leg, _model_leg, _tree_leg, _views_leg
from tests.support import fake_binary

INTACT_TREE = {"status": "intact", "checkpoints": 0, "tree_size": 0}
CONSISTENT_REPLAY = {"status": "consistent", "transitions": 8, "claims": 12}


def client_with(tmp_path: Path, stdout: str) -> GlasshouseClient:
    return GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(fake_binary(tmp_path, stdout))
    )


def _verify_report(tmp_path: Path, replay: dict, tree: dict):  # type: ignore[type-arg, no-untyped-def]
    """The typed `verify` envelope, via a fake binary playing it back."""
    return client_with(tmp_path, json.dumps({"replay": replay, "tree": tree})).verify()


def test_the_model_leg_names_both_hashes_on_divergence(tmp_path: Path) -> None:
    drifted = json.dumps({"program": "glasshouse", "hash": "sha256:0000"})
    leg = _model_leg(client_with(tmp_path, drifted))
    assert not leg.ok
    assert "sha256:0000" in leg.detail
    assert MODEL_HASH in leg.detail


def test_the_model_leg_passes_on_agreement(tmp_path: Path) -> None:
    agreed = json.dumps({"program": "glasshouse", "hash": MODEL_HASH})
    assert _model_leg(client_with(tmp_path, agreed)).ok


def test_the_ledger_leg_reads_the_replay_verdict(tmp_path: Path) -> None:
    leg = _ledger_leg(_verify_report(tmp_path, CONSISTENT_REPLAY, INTACT_TREE))
    assert leg.ok
    assert "8 transition(s) replay to 12 claim(s)" in leg.detail


def test_the_ledger_leg_counts_both_divergence_buckets(tmp_path: Path) -> None:
    divergent = {
        "status": "divergent",
        "only_in_claims_table": [{"predicate": "TradeCaptured", "args": []}],
        "only_in_replay": [],
    }
    leg = _ledger_leg(_verify_report(tmp_path, divergent, INTACT_TREE))
    assert not leg.ok
    assert "1 claim(s) only in the claims table, 0 only in the replay" in leg.detail


def test_the_tree_leg_passes_when_the_history_tree_is_intact(tmp_path: Path) -> None:
    leg = _tree_leg(_verify_report(tmp_path, CONSISTENT_REPLAY, INTACT_TREE))
    assert leg.ok
    assert "intact" in leg.detail


def test_the_tree_leg_names_a_tampered_verdict(tmp_path: Path) -> None:
    tampered = {
        "status": "tampered",
        "tree_size": 5,
        "recorded_root": "sha256:aaaa",
        "recomputed_root": "sha256:bbbb",
    }
    leg = _tree_leg(_verify_report(tmp_path, CONSISTENT_REPLAY, tampered))
    assert not leg.ok
    assert "Tampered" in leg.detail


def _dead_engine() -> sa.Engine:
    return sa.create_engine("postgresql+psycopg://127.0.0.1:1/nowhere")


def test_the_views_leg_passes_when_the_catalogue_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    assert _views_leg(_dead_engine()).ok


def test_the_views_leg_names_both_hashes_on_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: "sha256:0000")
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    leg = _views_leg(_dead_engine())
    assert not leg.ok
    assert "sha256:0000" in leg.detail
    assert MODEL_HASH in leg.detail


def test_the_views_leg_reports_an_unapplied_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: None)
    leg = _views_leg(_dead_engine())
    assert not leg.ok
    assert "not applied" in leg.detail


def test_the_views_leg_catches_a_dropped_view_the_hash_would_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hash still names the committed programme, but a catalogued view is
    # gone: the inventory check fails where the hash alone would pass.
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ("trade_terms",))
    leg = _views_leg(_dead_engine())
    assert not leg.ok
    assert "trade_terms" in leg.detail


def test_views_model_hash_is_none_on_an_unreachable_database() -> None:
    # The real read against a dead database: a SQLAlchemy error is a
    # "not applied" verdict (None), never a raise.
    assert views_model_hash(_dead_engine()) is None


def test_missing_catalogued_views_is_empty_on_an_unreachable_database() -> None:
    # An absent surface reads as a whole inventory (the empty tuple); the
    # not-applied verdict belongs to views_model_hash, not this check.
    assert missing_catalogued_views(_dead_engine()) == ()


def test_the_report_renders_verdict_first() -> None:
    report = VerifyReport((Leg("model", True, "fine"), Leg("ledger", False, "broken")))
    assert not report.ok
    rendered = report.render()
    assert rendered.splitlines()[0] == "glasshouse verify: DIVERGENT"
    assert "FAIL ledger" in rendered
