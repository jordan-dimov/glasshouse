"""Scaffold smoke tests: the app constructs, answers /healthz, and
/readyz tells deployment hooks the truth about the binary."""

from pathlib import Path

import pytest
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


def test_readyz_is_503_when_the_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/nonexistent/morpholog")
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"morpholog": "missing"}


def test_readyz_reports_a_binary_that_cannot_speak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "morpholog"
    broken.write_text("#!/bin/sh\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(broken))
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"morpholog": "error"}


def test_readyz_is_ok_with_a_working_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    working = tmp_path / "morpholog"
    working.write_text("#!/bin/sh\nexit 0\n")
    working.chmod(0o755)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(working))
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"morpholog": "ok"}
