"""Scaffold smoke tests: the app constructs and answers /healthz."""

from fastapi.testclient import TestClient

from glasshouse import __version__
from glasshouse.api.app import create_app


def test_healthz() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_serves() -> None:
    client = TestClient(create_app())
    assert client.get("/openapi.json").status_code == 200
