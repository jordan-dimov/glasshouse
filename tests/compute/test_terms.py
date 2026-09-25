"""The in-force selector in Python, held to a brute-force reading: it
must say exactly what the ledger's generated `trade_terms_in_force_on`
says, because the gate is the authority and this only predicts it."""

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from glasshouse.commit import models
from glasshouse.compute.terms import (
    TermsError,
    current_terms,
    next_version_id,
    terms_in_force_on,
    terms_version_id,
)

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
D0 = dt.date(2026, 6, 1)


def version(ordinal: int, day: int) -> models.TradeTermsClaim:
    return models.TradeTermsClaim(
        "acme",
        "T-1",
        terms_version_id("T-1", ordinal),
        Decimal(ordinal),
        Decimal("50"),
        T0,
        T0 + dt.timedelta(hours=1),
        D0 + dt.timedelta(days=day),
    )


def brute_force(
    versions: list[models.TradeTermsClaim], as_of: dt.date
) -> models.TradeTermsClaim | None:
    """The selector's body, read literally: a version effective on or
    before the date, with no later version also on or before it."""
    for candidate in versions:
        if candidate.effective_from > as_of:
            continue
        later = [
            other
            for other in versions
            if other.effective_from <= as_of and other.effective_from > candidate.effective_from
        ]
        if not later:
            return candidate
    return None


@given(
    st.lists(st.integers(min_value=0, max_value=90), max_size=8, unique=True),
    st.integers(min_value=-5, max_value=95),
)
def test_agrees_with_the_brute_force_reading(days: list[int], offset: int) -> None:
    versions = [version(i + 1, day) for i, day in enumerate(days)]
    as_of = D0 + dt.timedelta(days=offset)
    assert terms_in_force_on(versions, as_of) == brute_force(versions, as_of)


def test_none_is_in_force_before_the_first_version() -> None:
    assert terms_in_force_on([version(1, 10)], D0) is None
    assert terms_in_force_on([], D0) is None
    assert current_terms([]) is None


def test_current_is_the_latest_effective_date_whatever_the_order() -> None:
    versions = [version(2, 20), version(1, 5), version(3, 12)]
    assert current_terms(versions) == version(2, 20)
    assert current_terms(versions) == terms_in_force_on(versions, dt.date(2099, 1, 1))


def test_a_tie_is_refused_not_guessed() -> None:
    with pytest.raises(TermsError, match="effective from"):
        terms_in_force_on([version(1, 3), version(2, 3)], D0 + dt.timedelta(days=9))


def test_version_ids_are_a_readable_per_trade_sequence() -> None:
    assert terms_version_id("T-001", 1) == "T-001/v1"
    assert terms_version_id("T-001", 2) == "T-001/v2"


def test_the_next_version_id_skips_names_a_caller_already_used() -> None:
    # Two versions, one of them named outside the convention and one
    # named ahead of it: the count-plus-one id would collide.
    named_ahead = models.TradeTermsClaim(
        "acme", "T-1", "T-1/v3", Decimal(1), Decimal("50"), T0, T0, D0 + dt.timedelta(days=1)
    )
    custom = models.TradeTermsClaim(
        "acme", "T-1", "desk-rebook", Decimal(1), Decimal("50"), T0, T0, D0 + dt.timedelta(days=2)
    )
    assert next_version_id("T-1", [version(1, 0)]) == "T-1/v2"
    assert next_version_id("T-1", [version(1, 0), named_ahead, custom]) == "T-1/v4"
    assert next_version_id("T-1", []) == "T-1/v1"
