"""The instruction read and request builder without a ledger: status and
what is held from one fake read, and the mistakes a caller can make
refused in words before anything is proposed.
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from glasshouse.api import instructions
from glasshouse.commit import GlasshouseClient
from tests.support import fake_binary

NOW = dt.datetime(2026, 7, 1, 12, tzinfo=dt.UTC)


def placed(name: str, valid_until: str, book: str = "spec-de") -> dict[str, object]:
    return {
        "predicate": "Instruction",
        "asserted_in": "t-1",
        "args": {
            "org": "acme-energy",
            "book": book,
            "instruction": name,
            "proposer": "agent",
            "market": "de-power",
            "direction": "buy",
            "quantity": "4",
            "limit_price": "80",
            "delivery_start": "2026-07-02T00:00:00Z",
            "delivery_end": "2026-07-02T02:00:00Z",
            "placed_on": "2026-07-01",
            "valid_until": valid_until,
        },
    }


def still_open(name: str, remaining: str, valid_until: str) -> dict[str, object]:
    return {
        "predicate": "InstructionOpen",
        "asserted_in": "t-1",
        "args": {
            "org": "acme-energy",
            "book": "spec-de",
            "instruction": name,
            "direction": "buy",
            "remaining": remaining,
            "delivery_start": "2026-07-02T00:00:00Z",
            "delivery_end": "2026-07-02T02:00:00Z",
            "valid_until": valid_until,
        },
    }


LATER, EARLIER = "2026-07-01T13:00:00Z", "2026-07-01T11:00:00Z"
CLAIMS = [
    placed("I-open", LATER),
    still_open("I-open", "4", LATER),
    placed("I-overdue", EARLIER),
    still_open("I-overdue", "4", EARLIER),
    placed("I-gone", LATER, book="hedge-de"),
    {
        "predicate": "InstructionEnded",
        "asserted_in": "t-2",
        "args": {"org": "acme-energy", "instruction": "I-gone", "outcome": "cancelled"},
    },
    {
        "predicate": "ApprovalRequired",
        "asserted_in": "t-0",
        "args": {"org": "acme-energy", "book": "spec-de"},
    },
    {
        "predicate": "InstructionApproved",
        "asserted_in": "t-3",
        "args": {"org": "acme-energy", "instruction": "I-open", "approver": "risk-manager"},
    },
    {
        "predicate": "MarketCurrency",
        "asserted_in": "t-0",
        "args": {"org": "acme-energy", "market": "de-power", "currency": "EUR"},
    },
]


def client(tmp_path: Path) -> GlasshouseClient:
    binary = fake_binary(tmp_path, json.dumps(CLAIMS))
    return GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))


def test_status_and_what_is_held_come_from_one_read(tmp_path: Path) -> None:
    rows = instructions.list_instructions(client(tmp_path), org="acme-energy", now=NOW)
    seen = {row.instruction: (row.status, row.holding_mw) for row in rows}
    assert seen == {
        "I-open": ("open", Decimal("4")),
        # Past its time, lapse not recorded: it still holds its room.
        "I-overdue": ("overdue", Decimal("4")),
        "I-gone": ("cancelled", Decimal("0")),
    }
    assert {row.currency for row in rows} == {"EUR"}
    # spec-de requires approval: one has it, one waits. hedge-de does not.
    approval = {row.instruction: (row.approval, row.approved_by) for row in rows}
    assert approval == {
        "I-open": ("approved", "risk-manager"),
        "I-overdue": ("awaiting", None),
        "I-gone": ("not required", None),
    }
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv.count("--predicate") == 6  # one read of the ledger, six facts


def test_open_only_keeps_everything_still_holding_room(tmp_path: Path) -> None:
    holding = instructions.list_instructions(
        client(tmp_path), org="acme-energy", open_only=True, now=NOW
    )
    assert [row.instruction for row in holding] == ["I-open", "I-overdue"]
    by_book = instructions.list_instructions(
        client(tmp_path), org="acme-energy", book="hedge-de", now=NOW
    )
    assert [row.instruction for row in by_book] == ["I-gone"]
    assert instructions.list_instructions(client(tmp_path), org="someone-else", now=NOW) == []


FIELDS = {
    "book": "spec-de",
    "instruction": "I-1",
    "market": "de-power",
    "direction": "buy",
    "quantity": "4",
    "limit_price": "80",
    "delivery_start": "2026-07-02T00:00:00+00:00",
    "delivery_end": "2026-07-02T02:00:00+00:00",
    "valid_until": "2026-07-01T18:00:00+00:00",
}


def test_a_well_formed_instruction_becomes_one_typed_request() -> None:
    request = instructions.place_request(FIELDS, org="acme-energy", placed_on=NOW.date())
    assert (request.org, request.quantity, request.placed_on) == (
        "acme-energy",
        Decimal("4"),
        dt.date(2026, 7, 1),
    )
    assert request.valid_until == dt.datetime(2026, 7, 1, 18, tzinfo=dt.UTC)
    assert request.TRANSFORMATION == "place_instruction"


@pytest.mark.parametrize(
    ("field", "bad", "says"),
    [
        ("quantity", "lots", "exact decimals"),
        ("limit_price", "cheap", "exact decimals"),
        ("valid_until", "tomorrow", "RFC 3339"),
        ("delivery_start", "2026-07-02T00:00:00", "offset from UTC"),  # naive: never guessed
        ("valid_until", "2026-07-01T18:00:00", "offset from UTC"),
    ],
)
def test_a_mistake_is_refused_in_words(field: str, bad: str, says: str) -> None:
    with pytest.raises(ValueError, match=says):
        instructions.place_request(FIELDS | {field: bad}, org="acme-energy", placed_on=NOW.date())
