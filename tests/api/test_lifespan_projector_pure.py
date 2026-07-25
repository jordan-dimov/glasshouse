"""The demo profile's background projector in the web lifespan: started
only in the demo environment, stopped and joined on shutdown, RETRYING
through operational failure (a dead or restarting database is weather,
not a reason to die), and visible through /readyz - a thread that does
die is a loud readiness verdict, never a silent lag.
"""

import threading

import pytest
from fastapi.testclient import TestClient

from glasshouse.api import app as app_module

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


def test_the_thread_starts_only_in_the_demo_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    def fake_start(client: object, engine: object) -> tuple[threading.Thread, threading.Event]:
        started.append("demo")
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        return thread, stop

    monkeypatch.setattr(app_module, "start_projector_thread", fake_start)

    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "dev")
    with TestClient(app_module.create_app()):
        pass
    assert started == []  # dev composes its own run mode

    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()):
        pass
    assert started == ["demo"]


def test_the_projector_retries_through_a_dead_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An operational failure is a condition that passes, not a death
    # sentence: against the dead database the thread stays alive and
    # retrying, /readyz reports it ok (the DATABASE verdict carries the
    # outage), the screens keep serving, and shutdown is prompt because
    # the backoff waits on the stop event.
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/healthz").status_code == 200
        verdicts = client.get("/readyz").json()
        assert verdicts["database"] == "error"  # the outage, named honestly
        assert verdicts["projector"] == "ok"  # alive and retrying, not dead
        thread = client.app.state.projector_thread  # type: ignore[attr-defined]
        assert thread is not None
        assert thread.is_alive()
    assert not thread.is_alive()  # the stop event ended the backoff promptly


def test_a_dead_projector_is_a_loud_readiness_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the thread ever DOES die (a semantic ProjectionError, an
    # unforeseen crash), /readyz must say so rather than let projection
    # lag silently until the next restart.
    def fake_start(client: object, engine: object) -> tuple[threading.Thread, threading.Event]:
        thread = threading.Thread(target=lambda: None, daemon=True)
        thread.start()
        thread.join()  # already dead by the time anyone looks
        return thread, threading.Event()

    monkeypatch.setattr(app_module, "start_projector_thread", fake_start)
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()) as client:
        verdicts = client.get("/readyz").json()
    assert verdicts["projector"] == "error"


def test_dev_readiness_carries_no_projector_verdict() -> None:
    with TestClient(app_module.create_app()) as client:
        verdicts = client.get("/readyz").json()
    assert "projector" not in verdicts  # dev has no thread to answer for
