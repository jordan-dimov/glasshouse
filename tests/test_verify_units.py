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
from glasshouse.commit.morpholog_client import MORPHOLOG_VERSION
from glasshouse.commit.morpholog_client.envelopes import (
    ViewsIntact,
    ViewsNotSealed,
    ViewsTampered,
)
from glasshouse.compute.store import CurveStore
from glasshouse.projections import accumulate
from glasshouse.projections.tables import metadata as projection_metadata
from glasshouse.verify import (
    Leg,
    VerifyReport,
    _ledger_leg,
    _model_leg,
    _tree_leg,
    _views_leg,
    verify,
)
from tests.support import fake_binary, stamps

INTACT_TREE = {"status": "intact", "checkpoints": 0, "tree_size": 0}
CONSISTENT_REPLAY = {"status": "consistent", "transitions": 8, "claims": 12}
INTACT_SEAL = ViewsIntact(views_checked=11)


def client_with(
    tmp_path: Path, stdout: str, hash_report: dict[str, object] | None = None
) -> GlasshouseClient:
    return GlasshouseClient(
        "model.morph",
        "postgres:///x",
        binary=str(fake_binary(tmp_path, stdout, hash_report=hash_report)),
    )


def _verify_report(tmp_path: Path, replay: dict, tree: dict):  # type: ignore[type-arg, no-untyped-def]
    """The typed `verify` envelope, via a fake binary playing it back."""
    report = {"replay": replay, "tree": tree, "role_rebindings": {"status": "not_evaluated"}}
    return client_with(tmp_path, json.dumps(report)).audit_verify()


def test_the_replay_covers_every_projection_table(tmp_path: Path) -> None:
    # The projection leg compares every table in the projection metadata,
    # so a table the replay forgot to return would fail the leg rather
    # than pass unverified. This pins the other half: the replay returns
    # exactly that set, over an empty tail.
    replay = accumulate(client_with(tmp_path, ""))  # an empty tail
    assert set(replay) == set(projection_metadata.tables)


def test_the_model_leg_names_the_binary_version_and_the_hash(tmp_path: Path) -> None:
    leg = _model_leg(client_with(tmp_path, ""))
    assert leg.ok
    assert f"morpholog {MORPHOLOG_VERSION}" in leg.detail
    assert MODEL_HASH in leg.detail


@pytest.mark.parametrize(
    "changed", [{"hash": "sha256:0000"}, {"morpholog_version": "0.0.99"}], ids=["rules", "binary"]
)
def test_the_model_leg_keeps_attesting_after_the_first_use_check(
    tmp_path: Path, changed: dict[str, object]
) -> None:
    # The client's check runs once, before its first call, and by upstream's
    # contract does not notice a binary or programme replaced under a
    # client already checked - the web app's client lives for the whole
    # process. verify is continuing attestation, so the leg compares the
    # live report against both committed stamps every time.
    client = client_with(tmp_path, "")
    assert client.audit() == []  # the first-use check passed here
    (tmp_path / "hash.txt").write_text(json.dumps(stamps(**changed)))
    leg = _model_leg(client)
    assert not leg.ok
    assert str(next(iter(changed.values()))) in leg.detail
    assert MODEL_HASH in leg.detail
    assert MORPHOLOG_VERSION in leg.detail


def test_a_refused_client_fails_every_ledger_leg_by_name(tmp_path: Path) -> None:
    # The client's first-use check (a binary of another version here)
    # refuses before any leg can run. verify does not raise: every leg is
    # total, so each client-backed leg carries the refusal (the client
    # remembers it), the two database-backed legs name the dead database
    # they met, and the whole report is DIVERGENT - never a crash and
    # never "ok".
    client = client_with(tmp_path, "", hash_report=stamps(morpholog_version="0.0.1"))
    engine = _dead_engine()
    try:
        report = verify(client, engine, CurveStore(engine))
    finally:
        engine.dispose()
    assert not report.ok
    by_name = {leg.name: leg for leg in report.legs}
    assert all(not leg.ok for leg in report.legs)
    for name in ("model", "ledger", "tree", "payloads"):
        assert by_name[name].detail.startswith("could not run: the binary is Morpholog 0.0.1")
    for name in ("projections", "views"):
        assert by_name[name].detail.startswith("could not run: ")


