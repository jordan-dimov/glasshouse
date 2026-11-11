"""The three screens against the real stack, fed by the seed dataset:
every number an exact string, negatives visible, filters narrowing,
the fragment a fragment, tenancy explicit even when empty.

Self-provisioned module slate, same contract as every integration leg.
"""

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from tests.support import BINARY, DB, needs_live_stack, seeded_ledger

pytestmark = needs_live_stack

ORG = "acme-energy"


@pytest.fixture(scope="module")
def seeded() -> sa.Engine:
    _, engine = seeded_ledger()
    return engine


@pytest.fixture
def ui(seeded: sa.Engine, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    return TestClient(create_app())


def test_with_one_organisation_there_is_nothing_to_pick(ui: TestClient) -> None:
    # A page whose only content is one link is a wasted click: the one
    # organisation's overview is where a visitor lands.
    with ui as client:
        response = client.get("/ui", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/ui?org={ORG}"


def test_positions_open_with_each_book_against_its_limit(ui: TestClient) -> None:
    with ui as client:
        page = client.get("/ui/positions", params={"org": ORG}).text
    assert "Against the limits" in page
    # spec-de peaks at 20 MW in the 12:00 hour, the cap is 25, so 5 left.
    limits = page[page.index("Against the limits") : page.index("What each trade is worth")]
    row = limits[limits.index("<td>spec-de</td>") :]
    row = row[: row.index("</tr>")]
    assert 'class="numeric">20<' in row
    assert "2026-07-01 12:00Z" in row
    assert 'class="numeric">25<' in row
    assert 'class="numeric">5<' in row
    # The British book has no cap, and the screen says so.
    assert "no limit" in limits
    # The summary and the valuations come before the long hourly table.
    assert page.index("What each trade is worth") < page.index("Position hour by hour")
    # No internal record id takes up a column.
    assert '<th scope="col">Transition</th>' not in page


def test_filters_offer_what_exists(ui: TestClient) -> None:
    with ui as client:
        trades = client.get("/ui/blotter", params={"org": ORG, "book": "spec-gb"}).text
    for option in ("hedge-de", "spec-de", "de-power", "gb-power"):
        assert f'<option value="{option}"' in trades
    assert '<option value="spec-gb" selected>' in trades
    assert "exact match" not in trades
    assert "G-001" in trades
    assert "T-001" not in trades


def test_the_curves_screen_shows_the_prices_in_use(ui: TestClient) -> None:
    with ui as client:
        page = client.get("/ui/curves", params={"org": ORG, "market": "gb-power"}).text
        german = client.get("/ui/curves", params={"org": ORG, "market": "de-power"}).text
    assert "Prices in use: crv-gb-2026-07-01-v1" in page
    assert "Price (GBP per MWh)" in page
    assert 'class="numeric">60<' in page  # midnight
    assert 'class="numeric">83<' in page  # 23:00
    # One version: nothing to compare, so no compare controls.
    assert "Compare the two versions" not in page
    # Two versions: the one in use against the one it replaced is chosen.
    assert "Compare the two versions" in german
    assert 'name="compare" value="crv-2026-07-01-v2" checked' in german
    assert 'name="base" value="crv-2026-07-01-v1" checked' in german


def test_overview_renders_the_seeded_day(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui", params={"org": ORG})
    assert response.status_code == 200
    assert "spec-de" in response.text
    assert "hedge-de" in response.text
    assert "3 trades" in response.text  # spec-de carries T-001, T-002, T-005
    assert "<h2>System</h2>" in response.text


def test_blotter_renders_exact_terms_and_filters_narrow(ui: TestClient) -> None:
    with ui as client:
        full = client.get("/ui/blotter", params={"org": ORG})
        by_book = client.get("/ui/blotter", params={"org": ORG, "book": "hedge-de"})
        by_market = client.get("/ui/blotter", params={"org": ORG, "market": "fr-power"})
    assert full.status_code == 200
    assert "T-001" in full.text
    assert "7.5" in full.text  # T-002's quantity, the exact string
    assert "Next 50" not in full.text  # six trades: one page, honestly
    assert "T-003" in by_book.text
    assert "T-001" not in by_book.text
    assert "No trades match" in by_market.text


def test_the_blotter_fragment_swaps_in_place(ui: TestClient) -> None:
    with ui as client:
        fragment = client.get(
            "/ui/blotter",
            params={"org": ORG},
            headers={"HX-Request": "true", "HX-Request-Type": "partial"},
        )
        # A back navigation: htmx asks for the page it pushed, targeting
        # the body, and must get the chrome back with the rows.
        restored = client.get(
            "/ui/blotter",
            params={"org": ORG},
            headers={
                "HX-Request": "true",
                "HX-Request-Type": "full",
                "HX-History-Restore-Request": "true",
            },
        )
    assert fragment.status_code == 200
    assert "T-001" in fragment.text
    assert "<html" not in fragment.text
    assert restored.status_code == 200
    assert "T-001" in restored.text
    assert "<html" in restored.text
    # Both representations of the same URL tell caches what they vary on.
    assert fragment.headers["vary"] == "HX-Request-Type"
    assert restored.headers["vary"] == "HX-Request-Type"


def test_positions_show_the_short_hour_and_current_marks(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui/positions", params={"org": ORG})
    assert response.status_code == 200
    # T-006 alone in hedge-de makes a net-short hour; T-001, struck above
    # the curve, carries a negative mark - both rendered loud.
    assert 'class="numeric neg">-15<' in response.text
    assert 'class="numeric neg">-100<' in response.text
    assert response.text.count("crv-2026-07-01-v2") == 6  # one current mark per trade, all on v2
    with ui as client:
        windowed = client.get(
            "/ui/positions",
            params={"org": ORG, "start": "2026-07-01T09:00", "end": "2026-07-01T10:00"},
        )
    assert windowed.status_code == 200
    assert "2026-07-01 09:00Z" in windowed.text
    assert "2026-07-01 10:00Z" not in windowed.text


def test_the_positions_filter_bar_narrows_the_marks_too(ui: TestClient) -> None:
    # One filter bar governs both sections: a book filter must not show
    # hedge-de positions above marks from the whole organisation.
    with ui as client:
        by_book = client.get("/ui/positions", params={"org": ORG, "book": "hedge-de"})
        by_market = client.get("/ui/positions", params={"org": ORG, "market": "fr-power"})
    assert "T-003" in by_book.text  # a hedge-de mark stays
    assert "T-001" not in by_book.text  # every spec-de mark is gone
    assert by_book.text.count("crv-2026-07-01-v2") == 3  # hedge-de's three current marks only
    assert by_market.text.count("crv-2026-07-01-v2") == 0
    assert "No trade here has been valued" in by_market.text


def test_a_second_org_is_empty_but_visible(ui: TestClient) -> None:
    with ui as client:
        response = client.get("/ui/blotter", params={"org": "someone-else"})
    assert response.status_code == 200
    assert "No trades match" in response.text
    # The requested org joins the selector: tenancy stays explicit even
    # on an empty screen.
    assert '<option value="someone-else" selected>' in response.text
    assert f'<option value="{ORG}" ' in response.text


def test_the_blotter_and_marks_show_the_amendment(ui: TestClient) -> None:
    # The seed amends T-003 (5 -> 8 MW) and re-marks it: the blotter row
    # carries the current version with its badge, and the latest mark
    # names the terms it was struck under (law 5, in place).
    with ui as client:
        blotter = client.get("/ui/blotter", params={"org": ORG, "book": "hedge-de"})
        positions = client.get("/ui/positions", params={"org": ORG, "book": "hedge-de"})
    assert "T-003/v2" in blotter.text
    assert "changed 1 time" in blotter.text
    assert 'class="numeric">8<' in blotter.text
    assert "T-003/v2" in positions.text
    assert 'class="numeric">120<' in positions.text  # 8 MW * 15 EUR over hours 00-06
