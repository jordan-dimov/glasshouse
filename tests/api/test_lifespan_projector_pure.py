"""The demo profile's background projector in the web lifespan: started
only in the demo environment, stopped and joined on shutdown, RETRYING
through operational failure (a dead or restarting database is weather,
not a reason to die), and visible through /readyz.

What "visible" means changed after the demo went live. The verdict used
to be the thread's liveness, and retrying keeps a thread alive through
anything - so a projector that had refused twenty-one consecutive audit
tails and applied nothing reported itself healthy for a quarter of an
hour. The verdict now reads the projector's own progress: a short
outage is tolerated (the database verdict carries it), a condition that
outlasts the threshold is unready.
"""

import threading

import pytest
from fastapi.testclient import TestClient

from glasshouse.api import app as app_module
from glasshouse.api import health
from glasshouse.commit import MorphologError
from glasshouse.projections.runner import ProjectorStatus, RunningProjector

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


def _stub_projector(*, alive: bool = True) -> RunningProjector:
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait if alive else (lambda: None), daemon=True)
    thread.start()
    if not alive:
        thread.join()
    return RunningProjector(thread=thread, stop=stop, status=ProjectorStatus())


def test_the_thread_starts_only_in_the_demo_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    def fake_start(client: object, engine: object) -> RunningProjector:
        started.append("demo")
        return _stub_projector()

    monkeypatch.setattr(app_module, "start_projector_thread", fake_start)

    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "dev")
    with TestClient(app_module.create_app()):
        pass
    assert started == []  # dev composes its own run mode

    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()):
        pass
    assert started == ["demo"]


def test_a_brief_outage_leaves_the_projector_verdict_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An operational failure is a condition that passes, not a death
    # sentence: against the dead database the thread stays alive and
    # retrying, the DATABASE verdict carries the outage, the screens keep
    # serving, and shutdown is prompt because the backoff waits on the
    # stop event. (Whether the verdict has crossed the failure threshold
    # by the time anyone asks is a matter of how long the readiness
    # checks themselves took, so the threshold semantics are pinned
    # deterministically in the two tests below rather than raced for
    # here.)
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()) as client:
        assert client.get("/healthz").status_code == 200
        verdicts = client.get("/readyz").json()
        assert verdicts["database"] == "error"  # the outage, named honestly
        projector = client.app.state.projector  # type: ignore[attr-defined]
        assert projector.thread.is_alive()  # retrying, not dead
        assert projector.status.progress().consecutive_failures >= 1
    assert not projector.thread.is_alive()  # the stop event ended the backoff promptly


def test_a_projector_that_keeps_failing_is_unready(monkeypatch: pytest.MonkeyPatch) -> None:
    # The regression this whole change exists for. The thread is alive
    # and will stay alive - that is the retry loop working - and it has
    # not got through once. Readiness must say so.
    projector = _stub_projector()
    monkeypatch.setattr(app_module, "start_projector_thread", lambda client, engine: projector)
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()) as client:
        for _ in range(health.FAILURE_THRESHOLD):
            projector.status.record_failure(MorphologError("hidden sessions"))
        response = client.get("/readyz")
        assert projector.thread.is_alive()  # alive, and getting nowhere

    assert response.json()["projector"] == "error"
    assert response.status_code == 503


def test_a_recovered_projector_is_ready_again(monkeypatch: pytest.MonkeyPatch) -> None:
    # The counter resets on the first successful poll, so a demo that
    # rode out a long database restart stops reporting itself unready
    # without needing a redeploy.
    projector = _stub_projector()
    monkeypatch.setattr(app_module, "start_projector_thread", lambda client, engine: projector)
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()) as client:
        for _ in range(health.FAILURE_THRESHOLD + 5):
            projector.status.record_failure(MorphologError("hidden sessions"))
        assert client.get("/readyz").json()["projector"] == "error"
        projector.status.record_poll(0)  # a poll that found nothing is still progress
        assert client.get("/readyz").json()["projector"] == "ok"


def test_a_dead_projector_is_a_loud_readiness_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If the thread ever DOES die (a semantic ProjectionError, an
    # unforeseen crash), /readyz must say so rather than let projection
    # lag silently until the next restart.
    monkeypatch.setattr(
        app_module, "start_projector_thread", lambda client, engine: _stub_projector(alive=False)
    )
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(app_module.create_app()) as client:
        verdicts = client.get("/readyz").json()
    assert verdicts["projector"] == "error"


def test_dev_readiness_carries_no_projector_verdict() -> None:
    with TestClient(app_module.create_app()) as client:
        verdicts = client.get("/readyz").json()
    assert "projector" not in verdicts  # dev has no thread to answer for
