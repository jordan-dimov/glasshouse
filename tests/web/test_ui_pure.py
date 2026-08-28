"""The Control Room's deterministic legs: routing, the org rule, the
HTML 503 face, static assets, and the UTC filter boundary - all against
a deliberately dead database, so the verdicts hold whatever is running
locally.
"""

import base64
import datetime as dt
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.web import STATIC_DIR
from glasshouse.web.routes import _utc_instant
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


def test_root_redirects_to_the_control_room() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/ui"


@pytest.mark.parametrize(
    "path", ["/ui/blotter", "/ui/positions", "/ui/curves", "/ui/imports", "/ui/audit"]
)
def test_a_screen_without_an_org_goes_to_the_picker(path: str) -> None:
    # A 303 before any database work: the dead database proves no query
    # ran on the way out.
    with TestClient(create_app()) as client:
        response = client.get(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ui"


def test_a_dead_commit_layer_is_an_html_503_on_the_audit_screen(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The audit screen's subject is the ledger, so its client read comes
    # first; an operationally failing binary is the same honest 503 as a
    # dead database, in HTML.
    broken = fake_binary(tmp_path, "", stderr="Error: database unreachable", exit_code=1)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(broken))
    with TestClient(create_app()) as client:
        response = client.get("/ui/audit", params={"org": "acme-energy"})
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "database is unavailable" in response.text


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


def test_every_screen_footer_names_the_deployed_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b1c8b89d6b3c56fbb5aee2f3ff657fdf38efa312")
    with TestClient(create_app()) as client:
        page = client.get("/ui")
    # The dead database makes this the 503 face, which shares the chrome:
    # even the unavailable screen says which build is answering.
    assert page.status_code == 503
    assert 'title="b1c8b89d6b3c56fbb5aee2f3ff657fdf38efa312">b1c8b89d6b3c<' in page.text


def test_the_vendored_htmx_is_the_one_its_record_names() -> None:
    # VENDORED.md records what was fetched; the file is what is served.
    # A replaced file behind a stale record, or a record bumped without
    # the file, fails here rather than in a browser.
    vendor = STATIC_DIR / "vendor"
    record = (vendor / "VENDORED.md").read_text()
    version = re.search(r"^- Version: (\S+)$", record, re.MULTILINE)
    digest = re.search(r"^- SHA-256: ([0-9a-f]{64})$", record, re.MULTILINE)
    assert version is not None
    assert digest is not None
    script = (vendor / "htmx.min.js").read_bytes()
    assert hashlib.sha256(script).hexdigest() == digest.group(1)
    assert f'version="{version.group(1)}"'.encode() in script


def test_a_fragment_request_gets_a_fragment_503_and_a_restore_the_page() -> None:
    # htmx swaps error responses in, so the 503 must arrive in the shape
    # the requester will place: bare for a panel swap, whole for a
    # history restore (an htmx request too, but one targeting the body).
    with TestClient(create_app()) as client:
        fragment = client.get(
            "/ui/blotter",
            params={"org": "acme-energy"},
            headers={"HX-Request": "true", "HX-Request-Type": "partial"},
        )
        restore = client.get(
            "/ui/blotter",
            params={"org": "acme-energy"},
            headers={
                "HX-Request": "true",
                "HX-Request-Type": "full",
                "HX-History-Restore-Request": "true",
            },
        )
    assert fragment.status_code == 503
    assert "database is unavailable" in fragment.text
    assert "<html" not in fragment.text
    assert restore.status_code == 503
    assert "database is unavailable" in restore.text
    assert "<html" in restore.text
    # Two representations of one URL: a cache must key on the header.
    assert fragment.headers["vary"] == "HX-Request-Type"
    assert restore.headers["vary"] == "HX-Request-Type"


def test_a_malformed_time_window_is_a_422_not_a_503() -> None:
    # Database-free by construction: the dead database would turn any
    # chrome query into a 503, so a 422 here proves none ran.
    with TestClient(create_app()) as client:
        response = client.get(
            "/ui/positions", params={"org": "acme-energy", "start": "yesterday-ish"}
        )
    assert response.status_code == 422
    assert "UTC" in response.text


def _preview(client, **overrides: object):  # type: ignore[no-untyped-def]
    form = {"org": "acme-energy", "kind": "trades", "actor": "alice"}
    files = {"file": ("trades.csv", overrides.pop("data", b"book,trade\n1,2\n"), "text/csv")}
    form.update({k: str(v) for k, v in overrides.items()})
    return client.post("/ui/imports/preview", data=form, files=files)


def test_upload_guards_refuse_before_any_backend_work() -> None:
    # All four refusals are decided on the upload's own evidence: the
    # dead database proves neither the ledger nor the read model was
    # consulted on the way out.
    with TestClient(create_app()) as client:
        oversized = _preview(client, data=b"x" * (512 * 1024 + 1))
        not_text = _preview(client, data="café".encode("utf-16"))
        bad_header = _preview(client, data=b"not,the,contract\n1,2,3\n")
        bad_kind = _preview(client, kind="spreadsheets")
    assert oversized.status_code == 413
    assert "512 KiB" in oversized.text
    assert not_text.status_code == 422
    assert bad_header.status_code == 422
    assert bad_kind.status_code == 422


def test_a_damaged_preview_payload_is_refused_before_any_backend_work() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/ui/imports/commit",
            data={
                "org": "acme-energy",
                "kind": "trades",
                "actor": "alice",
                "text_b64": "not!!!base64",
            },
        )
    assert response.status_code == 422
    assert "preview" in response.text


