"""The tamper-evidence surface against the real binary: a checkpoint
anchors the audit log's Merkle tree, an evidence pack exports it for
offline verification, the pack verifies intact, and a tampered pack is
caught - all through the generated typed methods, no hand-written
bridge (since v0.0.12 the pack streams to a file as NDJSON: a manifest
line, the checkpoints, then every row).

Same gating and provisioning contract as the other integration legs.
"""

import json
from pathlib import Path

import pytest

from glasshouse import cli
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.commit.morpholog_client.envelopes import (
    CheckpointCreated,
    RoleRebindingsEvaluated,
    TreeIntact,
)
from tests.support import BINARY, DB, needs_live_stack, provision

# usefixtures("cli_binary") pins GLASSHOUSE_MORPHOLOG_BIN for the legs
# that drive cli.main (the CLI builds its own client and CI has no
# morpholog on PATH).
pytestmark = [needs_live_stack, pytest.mark.usefixtures("cli_binary")]

ORG, BOOK = "acme-energy", "spec-de"


@pytest.fixture(scope="module")
def anchored() -> GlasshouseClient:
    """A provisioned ledger with a governed write and a checkpoint, so
    the history tree has real rows to prove."""
    provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    assert isinstance(
        client.submit(
            models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
            actor="bootstrap",
        ),
        Committed,
    )
    outcome = client.audit_checkpoint()
    assert isinstance(outcome, CheckpointCreated)
    assert outcome.checkpoint.tree_size > 0
    return client


def test_an_evidence_pack_verifies_intact_offline(
    anchored: GlasshouseClient, tmp_path: Path
) -> None:
    # Export the pack covering a specific checkpoint (no new rows since
    # the fixture, so this names the fixture's checkpoint by tree_size).
    tree_size = anchored.audit_checkpoint().checkpoint.tree_size
    pack = tmp_path / "pack.ndjson"
    manifest = anchored.audit_export(str(pack), tree_size=tree_size)
    assert pack.exists()
    assert (manifest.pack_kind, manifest.tree_size) == ("prefix", tree_size)
    # evidence_verify takes no database - the offline guarantee.
    report = anchored.audit_verify_pack(str(pack))
    assert isinstance(report.verdict, TreeIntact)
    # Every row this binary wrote carries the asserting role's OID, so
    # the rebinding finding is evaluated - and finds no change.
    assert isinstance(report.role_rebindings, RoleRebindingsEvaluated)
    assert report.role_rebindings.changes == []


def _tamper_first_checkpoint(pack: Path) -> None:
    # Corrupt the recorded checkpoint root (line 2 of the pack: the
    # manifest comes first, then the checkpoints): the verifier
    # recomputes it from the rows and the mismatch is the whole point.
    lines = pack.read_text().splitlines()
    checkpoint = json.loads(lines[1])
    root = checkpoint["root_hash"]
    checkpoint["root_hash"] = root[:-1] + ("0" if root[-1] != "0" else "1")
    lines[1] = json.dumps(checkpoint)
    pack.write_text("\n".join(lines) + "\n")


def test_a_tampered_pack_is_caught(anchored: GlasshouseClient, tmp_path: Path) -> None:
    pack = tmp_path / "pack.ndjson"
    anchored.audit_export(str(pack))
    _tamper_first_checkpoint(pack)
    assert not isinstance(anchored.audit_verify_pack(str(pack)).verdict, TreeIntact)


def test_the_cli_checkpoints_exports_and_verifies(
    anchored: GlasshouseClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["checkpoint", "--database-url", DB]) == 0
    assert "checkpoint" in capsys.readouterr().out
    pack = tmp_path / "cli-pack.ndjson"
    assert cli.main(["evidence-export", str(pack), "--database-url", DB]) == 0
    assert pack.exists()
    assert cli.main(["evidence-verify", str(pack)]) == 0
    assert "evidence verify: intact" in capsys.readouterr().out


def test_offline_verification_ignores_the_deployment_environment(
    anchored: GlasshouseClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The forensic command is run by a third party against a pack and an
    # anchor, on a machine whose environment is none of their business.
    # Settings a hosted deployment would refuse to boot with must not
    # stop a valid pack from being verified.
    pack = tmp_path / "forensic-pack.ndjson"
    assert cli.main(["evidence-export", str(pack), "--database-url", DB]) == 0
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", "short")
    monkeypatch.setenv("GLASSHOUSE_AUDIT_WRITER_ROLES", "[broken")
    assert cli.main(["evidence-verify", str(pack)]) == 0
    assert "evidence verify: intact" in capsys.readouterr().out


def test_the_anchor_round_trip(
    anchored: GlasshouseClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The full external-anchor workflow: checkpoint writes an anchor
    # file, the pack is exported, and verify checks the pack extends the
    # externally-held anchor (the check a coordinated rewrite cannot pass).
    anchor = tmp_path / "anchor.json"
    assert cli.main(["checkpoint", "--out", str(anchor), "--database-url", DB]) == 0
    assert anchor.exists()
    assert "anchor written to" in capsys.readouterr().out
    pack = tmp_path / "pack.ndjson"
    assert cli.main(["evidence-export", str(pack), "--database-url", DB]) == 0
    assert cli.main(["evidence-verify", str(pack), "--anchor", str(anchor)]) == 0
    assert "evidence verify: intact" in capsys.readouterr().out


def test_the_cli_verify_fails_a_tampered_pack(
    anchored: GlasshouseClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = tmp_path / "tampered.ndjson"
    anchored.audit_export(str(pack))
    _tamper_first_checkpoint(pack)
    assert cli.main(["evidence-verify", str(pack)]) == 1
    assert "evidence verify: intact" not in capsys.readouterr().out
