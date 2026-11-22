"""Where a mandate stands, as the read says it: suspension outranks
expiry, and expiry outranks a missing approval, because each is the
reason a trade would be refused first that a person can act on."""

import datetime as dt

from glasshouse.api.mandates import standing_of

NOW = dt.datetime(2026, 11, 22, 12, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(days=1)
EARLIER = NOW - dt.timedelta(days=1)


def test_an_approved_mandate_in_time_is_open() -> None:
    assert standing_of(approved=True, suspended=False, valid_until=LATER, now=NOW) == "open"


def test_one_not_yet_approved_awaits_approval() -> None:
    assert (
        standing_of(approved=False, suspended=False, valid_until=LATER, now=NOW)
        == "awaiting approval"
    )


def test_a_mandate_past_its_end_is_expired_approved_or_not() -> None:
    for approved in (True, False):
        assert (
            standing_of(approved=approved, suspended=False, valid_until=EARLIER, now=NOW)
            == "expired"
        )


def test_a_suspended_mandate_says_so_even_once_expired() -> None:
    assert standing_of(approved=True, suspended=True, valid_until=EARLIER, now=NOW) == "suspended"


def test_its_last_instant_is_still_open() -> None:
    assert standing_of(approved=True, suspended=False, valid_until=NOW, now=NOW) == "open"
