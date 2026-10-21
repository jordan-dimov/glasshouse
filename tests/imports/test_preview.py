"""The why renderer and the preview wiring, against canned explanation
verdicts on a fake binary. The ledger-true previews (refusals naming
missing grants) live in the integration leg."""

import datetime as dt
import json
from pathlib import Path

from glasshouse.commit import GlasshouseClient, envelopes
from glasshouse.imports import preview_trades, why
from glasshouse.imports.report import wire_text
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


def test_why_leads_with_the_named_rule_and_the_values_it_compared() -> None:
    # From v0.0.15 a refusal carries the two values its failing comparison
    # compared, and every Glasshouse gate has a name: the line a desk
    # reads is the ledger's own numbers under the rule's own name, with
    # nothing recomputed here.
    late = envelopes.Explanation.from_json(
        {
            "transition": REFUSAL["transition"],
            "verdict": {
                "rejected": {
                    "kind": "gate",
                    "gate": "TradeTerms(...) and (effective_from after prior_from)",
                    "rule": "amendment_takes_effect_later",
                    "statement_kind": "require",
                    "directly_missing_claims": [],
                    "compared": {
                        "op": "after",
                        "left": {"type": "date", "value": "2026-06-08"},
                        "right": {"type": "date", "value": "2026-06-09"},
                    },
                }
            },
        }
    )
    assert why(late) == "amendment_takes_effect_later: 2026-06-08 after 2026-06-09 does not hold"
    duplicate = envelopes.Explanation.from_json(
        {
            "transition": REFUSAL["transition"],
            "verdict": {
                "rejected": {
                    "kind": "gate",
                    "gate": "not TradeCaptured(_, _, trade, _, _, _)",
                    "rule": "trade_is_new",
                    "statement_kind": "require",
                    "directly_missing_claims": [],
                }
            },
        }
    )
    assert why(duplicate) == "trade_is_new: not TradeCaptured(_, _, trade, _, _, _) refused"
    zero = envelopes.Explanation.from_json(
        {
            "transition": REFUSAL["transition"],
            "verdict": {
                "rejected": {
                    "kind": "invariant",
                    "name": "quantity_is_positive",
                    "rule": "...",
                    "compared": {
                        "op": ">",
                        "left": {"type": "quantity", "value": {"amount": "0", "unit": "MW"}},
                        "right": {"type": "quantity", "value": {"amount": "0", "unit": "MW"}},
                    },
                }
            },
        }
    )
    # From v0.0.16 the codec keeps a quantity's unit, and the line a desk
    # reads carries it: the rule compares megawatts, so the refusal says so.
    assert why(zero) == "would break invariant quantity_is_positive: 0 MW > 0 MW does not hold"
    # A per-hour limit's refusal: the ledger's witness carries every
    # binding in scope (the proposal's arguments, the cap, the whole hour
    # grid); the desk's line shows the quantifier's binder, the hour.
    over_limit = envelopes.Explanation.from_json(
        {
            "transition": REFUSAL["transition"],
            "verdict": {
                "rejected": {
                    "kind": "gate",
                    "gate": "(forall cap in PositionLimit(org, book, cap): forall h in hours: ...)",
                    "rule": "hour_within_limit",
                    "statement_kind": "require",
                    "directly_missing_claims": [],
                    "compared": {
                        "op": "<=",
                        "left": {"type": "quantity", "value": {"amount": "13", "unit": "MW"}},
                        "right": {"type": "quantity", "value": {"amount": "10", "unit": "MW"}},
                    },
                    "witness": [
                        {"var": "book", "value": {"type": "subject", "value": "spec-de"}},
                        {
                            "var": "cap",
                            "value": {"type": "quantity", "value": {"amount": "10", "unit": "MW"}},
                        },
                        {
                            "var": "h",
                            "value": {"type": "timestamp", "value": "2026-07-01T10:00:00Z"},
                        },
                        {
                            "var": "hours",
                            "value": {
                                "type": "collection",
                                "value": [
                                    {"type": "timestamp", "value": "2026-07-01T10:00:00Z"},
                                    {"type": "timestamp", "value": "2026-07-01T11:00:00Z"},
                                ],
                            },
                        },
                    ],
                }
            },
        }
    )
    assert why(over_limit) == (
        "hour_within_limit: 13 MW <= 10 MW does not hold (h = 2026-07-01T10:00:00+00:00)"
    )
    assert wire_text([dt.datetime(2026, 7, 1, 10, tzinfo=dt.UTC)]) == "[2026-07-01T10:00:00+00:00]"
