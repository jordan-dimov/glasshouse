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


def standing(
    name: str,
    valid_until: str,
    *,
    book: str = "spec-de",
    holding: str = "4",
    outcome: str = "open",
    approval: str = "awaiting",
    approved_by: str = "nobody",
) -> dict[str, object]:
    """One row of the ledger's derived `InstructionStanding`, as the
    binary spells it."""
    return {
        "predicate": "InstructionStanding",
        "args": {
            "org": "acme-energy",
            "instruction": name,
            "book": book,
            "market": "de-power",
            "direction": "buy",
            "quantity": "4",
            "limit_price": "80",
            "currency": "EUR",
            "delivery_start": "2026-07-02T00:00:00Z",
            "delivery_end": "2026-07-02T02:00:00Z",
            "placed_on": "2026-07-01",
            "valid_until": valid_until,
            "proposer": "agent",
            "holding": holding,
            "outcome": outcome,
            "approval": approval,
            "approved_by": approved_by,
        },
    }


LATER, EARLIER = "2026-07-01T13:00:00Z", "2026-07-01T11:00:00Z"
ROWS = [
    standing("I-open", LATER, approval="approved", approved_by="risk-manager"),
    standing("I-overdue", EARLIER),
    standing(
        "I-gone", LATER, book="hedge-de", holding="0", outcome="cancelled", approval="not_required"
    ),
]


def client(tmp_path: Path) -> GlasshouseClient:
    binary = fake_binary(tmp_path, json.dumps(ROWS))
    return GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))


def test_status_and_what_is_held_come_from_the_ledgers_one_read(tmp_path: Path) -> None:
    rows = instructions.list_instructions(client(tmp_path), org="acme-energy", now=NOW)
    seen = {row.instruction: (row.status, row.holding_mw) for row in rows}
    assert seen == {
        "I-open": ("open", Decimal("4")),
        # Past its time, lapse not recorded: the ledger's room rule no
        # longer counts it, so neither does the list. The clock is the one
        # thing worked out here.
        "I-overdue": ("overdue", Decimal("0")),
        "I-gone": ("cancelled", Decimal("0")),
    }
    assert {row.currency for row in rows} == {"EUR"}
    approval = {row.instruction: (row.approval, row.approved_by) for row in rows}
    assert approval == {
        "I-open": ("approved", "risk-manager"),
        "I-overdue": ("awaiting", None),
        "I-gone": ("not required", None),
    }
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["inspect", "derived"]
    assert "InstructionStanding" in argv
    # The organisation is the binary's to narrow by, not ours.
    assert argv[argv.index("--where") + 1] == "org=acme-energy"


def test_open_only_keeps_what_still_holds_room(tmp_path: Path) -> None:
    holding = instructions.list_instructions(
        client(tmp_path), org="acme-energy", open_only=True, now=NOW
    )
    assert [row.instruction for row in holding] == ["I-open"]


def test_a_book_is_narrowed_by_the_binary_too(tmp_path: Path) -> None:
    instructions.list_instructions(client(tmp_path), org="acme-energy", book="hedge-de", now=NOW)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    wheres = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--where"]
    assert sorted(wheres) == ["book=hedge-de", "org=acme-energy"]


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
        ("quantity", "NaN", "exact decimals"),
        ("limit_price", "86." + "1" * 29, "28 decimal places"),  # held as written, or refused
        ("valid_until", "tomorrow", "RFC 3339"),
        ("delivery_start", "2026-07-02T00:00:00", "offset from UTC"),  # naive: never guessed
        ("valid_until", "2026-07-01T18:00:00", "offset from UTC"),
    ],
)
def test_a_mistake_is_refused_in_words(field: str, bad: str, says: str) -> None:
    with pytest.raises(ValueError, match=says):
        instructions.place_request(FIELDS | {field: bad}, org="acme-energy", placed_on=NOW.date())
