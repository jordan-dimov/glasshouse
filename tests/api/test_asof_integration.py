"""As-of viewing against the real stack (glasshouse#41): the seeded book
read at past instants taken from its own audit tail. The curve correction
and the amendment are the two moments the numbers must differ across, and
the present read as-of must equal the present read from the tables,
which is the replay and the projector agreeing row for row.
"""

import datetime as dt
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.compute.store import engine_url
from tests.support import BINARY, DB, needs_live_stack, seeded_ledger

pytestmark = needs_live_stack

ORG = "acme-energy"


@pytest.fixture(scope="module")
def moments() -> dict[str, dt.datetime]:
    """The instants this module reads at, each the placing of the last
    transition before an event: `before_capture`, `before_correction`,
    `before_amendment`, and `end`, the placing of the last row."""
    client, engine = seeded_ledger()
    engine.dispose()
    tail = client.audit_named()
    placed = [row.ordering.ordered_at for row in tail if row.ordering is not None]
    assert len(placed) == len(tail)  # a fresh ledger: every row is in the order
    names = [row.transformation_name for row in tail]

    def before(name: str) -> dt.datetime:
        first = names.index(name)
        assert placed[first - 1] < placed[first]
        return placed[first - 1]

    return {
        "before_capture": before("capture_trade"),
        "before_correction": before("correct_curve"),
        "before_amendment": before("amend_trade"),
        "end": placed[-1],
    }


