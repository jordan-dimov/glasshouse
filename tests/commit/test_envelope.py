"""The wire contract: golden envelopes parse, the codec is exact both
ways, and Decimal never takes a float detour."""

import datetime as dt
import json
import re
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.commit import (
    Committed,
    GateRejection,
    InvariantRejection,
    Rejected,
    RejectedVerdict,
)
from glasshouse.commit.envelope import OUTCOME, Explanation, named_wire, untag
from tests.commit import envelopes

# The exact Decimal pattern `morpholog schema` emits; `named_wire` must
# stay inside it.
WIRE_DECIMAL = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")


def test_committed_envelope_parses_exactly() -> None:
    outcome = OUTCOME.validate_python(json.loads(envelopes.COMMITTED_CAPTURE))
    assert isinstance(outcome, Committed)
    assert outcome.transition_id == UUID("019e9f47-68f7-7d53-97fa-83fdd3dd6df6")
    assert outcome.actor == "trader"
    terms = next(c for c in outcome.asserted_claims if c.predicate == "TradeTerms")
    assert terms.args == ("t2", "t2v1", Decimal("50"), "2026Q4", dt.date(2026, 6, 7))
    price = next(c for c in outcome.asserted_claims if c.predicate == "CapturedPrice")
    assert price.args[1] == Decimal("82.50")
    assert str(price.args[1]) == "82.50"  # scale preserved: no float detour
    assert outcome.retracted_claims == ()
    assert [i.name for i in outcome.emitted_intents] == ["TradeCapturedAdmitted"]


def test_rejected_envelope_parses() -> None:
    outcome = OUTCOME.validate_python(json.loads(envelopes.REJECTED_DUPLICATE))
    assert isinstance(outcome, Rejected)
    assert "require failed" in outcome.reason
    assert outcome.explanation is None


def test_rejected_with_explanation_carries_the_same_snapshot_verdict() -> None:
    outcome = OUTCOME.validate_python(json.loads(envelopes.REJECTED_WITH_EXPLANATION))
    assert isinstance(outcome, Rejected)
    assert outcome.explanation is not None
    assert isinstance(outcome.explanation.verdict, RejectedVerdict)
    detail = outcome.explanation.verdict.rejected
    assert isinstance(detail, GateRejection)
    assert detail.gate == "not TradeCaptured(trade, _, _)"


def test_named_claims_are_bare_and_wire_true() -> None:
    from glasshouse.commit.envelope import NAMED_CLAIMS

    claims = {
        c.predicate: c.args
        for c in NAMED_CLAIMS.validate_python(json.loads(envelopes.NAMED_CLAIMS))
    }
    # Wire-true: decimals and dates stay strings on the named read; the
    # generated per-predicate models own the typing.
    assert claims["CapturedPrice"] == {"trade": "t1", "price": "45.20"}
    assert claims["TradeTerms"]["effective_from"] == "2026-06-01"


def test_explain_admissible() -> None:
    explanation = Explanation.model_validate(json.loads(envelopes.EXPLAIN_ADMISSIBLE))
    assert explanation.is_admissible


def test_explain_gate_rejection_names_the_suppliers() -> None:
    explanation = Explanation.model_validate(json.loads(envelopes.EXPLAIN_GATE))
    assert not explanation.is_admissible
    assert isinstance(explanation.verdict, RejectedVerdict)
    detail = explanation.verdict.rejected
    assert isinstance(detail, GateRejection)
    (missing,) = detail.directly_missing_claims
    assert missing.rendered == "CurrentOfficialPrice(t2, opx)"
    assert "confirm_trade" in missing.candidate_supplier_transformations


def test_explain_invariant_rejection() -> None:
    explanation = Explanation.model_validate(json.loads(envelopes.EXPLAIN_INVARIANT))
    assert isinstance(explanation.verdict, RejectedVerdict)
    detail = explanation.verdict.rejected
    assert isinstance(detail, InvariantRejection)
    assert detail.name == "settled_within_effective_terms"


def test_untag_collection_nests() -> None:
    wire = {
        "type": "collection",
        "value": [
            {"type": "subject", "value": "t1"},
            {"type": "decimal", "value": "1.5"},
            {"type": "collection", "value": [{"type": "bool", "value": True}]},
        ],
    }
    assert untag(wire) == ["t1", Decimal("1.5"), [True]]


def test_untag_refuses_untagged_input() -> None:
    with pytest.raises(ValueError, match="not a tagged morpholog value"):
        untag("bare string")


def test_named_wire_renders_each_kind() -> None:
    assert named_wire(
        {
            "trade": "t1",
            "quantity": Decimal("100.5"),
            "captured_on": dt.date(2026, 6, 7),
            "firm": True,
        }
    ) == {"trade": "t1", "quantity": "100.5", "captured_on": "2026-06-07", "firm": True}


@given(
    st.decimals(
        min_value=Decimal("-1e12"),
        max_value=Decimal("1e12"),
        allow_nan=False,
        allow_infinity=False,
        places=9,
    )
)
def test_decimal_wire_round_trip(value: Decimal) -> None:
    wire = named_wire({"x": value})["x"]
    assert isinstance(wire, str)
    assert WIRE_DECIMAL.fullmatch(wire), wire
    assert untag({"type": "decimal", "value": wire}) == value
