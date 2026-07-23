"""The Control Room's deterministic legs: routing, the org rule, the
HTML 503 face, static assets, and the UTC filter boundary - all against
a deliberately dead database, so the verdicts hold whatever is running
locally.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.web.routes import _utc_instant

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


def test_root_redirects_to_the_control_room() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui"


@pytest.mark.parametrize("path", ["/ui/blotter", "/ui/positions"])
def test_a_screen_without_an_org_goes_to_the_picker(path: str) -> None:
    # A 303 before any database work: the dead database proves no query
    # ran on the way out.
    with TestClient(create_app()) as client:
        response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ui"


def test_a_dead_database_is_an_html_503_on_ui_paths() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/ui", params={"org": "acme-energy"})
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "database is unavailable" in response.text
    # The JSON surface keeps its pinned body on the same verdict.
    with TestClient(create_app()) as client:
        json_response = client.get("/trades", params={"org": "acme-energy"})
    assert json_response.json() == {"detail": "database unavailable"}


def test_static_assets_are_served() -> None:
    with TestClient(create_app()) as client:
        css = client.get("/static/css/tokens.css")
        script = client.get("/static/vendor/htmx.min.js")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "--accent" in css.text
    assert script.status_code == 200


def test_a_malformed_time_window_is_a_422_not_a_503() -> None:
    # Database-free by construction: the dead database would turn any
    # chrome query into a 503, so a 422 here proves none ran.
    with TestClient(create_app()) as client:
        response = client.get(
            "/ui/positions", params={"org": "acme-energy", "start": "yesterday-ish"}
        )
    assert response.status_code == 422
    assert "UTC" in response.text


def test_datetime_local_values_are_defined_as_utc() -> None:
    # Law 9 at the filter boundary: a browser's offset-less value becomes
    # an aware UTC instant, an aware value is converted, blank is None.
    parsed = _utc_instant("2026-07-01T08:30")
    assert parsed == dt.datetime(2026, 7, 1, 8, 30, tzinfo=dt.UTC)
    aware = _utc_instant("2026-07-01T08:30+02:00")
    assert aware == dt.datetime(2026, 7, 1, 6, 30, tzinfo=dt.UTC)
    assert _utc_instant(None) is None
    assert _utc_instant("") is None
    with pytest.raises(ValueError, match="Invalid isoformat"):
        _utc_instant("yesterday-ish")