def test_a_hosted_demo_without_a_login_refuses_browser_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A missing env var must never leave a public deployment writable.
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(create_app()) as client:
        preview = _preview(client)
        commit = client.post(
            "/ui/imports/commit",
            data={"org": "acme-energy", "kind": "trades", "actor": "alice", "text_b64": "Ym9vaw=="},
        )
    assert preview.status_code == 403
    assert commit.status_code == 403
    assert "need the demo login" in preview.text


def test_the_demo_with_a_login_is_not_fenced(monkeypatch: pytest.MonkeyPatch) -> None:
    # With the login configured the gate is the guard, not the fence:
    # an authenticated preview proceeds to the handler's own verdicts.
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", "a-long-demo-password")
    with TestClient(create_app()) as client:
        response = client.post(
            "/ui/imports/preview",
            data={"org": "acme-energy", "kind": "spreadsheets", "actor": ""},
            files={"file": ("t.csv", b"book,trade\n", "text/csv")},
            auth=("demo", "a-long-demo-password"),
        )
    assert response.status_code == 422  # the form check, not a fence 403
    assert "trades or curves" in response.text


def test_the_login_derives_the_actor_and_hides_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", "a-long-demo-password")
    with TestClient(create_app()) as client:
        page = client.get(
            "/ui/imports", params={"org": "acme-energy"}, auth=("demo", "a-long-demo-password")
        )
        # An empty actor field with a damaged payload: the refusal must be
        # about the payload, proving the derived identity satisfied the
        # actor check before any backend work.
        commit = client.post(
            "/ui/imports/commit",
            data={"org": "acme-energy", "kind": "trades", "actor": "", "text_b64": "not!!!b64"},
            auth=("demo", "a-long-demo-password"),
        )
    assert page.status_code == 503  # dead DB chrome; the template legs are pure below
    assert commit.status_code == 422
    assert "preview payload is damaged" in commit.text
    assert "An actor is required" not in commit.text


def test_browser_writes_are_fenced_off_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    # Typed-actor identity is an L0 convenience; production refuses the
    # browser write path entirely until authenticated identity lands.
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "production")
    with TestClient(create_app()) as client:
        preview = _preview(client)
        commit = client.post(
            "/ui/imports/commit",
            data={"org": "acme-energy", "kind": "trades", "actor": "alice", "text_b64": "Ym9vaw=="},
        )
    assert preview.status_code == 403
    assert commit.status_code == 403
    assert "fenced off in production" in preview.text


def test_the_browser_row_cap_points_at_the_cli() -> None:
    # 2001 data rows is small in bytes but long in subprocess work (one
    # explain per row on preview, one batch on commit): the browser path
    # refuses and names the CLI, database-free.
    many_rows = (
        "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end\n"
        + "b,t,c,m,buy,1,1,x,y\n" * 2001
    )
    with TestClient(create_app()) as client:
        response = _preview(client, data=many_rows.encode())
    assert response.status_code == 413
    assert "Too many rows" in response.text
    assert "CLI" in response.text


def test_the_commit_endpoint_enforces_the_same_cap_as_the_upload() -> None:
    # The commit endpoint is directly reachable, so an oversized payload
    # must not bypass the preview's 512 KiB cap - refused on the encoded
    # length before any decoding, database-free.
    oversized = base64.b64encode(b"x" * (512 * 1024 + 3)).decode()
    with TestClient(create_app()) as client:
        response = client.post(
            "/ui/imports/commit",
            data={
                "org": "acme-energy",
                "kind": "trades",
                "actor": "alice",
                "text_b64": oversized,
            },
        )
    assert response.status_code == 413
    assert "512 KiB" in response.text


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
