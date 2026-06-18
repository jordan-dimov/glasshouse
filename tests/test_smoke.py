"""Scaffold smoke tests: the app constructs, answers /healthz, and
/readyz tells deployment hooks the truth in three independent verdicts
(binary, database, and the commit layer that needs both).

The pure leg runs with a deliberately dead database URL so its verdicts
are deterministic whatever is running locally; the all-ok 200 lives in
the env-gated integration leg (tests/api/)."""

import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glasshouse import __version__
from glasshouse.api.app import create_app

# Connection refused instantly; nothing listens on port 1.
DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


def fake_binary(tmp_path: Path, exit_code: int) -> Path:
    binary = tmp_path / "morpholog"
    binary.write_text(f"#!/bin/sh\nexit {exit_code}\n")
    binary.chmod(0o755)
    return binary


def test_healthz() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_openapi_serves() -> None:
    with TestClient(create_app()) as client:
        assert client.get("/openapi.json").status_code == 200


def test_readyz_is_503_when_the_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/nonexistent/morpholog")
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"morpholog": "missing", "database": "error", "commit": "error"}


def test_readyz_reports_a_binary_that_cannot_speak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(fake_binary(tmp_path, exit_code=1)))
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["morpholog"] == "error"


def test_readyz_verdicts_are_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The binary speaks, but the database is dead - so the commit
    # check, which needs both, is honest about it too.
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(fake_binary(tmp_path, exit_code=0)))
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"morpholog": "ok", "database": "error", "commit": "error"}


def test_readyz_reports_a_hanging_binary_as_error_not_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(fake_binary(tmp_path, exit_code=0)))

    def hang(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="morpholog --version", timeout=10)

    monkeypatch.setattr("glasshouse.api.app.subprocess.run", hang)
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["morpholog"] == "error"


def test_readyz_bounds_a_commit_probe_that_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --version answers instantly; the governed read hangs. The client's
    # timeout turns that into a fast error verdict, not a stuck request.
    two_faced = tmp_path / "morpholog"
    two_faced.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then exit 0; fi\nsleep 5\n')
    two_faced.chmod(0o755)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(two_faced))
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_TIMEOUT_SECONDS", "0.2")

    started = time.monotonic()
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["morpholog"] == "ok"
    assert response.json()["commit"] == "error"
    assert elapsed < 3  # the probe was bounded, not the sleep
