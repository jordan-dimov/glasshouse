"""Golden envelopes captured verbatim from morpholog-cli 0.0.1 on
07/06/2026 (post PR #125), driving `trade_lifecycle.morph`. These pin the
wire shapes the adapter is written against; if upstream changes an
envelope, these fail before any integration test does."""
# ruff: noqa: E501 (verbatim wire captures are never wrapped)

COMMITTED_CAPTURE = """\
{
  "status": "committed",
  "transition_id": "019e9f47-68f7-7d53-97fa-83fdd3dd6df6",
  "actor": {"type": "subject", "value": "trader"},
  "asserted_claims": [
    {
      "predicate": "TradeCaptured",
      "args": [
        {"type": "subject", "value": "t2"},
        {"type": "subject", "value": "power"},
        {"type": "subject", "value": "sell"}
      ]
    },
    {
      "predicate": "TradeTerms",
      "args": [
        {"type": "subject", "value": "t2"},
        {"type": "subject", "value": "t2v1"},
        {"type": "decimal", "value": "50"},
        {"type": "subject", "value": "2026Q4"},
        {"type": "date", "value": "2026-06-07"}
      ]
    },
    {
      "predicate": "CapturedPrice",
      "args": [
        {"type": "subject", "value": "t2"},
        {"type": "decimal", "value": "82.50"}
      ]
    }
  ],
  "retracted_claims": [],
  "emitted_intents": [
    {
      "name": "TradeCapturedAdmitted",
      "args": [{"type": "subject", "value": "t2"}]
    }
  ]
}
"""

REJECTED_DUPLICATE = """\
{
  "status": "rejected",
  "reason": "require failed: not TradeCaptured(trade, _, _) did not hold over pre-state"
}
"""

REJECTED_WITH_EXPLANATION = """\
{
  "explanation": {
    "transition": {
      "actor": "trader",
      "args": ["t1", "power", "buy", "v2", "100", "2026Q4", "2026-06-01", "45.20"],
      "transformation": "capture_trade"
    },
    "verdict": {
      "rejected": {
        "directly_missing_claims": [],
        "gate": "not TradeCaptured(trade, _, _)",
        "kind": "gate",
        "statement_kind": "require"
      }
    }
  },
  "reason": "require failed: not TradeCaptured(trade, _, _) did not hold over pre-state",
  "status": "rejected"
}
"""

NAMED_CLAIMS = """\
[
  {
    "args": {
      "price": "45.20",
      "trade": "t1"
    },
    "predicate": "CapturedPrice"
  },
  {
    "args": {
      "delivery_period": "2026Q4",
      "effective_from": "2026-06-01",
      "quantity": "100",
      "trade": "t1",
      "version_id": "v1"
    },
    "predicate": "TradeTerms"
  }
]
"""

EXPLAIN_ADMISSIBLE = """\
{
  "transition": {
    "transformation": "capture_trade",
    "args": ["t9", "power", "buy", "t9v1", "10", "2026Q4", "2026-06-07", "70"],
    "actor": "trader"
  },
  "verdict": "admissible"
}
"""

EXPLAIN_GATE = """\
{
  "transition": {
    "transformation": "settle_trade",
    "args": ["t2", "10", "sx", "opx", "2026-12-31"],
    "actor": "middle_office"
  },
  "verdict": {
    "rejected": {
      "kind": "gate",
      "gate": "CurrentOfficialPrice(trade, official_price_id)",
      "statement_kind": "require",
      "directly_missing_claims": [
        {
          "predicate": "CurrentOfficialPrice",
          "rendered": "CurrentOfficialPrice(t2, opx)",
          "candidate_supplier_transformations": ["confirm_trade", "correct_official_price"]
        }
      ]
    }
  }
}
"""

EXPLAIN_INVARIANT = """\
{
  "transition": {
    "transformation": "settle_trade",
    "args": ["t1", "60", "s2", "op2", "2026-06-30"],
    "actor": "middle_office"
  },
  "verdict": {
    "rejected": {
      "kind": "invariant",
      "name": "settled_within_effective_terms",
      "rule": "(TradeSettled(trade, _, _, _, d) and TradeTerms(trade, _, qty, _, ef) and (ef on_or_before d) and (not (exists later_ef: TradeTerms(trade, _, _, _, later_ef) and (later_ef on_or_before d) and (later_ef after ef)))) implies (sum(s | TradeSettled(trade, s, _, _, sd) and (sd on_or_before d)) <= qty)"
    }
  }
}
"""
