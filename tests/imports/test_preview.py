"""The why renderer and the preview wiring, against canned explanation
verdicts on a fake binary. The ledger-true previews (refusals naming
missing grants) live in the integration leg."""

import json
from pathlib import Path

from glasshouse.commit import GlasshouseClient, envelopes
from glasshouse.imports import preview_trades, why
from tests.imports.test_trades import HEADER, MIXED
from tests.support import fake_binary

REFUSAL = {
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
}

ADMISSIBLE = {
    "transition": {"transformation": "capture_trade", "args": [], "actor": "alice"},
    "verdict": "admissible",
}


def test_why_renders_each_rejection_kind() -> None:
    refused = envelopes.Explanation.from_json(REFUSAL)
    assert (
        why(refused) == "missing MayCaptureTrade(mallory, acme-energy, spec-de) "
        "(supplied by grant_capture_authority)"
    )
    invariant = envelopes.Explanation.from_json(
        {
            "transition": REFUSAL["transition"],
            "verdict": {
                "rejected": {"kind": "invariant", "name": "quantity_is_positive", "rule": "..."}
            },
        }
    )
    assert why(invariant) == "would break invariant quantity_is_positive"
    errored = envelopes.Explanation.from_json(
        {
            "transition": REFUSAL["transition"],
            "verdict": {"rejected": {"kind": "error", "message": "kernel said no"}},
        }
    )
    assert why(errored) == "kernel said no"
    assert why(envelopes.Explanation.from_json(ADMISSIBLE)) == "admissible"
    assert why(None) == ""


def test_preview_dry_runs_every_surviving_row(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, json.dumps(REFUSAL))
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    report = preview_trades(client, MIXED, org="acme-energy", actor="mallory")

    # The three dishonest rows quarantine before any explain; the two
    # honest ones get the ledger's dry-run verdict, nothing committed.
    assert report.count("quarantined") == 3
    assert report.count("refused") == 2
    assert report.count("committed") == 0
    refused = [o for o in report.outcomes if o.status == "refused"]
    assert all("missing MayCaptureTrade" in o.detail for o in refused)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[0] == "explain"
    assert "--json" in argv


def test_an_all_quarantined_preview_never_reaches_the_binary(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, json.dumps(REFUSAL))
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    text = "\n".join([HEADER, MIXED.splitlines()[3]])  # one bad-decimal row only
    report = preview_trades(client, text, org="acme-energy", actor="alice")
    assert report.count("quarantined") == 1
    assert len(report.outcomes) == 1
    assert not (tmp_path / "argv.txt").exists()
