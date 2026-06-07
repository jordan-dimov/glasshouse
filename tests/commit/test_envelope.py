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
from glasshouse.commit.envelope import (
    NAMED_CLAIMS,
    OUTCOME,
    Explanation,
    Quantity,
    named_wire,
    untag,
)
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


def test_untag_quantity_keeps_the_unit_the_wire_spells_out() -> None:
    # Pinned from morpholog PR #127: only the self-describing tagged
    # codec carries the unit; named reads return the bare amount.
    value = untag({"type": "quantity", "value": {"amount": "10.5", "unit": "MW"}})
    assert value == Quantity(Decimal("10.5"), "MW")


def test_untag_refuses_untagged_input() -> None:
    with pytest.raises(ValueError, match="not a tagged morpholog value"):
        untag("bare string")


def test_named_wire_renders_each_kind() -> None:
    assert named_wire(
        {
            "trade": "t1",
            "quantity": Decimal("100.5"),
            "captured_on": dt.date(2026, 6, 7),
            "delivery_start": dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
            "firm": True,
        }
    ) == {
        "trade": "t1",
        "quantity": "100.5",
        "captured_on": "2026-06-07",
        "delivery_start": "2026-07-01T00:00:00+00:00",
        "firm": True,
    }


def test_named_wire_refuses_naive_timestamps() -> None:
    # Law 9: delivery periods are UTC instants; a naive datetime has no
    # instant to name.
    with pytest.raises(ValueError, match="timezone-aware"):
        named_wire({"delivery_start": dt.datetime(2026, 7, 1)})


def test_untag_timestamp_is_aware() -> None:
    value = untag({"type": "timestamp", "value": "2026-07-01T00:00:00Z"})
    assert value == dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
    assert isinstance(value, dt.datetime) and value.tzinfo is not None


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
