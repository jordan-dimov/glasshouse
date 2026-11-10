"""As-of viewing against the real stack (glasshouse#41): the seeded book
read at past instants taken from its own audit tail. The curve correction
and the amendment are the two moments the numbers must differ across, and
the present read as-of must equal the present read from the tables,
which is the replay and the projector agreeing row for row.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from glasshouse.compute.store import CurveStore
from glasshouse.seed import seed_demo
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG = "acme-energy"


@pytest.fixture(scope="module")
def moments() -> dict[str, dt.datetime]:
    """The instants this module reads at, each the placing of the last
    transition before an event: `before_capture`, `before_correction`,
    `before_amendment`, and `end`, the placing of the last row."""
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    seed_demo(client, CurveStore(engine), engine)
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
) -> list[dict]:  # type: ignore[type-arg]  # type: ignore[type-arg]
    query = {"org": ORG, **params} | ({"as_of": at.isoformat()} if at else {})
    response = client.get(path, params=query)
    assert response.status_code == 200, response.text
    rows: list[dict] = response.json()  # type: ignore[type-arg]
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
        then = _get(client, "/valuations", moments["before_correction"], latest="true")
        now = _get(client, "/valuations", latest="true")
    assert then
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
        assert len(_get(client, "/trades")) == 6


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
    assert "Back to current" in blotter.text
    # A screen that cannot answer for the past says so, never pretends.
    assert "This screen shows current data." in overview.text
    # And the present is still the present.
    assert "Viewing: Current" in current.text
    assert "T-001" in current.text


def test_a_future_moment_is_a_422_page_not_a_stale_screen(app: TestClient) -> None:
    with app as client:
        response = client.get("/ui/positions", params={"org": ORG, "as_of": "2999-01-01T00:00"})
    assert response.status_code == 422
    assert "Check the as-of time" in response.text
