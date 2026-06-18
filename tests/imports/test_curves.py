"""Curve grouping and whole-curve quarantine, against golden CSV
strings. The register path itself is exercised in the integration leg;
here the contract is that a curve is atomic and `HourlyCurve` is the
validator."""

import csv
import datetime as dt
import io
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.imports import ImportFormatError, parse_curves
from glasshouse.imports.curves import COLUMNS

HEADER = "market,as_of,version,period_start,price"


def test_rows_group_into_whole_curves_in_first_appearance_order() -> None:
    text = "\n".join(
        [
            HEADER,
            "de-power,2026-06-08,crv-a,2026-07-01T00:00:00Z,90",
            "de-power,2026-06-08,crv-b,2026-07-01T00:00:00Z,50",
            "de-power,2026-06-08,crv-a,2026-07-01T01:00:00Z,88",
            "de-power,2026-06-08,crv-b,2026-07-01T01:00:00Z,51",
        ]
    )
    curves, quarantined = parse_curves(text)
    assert not quarantined
    assert [(ref, version) for ref, _, _, version, _ in curves] == [
        ("de-power/2026-06-08/crv-a", "crv-a"),
        ("de-power/2026-06-08/crv-b", "crv-b"),
    ]
    _, _, as_of, _, curve = curves[0]
    assert as_of == dt.date(2026, 6, 8)
    assert curve.periods[1] == (dt.datetime(2026, 7, 1, 1, tzinfo=dt.UTC), Decimal("88"))


def test_a_gappy_curve_quarantines_whole_and_the_others_survive() -> None:
    text = "\n".join(
        [
            HEADER,
            "de-power,2026-06-08,crv-good,2026-07-01T00:00:00Z,90",
            "de-power,2026-06-08,crv-good,2026-07-01T01:00:00Z,88",
            "de-power,2026-06-08,crv-gap,2026-07-01T00:00:00Z,90",
            "de-power,2026-06-08,crv-gap,2026-07-01T02:00:00Z,88",  # hour 1 missing
        ]
    )
    curves, quarantined = parse_curves(text)
    assert [version for _, _, _, version, _ in curves] == ["crv-good"]
    (outcome,) = quarantined
    assert outcome.ref == "de-power/2026-06-08/crv-gap"
    assert "contiguous" in outcome.detail


def test_naive_and_mixed_instants_quarantine_with_a_reason() -> None:
    text = "\n".join(
        [
            HEADER,
            "de-power,2026-06-08,crv-naive,2026-07-01T00:00:00,90",
            "de-power,2026-06-08,crv-mixed,2026-07-01T00:00:00,90",
            "de-power,2026-06-08,crv-mixed,2026-07-01T01:00:00Z,88",
            "de-power,bad-date,crv-date,2026-07-01T00:00:00Z,90",
        ]
    )
    curves, quarantined = parse_curves(text)
    assert not curves
    assert {o.ref.rsplit("/", 1)[-1] for o in quarantined} == {"crv-naive", "crv-mixed", "crv-date"}
    assert all(o.detail for o in quarantined)


def test_header_mismatch_refuses_the_whole_file() -> None:
    with pytest.raises(ImportFormatError, match="unknown: when"):
        parse_curves(HEADER.replace("as_of", "when") + "\n")


# Slash-free cells so the "market/as_of/version" ref splits back into the
# group identity unambiguously.
cells = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126, exclude_characters="/"),
    max_size=8,
)
curve_rows = st.lists(st.fixed_dictionaries(dict.fromkeys(COLUMNS, cells)), max_size=12)


@given(curve_rows)
def test_every_curve_group_is_accounted_for_exactly_once(rows: list[dict[str, str]]) -> None:
    # A curve is the unit, not a row: every distinct (market, as_of,
    # version) group becomes exactly one outcome (a built curve or a
    # quarantine), by identity - nothing dropped, nothing duplicated, not
    # merely the right count.
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=sorted(COLUMNS))
    writer.writeheader()
    writer.writerows(rows)

    curves, quarantined = parse_curves(buffer.getvalue())
    expected = {(row["market"], row["as_of"], row["version"]) for row in rows}
    built = {ref for ref, *_ in curves}
    quarantined_refs = {outcome.ref for outcome in quarantined}
    actual = {tuple(ref.split("/", 2)) for ref in built | quarantined_refs}
    assert actual == expected
    # Disjoint by construction (one outcome per group); the count proves
    # no group landed in both.
    assert len(built) + len(quarantined_refs) == len(expected)
