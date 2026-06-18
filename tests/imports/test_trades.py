"""Trades CSV parsing, quarantine, and the receipt-to-line mapping,
against golden CSV strings and a fake binary playing back canned batch
receipts."""

import csv
import io
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.commit import GlasshouseClient
from glasshouse.imports import ImportFormatError, import_trades, parse_trades
from glasshouse.imports.trades import COLUMNS
from tests.support import fake_binary

HEADER = "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end"
GOOD = "spec-de,T-{n},stadtwerk-x,de-power,buy,10,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z"

MIXED = "\n".join(
    [
        HEADER,
        GOOD.format(n=1),  # line 2: reaches the batch
        "spec-de,T-2,cp,de-power,buy,10,86.25,2026-07-01T00:00:00,2026-07-02T00:00:00Z",  # naive
        "spec-de,T-3,cp,de-power,buy,ten,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",  # qty
        "spec-de,T-4,cp,de-power,long,10,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",  # dir
        GOOD.format(n=5),  # line 6: reaches the batch
    ]
)

RECEIPTS = "\n".join(
    [
        json.dumps(
            {
                "status": "committed",
                "transition_id": "tr-1",
                "actor": {"type": "subject", "value": "alice"},
                "asserted_claims": [],
                "retracted_claims": [],
                "emitted_intents": [],
                "row": 1,
            }
        ),
        json.dumps({"status": "rejected", "reason": "trade already captured", "row": 2}),
    ]
)


def test_parse_quarantines_each_dishonest_row_with_its_reason() -> None:
    accepted, quarantined = parse_trades(MIXED, org="acme-energy")
    assert [line for line, _ in accepted] == [2, 6]
    reasons = {line: outcome.detail for line, outcome in quarantined}
    assert "offset" in reasons[3]  # the codec refuses the naive instant
    assert "exact decimals" in reasons[4]
    assert "buy or sell" in reasons[5]


def test_header_mismatch_refuses_the_whole_file() -> None:
    with pytest.raises(ImportFormatError, match="missing: price"):
        parse_trades(MIXED.replace(",price,", ",cost,"), org="acme-energy")


def test_ragged_rows_quarantine() -> None:
    text = "\n".join(
        [HEADER, GOOD.format(n=1) + ",extra", GOOD.format(n=2)[: -len(",2026-07-02T00:00:00Z")]]
    )
    accepted, quarantined = parse_trades(text, org="acme-energy")
    assert not accepted
    assert ["more fields" in o.detail or "fewer fields" in o.detail for _, o in quarantined] == [
        True,
        True,
    ]


def test_import_maps_batch_receipts_back_to_csv_lines(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, RECEIPTS)
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    report = import_trades(client, MIXED, org="acme-energy", actor="alice")

    assert (report.committed, report.rejected, report.quarantined) == (1, 1, 3)
    by_ref = {o.ref: o for o in report.outcomes}
    assert by_ref["line 2"].status == "committed"
    assert by_ref["line 2"].detail == "tr-1"
    assert by_ref["line 6"].status == "rejected"
    # File order is preserved in the report.
    assert [o.ref for o in report.outcomes] == [f"line {n}" for n in (2, 3, 4, 5, 6)]
    # The batch carried only the two honest rows, with per-row args_named.
    sent = [json.loads(line) for line in (tmp_path / "stdin.txt").read_text().splitlines()]
    assert [row["args_named"]["trade"] for row in sent] == ["T-1", "T-5"]
    assert all(row["actor"] == "alice" for row in sent)


ERROR_RECEIPTS = "\n".join(
    [
        json.dumps(
            {
                "status": "committed",
                "transition_id": "tr-1",
                "actor": {"type": "subject", "value": "alice"},
                "asserted_claims": [],
                "retracted_claims": [],
                "emitted_intents": [],
                "row": 1,
            }
        ),
        json.dumps({"row": 2, "status": "error", "error": "could not serialize access"}),
    ]
)


def test_an_error_receipt_is_reported_per_row_not_raised(tmp_path: Path) -> None:
    text = "\n".join([HEADER, GOOD.format(n=1), GOOD.format(n=2)])
    binary = fake_binary(tmp_path, ERROR_RECEIPTS)
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    report = import_trades(client, text, org="acme-energy", actor="alice")
    assert (report.committed, report.errored) == (1, 1)
    assert "could not serialize" in report.outcomes[1].detail


def test_an_all_quarantined_file_never_reaches_the_binary(tmp_path: Path) -> None:
    text = "\n".join(
        [
            HEADER,
            "spec-de,T-1,cp,de-power,long,10,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",
            "spec-de,T-2,cp,de-power,buy,ten,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",
        ]
    )
    binary = fake_binary(tmp_path, "")
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    report = import_trades(client, text, org="acme-energy", actor="alice")
    assert report.quarantined == 2
    assert len(report.outcomes) == 2
    assert not (tmp_path / "argv.txt").exists()  # no batch was run


# Printable single-line cells: csv quotes commas and quotes faithfully,
# and with no embedded newline the reader's line number stays one per row,
# so the conservation law can be stated over physical lines.
cells = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=10)
trade_rows = st.lists(st.fixed_dictionaries(dict.fromkeys(COLUMNS, cells)), max_size=12)


@given(trade_rows)
def test_every_input_row_is_accounted_for_exactly_once(rows: list[dict[str, str]]) -> None:
    # The import law: nothing is silently dropped. Whatever the cells, a
    # well-headed file's every data row lands in exactly one of accepted
    # or quarantined, and the line numbers are the contiguous run 2..n+1.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=sorted(COLUMNS))
    writer.writeheader()
    writer.writerows(rows)

    accepted, quarantined = parse_trades(buffer.getvalue(), org="acme")
    assert len(accepted) + len(quarantined) == len(rows)
    lines = sorted([line for line, _ in accepted] + [line for line, _ in quarantined])
    assert lines == list(range(2, 2 + len(rows)))


def test_an_unparseable_instant_quarantines_with_the_format_named() -> None:
    text = "\n".join(
        [HEADER, "spec-de,T-1,cp,de-power,buy,10,86.25,yesterday,2026-07-02T00:00:00Z"]
    )
    accepted, quarantined = parse_trades(text, org="acme-energy")
    assert not accepted
    ((_, outcome),) = quarantined
    assert "RFC 3339" in outcome.detail
    assert "'yesterday'" in outcome.detail