def test_a_binary_that_cannot_start_fails_every_leg_instead_of_raising() -> None:
    # The generated adapter translates a timeout into MorphologError but
    # lets a missing or non-executable binary raise OSError; verify is
    # total over that too, because a mis-set GLASSHOUSE_MORPHOLOG_BIN is
    # exactly the deployment failure a verdict is for.
    client = GlasshouseClient("model.morph", "postgres:///x", binary="/definitely/not/here")
    engine = _dead_engine()
    try:
        report = verify(client, engine, CurveStore(engine))
    finally:
        engine.dispose()
    assert len(report.legs) == 6
    assert all(not leg.ok for leg in report.legs)
    assert all(leg.detail.startswith("could not run: ") for leg in report.legs)
    assert "not/here" in report.legs[0].detail


def test_an_unreachable_database_is_not_an_unapplied_surface() -> None:
    # Only an undefined schema or catalogue reads as "not applied"; a
    # database that cannot be reached is an operational failure the views
    # leg reports as such (through `_guarded`), never as a surface that was
    # never applied.
    engine = _dead_engine()
    try:
        with pytest.raises(sa.exc.OperationalError):
            views_model_hash(engine)
        with pytest.raises(sa.exc.OperationalError):
            missing_catalogued_views(engine)
    finally:
        engine.dispose()


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


def test_the_views_leg_passes_when_the_catalogue_and_seal_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    leg = _views_leg(_dead_engine(), INTACT_SEAL)
    assert leg.ok
    assert "seal intact over 11 view(s)" in leg.detail


def test_the_views_leg_names_both_hashes_on_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    # The local hash check decides without a seal verdict (the shared
    # verify call may have failed; the drift is evident regardless).
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: "sha256:0000")
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    leg = _views_leg(_dead_engine(), None)
    assert not leg.ok
    assert "sha256:0000" in leg.detail
    assert MODEL_HASH in leg.detail


def test_the_views_leg_reports_an_unapplied_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: None)
    leg = _views_leg(_dead_engine(), None)
    assert not leg.ok
    assert "not applied" in leg.detail


def test_the_views_leg_catches_a_dropped_view_the_hash_would_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hash still names the committed programme, but a catalogued view is
    # gone: the inventory check fails where the hash alone would pass.
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ("trade_terms",))
    leg = _views_leg(_dead_engine(), None)
    assert not leg.ok
    assert "trade_terms" in leg.detail


def test_the_views_leg_cannot_claim_ok_without_a_seal_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Local checks clean but the shared verify call failed: "ok" now
    # means unredefined, which only the seal can attest.
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    leg = _views_leg(_dead_engine(), None)
    assert not leg.ok
    assert "seal unverified" in leg.detail


def test_the_views_leg_fails_an_unsealed_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    # Upstream reports not_sealed and passes; Glasshouse fails it - the
    # committed script seals at apply time, so an unsealed live surface
    # is not the committed surface, and one re-apply seals it.
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    leg = _views_leg(_dead_engine(), ViewsNotSealed())
    assert not leg.ok
    assert "re-apply the inspection model" in leg.detail


def test_the_views_leg_names_the_redefined_and_missing_views(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(verify_module, "views_model_hash", lambda _engine: MODEL_HASH)
    monkeypatch.setattr(verify_module, "missing_catalogued_views", lambda _engine: ())
    leg = _views_leg(
        _dead_engine(), ViewsTampered(mismatched=["trade_terms"], missing=["official_curve"])
    )
    assert not leg.ok
    assert "redefined in place: trade_terms" in leg.detail
    assert "seal or view missing: official_curve" in leg.detail


def test_the_report_renders_verdict_first() -> None:
    report = VerifyReport((Leg("model", True, "fine"), Leg("ledger", False, "broken")))
    assert not report.ok
    rendered = report.render()
    assert rendered.splitlines()[0] == "glasshouse verify: DIVERGENT"
    assert "FAIL ledger" in rendered
