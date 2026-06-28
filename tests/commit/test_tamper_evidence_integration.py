"""The tamper-evidence surface against the real binary: a checkpoint
anchors the audit log's Merkle tree, an evidence pack exports it for
offline verification, the pack verifies intact, and a tampered pack is
caught - all through the generated typed methods plus our thin file
export, no hand-written bridge.

Same gating and provisioning contract as the other integration legs.
"""

import json
from pathlib import Path

import pytest

from glasshouse import cli
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.commit.morpholog_client.envelopes import CheckpointCreated, TreeIntact
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

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
    outcome = client.checkpoint()
    assert isinstance(outcome, CheckpointCreated)
    assert outcome.checkpoint.tree_size > 0
    return client


def test_an_evidence_pack_verifies_intact_offline(
    anchored: GlasshouseClient, tmp_path: Path
) -> None:
    # Export the pack covering a specific checkpoint (no new rows since
    # the fixture, so this names the fixture's checkpoint by tree_size).
    tree_size = anchored.checkpoint().checkpoint.tree_size
    pack = tmp_path / "pack.json"
    anchored.export_evidence_pack(pack, tree_size=tree_size)
    assert pack.exists()
    # evidence_verify takes no database - the offline guarantee.
    assert isinstance(anchored.evidence_verify(str(pack)), TreeIntact)


def test_a_tampered_pack_is_caught(anchored: GlasshouseClient, tmp_path: Path) -> None:
    pack = tmp_path / "pack.json"
    anchored.export_evidence_pack(pack)
    data = json.loads(pack.read_text())
    # Corrupt the recorded checkpoint root: the verifier recomputes it
    # from the rows and the mismatch is the whole point.
    root = data["checkpoints"][0]["root_hash"]
    data["checkpoints"][0]["root_hash"] = root[:-1] + ("0" if root[-1] != "0" else "1")
    pack.write_text(json.dumps(data))
    assert not isinstance(anchored.evidence_verify(str(pack)), TreeIntact)


def test_the_cli_checkpoints_exports_and_verifies(
    anchored: GlasshouseClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["checkpoint", "--database-url", DB]) == 0
    assert "checkpoint" in capsys.readouterr().out
    pack = tmp_path / "cli-pack.json"
    assert cli.main(["evidence-export", str(pack), "--database-url", DB]) == 0
    assert pack.exists()
    assert cli.main(["evidence-verify", str(pack)]) == 0
    assert "evidence verify: intact" in capsys.readouterr().out


def test_the_cli_verify_fails_a_tampered_pack(
    anchored: GlasshouseClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pack = tmp_path / "tampered.json"
    anchored.export_evidence_pack(pack)
    data = json.loads(pack.read_text())
    root = data["checkpoints"][0]["root_hash"]
    data["checkpoints"][0]["root_hash"] = root[:-1] + ("0" if root[-1] != "0" else "1")
    pack.write_text(json.dumps(data))
    assert cli.main(["evidence-verify", str(pack)]) == 1
    assert "evidence verify: intact" not in capsys.readouterr().out
