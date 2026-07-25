"""The demo login's deterministic legs: the gate answers before routing
(mounts and 404s included), the deployment probes stay open, credentials
verify constant-time on both halves, cross-site unsafe requests are
refused, and an unset password means no gate at all - all against the
dead database, so every verdict is the gate's own.
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from glasshouse.api.app import create_app

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"
PASSWORD = "correct horse battery staple"
CREDS = ("demo", PASSWORD)


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", PASSWORD)


@pytest.mark.parametrize(
    "path",
    ["/ui", "/trades", "/", "/static/css/tokens.css", "/no-such-path"],
)
def test_everything_is_challenged_without_credentials(path: str) -> None:
    # The 404 path proves the gate runs BEFORE routing: an unauthenticated
    # crawler learns nothing about the URL space.
    with TestClient(create_app()) as client:
        response = client.get(path)
    assert response.status_code == 401
    assert 'Basic realm="glasshouse demo"' in response.headers["www-authenticate"]
    assert response.json() == {"detail": "authentication required"}


@pytest.mark.parametrize(
    "credentials",
    [("demo", "wrong password"), ("mallory", PASSWORD), ("", "")],
)
def test_wrong_credentials_are_challenged(credentials: tuple[str, str]) -> None:
    with TestClient(create_app()) as client:
        response = client.get("/ui", auth=credentials)
    assert response.status_code == 401


def test_the_deployment_probes_stay_open() -> None:
    with TestClient(create_app()) as client:
        healthz = client.get("/healthz")
        readyz = client.get("/readyz")
    assert healthz.status_code == 200  # Render's healthCheckPath, no credentials
    assert readyz.status_code == 503  # the dead stack's honest verdict, never a 401


def test_credentials_pass_through_to_the_existing_faces() -> None:
    with TestClient(create_app()) as client:
        html = client.get("/ui", params={"org": "acme-energy"}, auth=CREDS)
        json_face = client.get("/trades", params={"org": "acme-energy"}, auth=CREDS)
        static = client.get("/static/css/tokens.css", auth=CREDS)
        no_org = client.get("/ui/blotter", auth=CREDS, follow_redirects=False)
    assert html.status_code == 503  # the dead-DB HTML face, reached through the gate
    assert json_face.json() == {"detail": "database unavailable"}
    assert static.status_code == 200
    assert no_org.status_code == 303  # routing behaviour unchanged behind the gate


@pytest.mark.parametrize("weak", ["", "   ", "short", "elevenchars"])
def test_a_blank_or_weak_password_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch, weak: str
) -> None:
    # The password is the whole perimeter of a public deployment: a
    # blank or trivial value fails LOUDLY at settings construction,
    # never quietly enables the gate with an empty secret (or lifts the
    # demo write fence).
    monkeypatch.setenv("GLASSHOUSE_DEMO_PASSWORD", weak)
    with pytest.raises(ValidationError, match="at least 12"):
        create_app()


def test_cross_site_unsafe_requests_are_refused() -> None:
    form = {"org": "acme-energy", "kind": "trades", "actor": "x", "text_b64": "Ym9vaw=="}
    with TestClient(create_app()) as client:
        hostile = client.post(
            "/ui/imports/commit",
            data=form,
            auth=CREDS,
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        same_origin = client.post(
            "/ui/imports/commit",
            data=form,
            auth=CREDS,
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        sibling = client.post(
            "/ui/imports/commit",
            data=form,
            auth=CREDS,
            headers={"Sec-Fetch-Site": "same-site"},
        )
        headerless = client.post("/ui/imports/commit", data=form, auth=CREDS)
        safe_get = client.get(
            "/ui", params={"org": "x"}, auth=CREDS, headers={"Sec-Fetch-Site": "cross-site"}
        )
    assert hostile.status_code == 403
    assert hostile.json() == {"detail": "cross-site request refused"}
    # A sibling subdomain (another *.a115.co.uk origin) is same-site,
    # not same-origin: with Basic's ambient credentials it is just as
    # hostile a write vector, and is refused too.
    assert sibling.status_code == 403
    # Same-origin browsers and header-less clients (curl) proceed to the
    # handler's own verdicts; safe methods are never blocked.
    assert same_origin.status_code != 403
    assert headerless.status_code != 403
    assert safe_get.status_code != 403


def test_an_unset_password_means_no_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GLASSHOUSE_DEMO_PASSWORD")
    with TestClient(create_app()) as client:
        response = client.get("/ui/blotter", follow_redirects=False)
    assert response.status_code == 303  # straight to the picker, no challenge