@pytest.fixture
def app(moments: dict[str, dt.datetime], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    return TestClient(create_app())


def _get(
    client: TestClient, path: str, at: dt.datetime | None = None, /, **params: str
) -> list[dict[str, Any]]:
    query = {"org": ORG, **params} | ({"as_of": at.isoformat()} if at else {})
    response = client.get(path, params=query)
    assert response.status_code == 200, response.text
    rows: list[dict[str, Any]] = response.json()
    return rows


def test_the_present_read_as_of_equals_the_present(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    # The replay up to the last row and the projection tables are the
    # same rows by the same order, on all three reads.
    with app as client:
        for path, extra in (
            ("/trades", {}),
            ("/positions", {}),
            ("/valuations", {}),
            ("/valuations", {"latest": "true"}),
        ):
            assert _get(client, path, moments["end"], **extra) == _get(client, path, **extra)


def test_before_the_correction_every_mark_is_against_the_first_curve(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    # The issue's own "done when": the figures before and after the
    # correction's instant differ as history says they should.
    with app as client:
        german = {t["trade"] for t in _get(client, "/trades", market="de-power")}
        then = [
            mark
            for mark in _get(client, "/valuations", moments["before_correction"], latest="true")
            if mark["trade"] in german
        ]
        now = [
            mark for mark in _get(client, "/valuations", latest="true") if mark["trade"] in german
        ]
    assert then
    # The German curve is the one that was corrected; the British book
    # has its own curve and is not part of this story.
    assert {mark["currency"] for mark in then} == {"EUR"}
    assert len({mark["curve_version"] for mark in then}) == 1
    (first_curve,) = {mark["curve_version"] for mark in then}
    assert any(mark["curve_version"] != first_curve for mark in now)
    assert sum(float(m["mtm"]) for m in then) != sum(float(m["mtm"]) for m in now)


def test_before_the_amendment_the_trade_carries_its_first_terms(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    with app as client:
        then = {t["trade"]: t for t in _get(client, "/trades", moments["before_amendment"])}
        now = {t["trade"]: t for t in _get(client, "/trades")}
        positions_then = _get(client, "/positions", moments["before_amendment"])
        positions_now = _get(client, "/positions")
    amended = [trade for trade, row in now.items() if row["amendment_count"]]
    assert amended
    for trade in amended:
        assert then[trade]["amendment_count"] == 0
        assert then[trade]["terms_version"] != now[trade]["terms_version"]
    # The amendment moved the book's hours, so the position at that
    # moment is not today's.
    assert positions_then != positions_now


def test_before_the_first_capture_the_book_is_empty(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    with app as client:
        assert _get(client, "/trades", moments["before_capture"]) == []
        assert _get(client, "/positions", moments["before_capture"]) == []
        assert len(_get(client, "/trades")) == 8


def test_an_instant_that_is_not_history_is_the_callers_error(app: TestClient) -> None:
    future = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()
    with app as client:
        refused = client.get("/trades", params={"org": ORG, "as_of": future})
        malformed = client.get("/trades", params={"org": ORG, "as_of": "last Tuesday"})
    assert refused.status_code == 422
    assert "in the future" in refused.json()["detail"]
    assert malformed.status_code == 422


def test_the_screens_say_which_moment_they_show(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    # A datetime-local value, as the top bar sends it: no offset, UTC.
    at = moments["before_capture"].strftime("%Y-%m-%dT%H:%M:%S.%f")
    with app as client:
        blotter = client.get("/ui/blotter", params={"org": ORG, "as_of": at})
        positions = client.get("/ui/positions", params={"org": ORG, "as_of": at})
        overview = client.get("/ui", params={"org": ORG, "as_of": at})
        current = client.get("/ui/blotter", params={"org": ORG})
    assert blotter.status_code == positions.status_code == overview.status_code == 200
    # The past is announced, and the empty book of that moment is shown.
    assert "You are viewing the book as it stood at" in blotter.text
    assert "No trades match this view." in blotter.text
    assert "T-001" not in blotter.text
    assert "You are viewing the book as it stood at" in positions.text
    # Moving between screens stays in the mode; leaving it is one link.
    assert "/ui/positions?org=acme-energy&amp;as_of=" in blotter.text
    assert "Back to now" in blotter.text
    # A screen that cannot answer for the past says so, never pretends.
    assert "This screen always shows the present." in overview.text
    # And the present is still the present.
    assert "Viewing: now" in current.text
    assert "T-001" in current.text


def test_a_future_moment_is_a_422_page_not_a_stale_screen(app: TestClient) -> None:
    with app as client:
        response = client.get("/ui/positions", params={"org": ORG, "as_of": "2999-01-01T00:00"})
    assert response.status_code == 422
    assert "Check the as-of time" in response.text
    # A page that refused the moment is not showing it: no banner and no
    # pill may say the book is being viewed as it stood then.
    assert "You are viewing the book as it stood" not in response.text
    assert "Viewing the past" not in response.text
    assert "Viewing: now" in response.text


def test_a_window_bound_with_no_offset_reads_the_same_then_and_now(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    # A naive bound was a 500 on the as-of path (a naive time compared
    # with a stored instant). It is UTC on both paths, and an offset is
    # converted, so the same window gives the same hours.
    with app as client:
        whole = _get(client, "/positions")
        first = dt.datetime.fromisoformat(whole[0]["period_start"])
        naive = first.replace(tzinfo=None).isoformat()
        shifted = first.astimezone(dt.timezone(dt.timedelta(hours=2))).isoformat()
        window_end = (first + dt.timedelta(hours=3)).replace(tzinfo=None).isoformat()
        now = _get(client, "/positions", start=naive, end=window_end)
        then = _get(client, "/positions", moments["end"], start=naive, end=window_end)
        by_offset = _get(client, "/positions", moments["end"], start=shifted, end=window_end)
    assert 0 < len({row["period_start"] for row in now}) <= 3
    assert len(now) < len(whole)
    assert then == now == by_offset


def test_an_instant_a_writer_may_still_precede_is_refused(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    # A writer holds the next place in the audit order and has not
    # finished (played here by the advisory lock a writer takes with its
    # place). The moment just passed could still gain that writer's rows,
    # so it is refused, on the API and on the screen, exactly as the
    # substrate refuses it; the last row's own instant lies below the held
    # order and is still answered. Once the writer is gone, the refused
    # instant is answerable and its answer is final.
    holder_engine = sa.create_engine(engine_url(DB))
    try:
        with app as client, holder_engine.connect() as holder:
            place = holder.execute(
                sa.text("select last_value + 1 from morpholog.audit_order_seq")
            ).scalar_one()
            holder.execute(
                sa.text("select pg_advisory_xact_lock((1::bigint << 62) | :place)"),
                {"place": place},
            )
            just_now = dt.datetime.now(dt.UTC)
            query = {"org": ORG, "as_of": just_now.isoformat()}
            refused = client.get("/positions", params=query)
            page = client.get("/ui/positions", params=query)
            settled = _get(client, "/trades", moments["end"])
            holder.rollback()
            after = _get(client, "/trades", just_now)
    finally:
        holder_engine.dispose()
    assert refused.status_code == 409, refused.text
    assert "not final yet" in refused.json()["detail"]
    assert refused.headers["Retry-After"] == "1"
    assert page.status_code == 409
    assert "That moment is not final yet" in page.text
    assert len(settled) == len(after) == 8


def test_the_screen_shows_utc_for_a_moment_written_with_an_offset(
    app: TestClient, moments: dict[str, dt.datetime]
) -> None:
    at = moments["before_amendment"].replace(microsecond=0) + dt.timedelta(seconds=1)
    plus_two = at.astimezone(dt.timezone(dt.timedelta(hours=2)))
    with app as client:
        page = client.get("/ui/blotter", params={"org": ORG, "as_of": plus_two.isoformat()})
    assert page.status_code == 200
    assert f"Viewing the past: {at:%Y-%m-%d %H:%M:%S} UTC" in page.text
    assert f"{plus_two:%H:%M:%S}+02:00" not in page.text
