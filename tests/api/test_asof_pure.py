"""The as-of read rules with fabricated rows, no ledger: the filters and
orders the live queries apply, the newest mark per trade, the instant
parsed as UTC, and the future refused before anything is replayed.
"""

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from glasshouse.api import asof
from glasshouse.commit import GlasshouseClient, MorphologError
from tests.support import fake_binary

T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def _mark(trade: str, curve: str, minutes: int, tid: str, org: str = "acme") -> dict[str, Any]:
    return {
        "org": org,
        "trade": trade,
        "curve_version": curve,
        "book": "spec" if trade != "T-3" else "hedge",
        "mtm": Decimal("1.5"),
        "valued_at": T0 + dt.timedelta(minutes=minutes),
        "transition_id": tid,
        "actor": "risk",
        "terms_version": f"{trade}/v1",
    }


PAST = asof.Snapshot(
    at=T0,
    transitions=9,
    trades=[
        {"org": "acme", "trade": "T-2", "book": "spec", "market": "de"},
        {"org": "acme", "trade": "T-1", "book": "spec", "market": "de"},
        {"org": "acme", "trade": "T-3", "book": "hedge", "market": "fr"},
        {"org": "other", "trade": "X-1", "book": "spec", "market": "de"},
    ],
    positions=[
        {"org": "acme", "book": "spec", "market": "de", "period_start": T0 + dt.timedelta(hours=h)}
        for h in (2, 0, 1)
    ],
    valuations=[
        _mark("T-1", "crv-1", 1, "t-a"),
        _mark("T-1", "crv-2", 5, "t-c"),
        _mark("T-2", "crv-1", 2, "t-b"),
        _mark("T-3", "crv-1", 3, "t-d"),
        _mark("X-1", "crv-1", 9, "t-z", org="other"),
    ],
)


def test_trades_are_scoped_filtered_ordered_and_paged() -> None:
    def ids(**narrowing: Any) -> list[str]:
        defaults: dict[str, Any] = {"book": None, "market": None, "limit": None, "offset": None}
        return [row["trade"] for row in asof.trades(PAST, org="acme", **(defaults | narrowing))]

    assert ids() == ["T-1", "T-2", "T-3"]  # the other organisation never appears
    assert ids(book="hedge") == ["T-3"]
    assert ids(market="de") == ["T-1", "T-2"]
    assert ids(limit=1, offset=1) == ["T-2"]


def test_positions_keep_the_half_open_window_and_the_table_order() -> None:
    rows = asof.positions(
        PAST, org="acme", book=None, market=None, start=T0, end=T0 + dt.timedelta(hours=2)
    )
    assert [row["period_start"].hour for row in rows] == [0, 1]
    assert asof.positions(PAST, org="acme", book="hedge", market=None, start=None, end=None) == []


def test_the_latest_mark_is_one_per_trade_and_market_goes_through_the_trades() -> None:
    def marks(**narrowing: Any) -> list[tuple[str, str]]:
        defaults: dict[str, Any] = {"trade": None, "book": None, "market": None, "latest": False}
        rows = asof.valuations(PAST, org="acme", **(defaults | narrowing))
        return [(row["trade"], row["curve_version"]) for row in rows]

    assert marks() == [("T-1", "crv-1"), ("T-1", "crv-2"), ("T-2", "crv-1"), ("T-3", "crv-1")]
    assert marks(latest=True) == [("T-1", "crv-2"), ("T-2", "crv-1"), ("T-3", "crv-1")]
    assert marks(latest=True, market="fr") == [("T-3", "crv-1")]
    assert marks(book="spec", trade="T-2") == [("T-2", "crv-1")]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("2026-07-01T10:00", dt.datetime(2026, 7, 1, 10, tzinfo=dt.UTC)),  # no offset: UTC
        ("2026-07-01T12:00:00+02:00", dt.datetime(2026, 7, 1, 10, tzinfo=dt.UTC)),
    ],
)
def test_an_instant_is_read_as_utc(raw: str | None, expected: dt.datetime | None) -> None:
    assert asof.parse_instant(raw) == expected


def test_what_is_not_an_instant_is_refused() -> None:
    with pytest.raises(ValueError, match="Invalid isoformat"):
        asof.parse_instant("last Tuesday")


def test_the_future_is_refused_before_anything_is_replayed() -> None:
    # The client points nowhere: reaching it would raise something else.
    client = GlasshouseClient("model.morph", "postgres:///x", binary="/nonexistent/morpholog")
    with pytest.raises(asof.NotYetHistoryError, match="in the future"):
        asof.snapshot(client, T0 + dt.timedelta(seconds=1), now=T0)


def test_a_window_bound_with_no_offset_is_utc_and_an_offset_is_converted() -> None:
    # The live path compares in the database; this one in Python, where a
    # naive bound against a stored instant is a TypeError. Both now take
    # the bound through `as_utc` first.
    naive = dt.datetime(2026, 7, 1, 1)  # noqa: DTZ001  the case under test
    offset = dt.datetime(2026, 7, 1, 4, tzinfo=dt.timezone(dt.timedelta(hours=2)))  # 02:00 UTC
    rows = asof.positions(
        PAST,
        org="acme",
        book=None,
        market=None,
        start=asof.as_utc(naive),
        end=asof.as_utc(offset),
    )
    assert [row["period_start"].hour for row in rows] == [1]
    assert asof.as_utc(None) is None


def test_an_instant_has_one_spelling() -> None:
    at = asof.parse_instant("2026-07-01T12:00:00.25+02:00")
    assert at is not None
    assert asof.canonical(at) == "2026-07-01T10:00:00.250000"
    assert asof.parse_instant(asof.canonical(at)) == at


NOT_SETTLED = (
    "Error: resolving --as-of timestamp\n\nCaused by:\n    the state as of "
    "2026-07-01T00:00:00Z is not settled: a transaction that may be ordered at or "
    "before it is still open, or the instant has not passed; retry, or give a transition id"
)


def test_an_unsettled_instant_is_refused_and_nothing_is_replayed(tmp_path: Path) -> None:
    # The binary's own refusal (captured from v0.0.18 with an audit order
    # held open) becomes ours; the last call it saw is the as-of read by
    # timestamp, so the tail was never fetched.
    binary = fake_binary(tmp_path, "", stderr=NOT_SETTLED, exit_code=1)
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    with pytest.raises(asof.NotSettledError, match="not final yet"):
        asof.snapshot(client, T0, now=T0 + dt.timedelta(hours=1))
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["inspect", "claims"]
    assert argv[argv.index("--as-of") + 1] == "2026-07-01T00:00:00+00:00"


def test_any_other_failure_of_the_settlement_read_stays_operational(tmp_path: Path) -> None:
    # A dead database must never read as "not settled, try again".
    binary = fake_binary(tmp_path, "", stderr="connection refused", exit_code=1)
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    with pytest.raises(MorphologError, match="connection refused"):
        asof.settle(client, T0)
