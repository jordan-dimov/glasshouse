"""The hand-written sliver of the commit zone: `GlasshouseClient.read`,
the typed per-predicate read with `--as-of` that bridges the generated
client's gap (see client.py), exercised against a fake binary."""

import datetime as dt
import json
from pathlib import Path

import pytest

from glasshouse.commit import GlasshouseClient, MorphologError, envelopes, models
from tests.support import fake_binary

NAMED_OFFICIAL_CURVE = json.dumps(
    [
        {
            "predicate": "OfficialCurve",
            "args": {
                "org": "acme-energy",
                "market": "de-power",
                "as_of": "2026-06-08",
                "version": "crv-v1",
            },
        }
    ]
)


def client(tmp_path: Path) -> GlasshouseClient:
    binary = fake_binary(tmp_path, NAMED_OFFICIAL_CURVE)
    return GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))


def test_read_decodes_by_declared_kind_through_the_named_surface(tmp_path: Path) -> None:
    (row,) = client(tmp_path).read(models.OfficialCurveClaim)
    assert row.version == "crv-v1"
    assert row.as_of == dt.date(2026, 6, 8)  # a date, not wire text
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["inspect", "claims"]
    assert argv[argv.index("--predicate") + 1] == "OfficialCurve"
    assert argv[argv.index("--named") + 1] == "model.morph"
    assert "--as-of" not in argv


def test_read_as_of_reaches_the_cli(tmp_path: Path) -> None:
    client(tmp_path).read(models.OfficialCurveClaim, as_of="0197-transition-id")
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[argv.index("--as-of") + 1] == "0197-transition-id"


def test_binary_discovery_honours_the_glasshouse_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One name across app, docs and commit zone: GLASSHOUSE_MORPHOLOG_BIN
    # wins when no binary is passed; an explicit argument still wins over
    # the environment.
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/opt/glasshouse/morpholog")
    assert GlasshouseClient("m.morph", "postgres:///x").binary == "/opt/glasshouse/morpholog"
    assert GlasshouseClient("m.morph", "postgres:///x", binary="explicit").binary == "explicit"
    monkeypatch.delenv("GLASSHOUSE_MORPHOLOG_BIN")
    monkeypatch.setenv("MORPHOLOG_BIN", "/usr/local/bin/morpholog")
    assert GlasshouseClient("m.morph", "postgres:///x").binary == "/usr/local/bin/morpholog"


def test_operations_are_bounded_when_a_timeout_is_set(tmp_path: Path) -> None:
    sleeper = tmp_path / "fake-morpholog"
    sleeper.write_text("#!/bin/sh\nsleep 5\n")
    sleeper.chmod(0o755)
    bounded = GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(sleeper), timeout_seconds=0.1
    )
    with pytest.raises(MorphologError, match=r"timed out after 0\.1"):
        bounded.hash()


CONSISTENT = json.dumps({"status": "consistent", "transitions": 8, "claims": 12})


def test_verify_ledger_parses_the_upstream_verdict(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, CONSISTENT)
    bridged = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    assert bridged.verify_ledger() == {"status": "consistent", "transitions": 8, "claims": 12}
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[0] == "verify"


BATCH_EXPLAINED = json.dumps(
    {
        "status": "rejected",
        "reason": "gate refused",
        "row": 1,
        "explanation": {
            "transition": {"transformation": "capture_trade", "args": [], "actor": "mallory"},
            "verdict": {
                "rejected": {
                    "kind": "gate",
                    "gate": "require MayCaptureTrade(actor, org, book)",
                    "statement_kind": "require",
                    "directly_missing_claims": [
                        {
                            "predicate": "MayCaptureTrade",
                            "rendered": "MayCaptureTrade(mallory, acme-energy, spec-de)",
                            "candidate_supplier_transformations": ["grant_capture_authority"],
                        }
                    ],
                }
            },
        },
    }
)


def test_run_batch_composes_explain_on_reject(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, BATCH_EXPLAINED)
    bridged = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    (receipt,) = bridged.run_batch(
        [{"transformation": "capture_trade", "actor": "mallory", "args_named": {}}],
        explain_on_reject=True,
    )
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert "--explain-on-reject" in argv
    assert isinstance(receipt.outcome, envelopes.Rejected)
    assert receipt.outcome.explanation is not None
    assert not receipt.outcome.explanation.admissible


def test_run_batch_omits_the_flag_by_default(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, BATCH_EXPLAINED)
    bridged = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    bridged.run_batch([{"transformation": "t", "actor": "a", "args_named": {}}])
    assert "--explain-on-reject" not in (tmp_path / "argv.txt").read_text().splitlines()


def test_run_batch_raises_on_operational_abort(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, BATCH_EXPLAINED, stderr="connection lost", exit_code=1)
    bridged = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    with pytest.raises(MorphologError, match="batch aborted after 1 receipt"):
        bridged.run_batch([{"transformation": "t", "actor": "a", "args_named": {}}])


def test_run_batch_is_bounded_when_a_timeout_is_set(tmp_path: Path) -> None:
    sleeper = tmp_path / "fake-morpholog"
    sleeper.write_text("#!/bin/sh\nsleep 5\n")
    sleeper.chmod(0o755)
    bounded = GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(sleeper), timeout_seconds=0.1
    )
    with pytest.raises(MorphologError, match="batch timed out"):
        bounded.run_batch([{"transformation": "t", "actor": "a", "args_named": {}}])


def test_verify_ledger_refuses_a_non_object_verdict(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, "[1, 2]")
    bridged = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    with pytest.raises(MorphologError, match="non-object verdict"):
        bridged.verify_ledger()
