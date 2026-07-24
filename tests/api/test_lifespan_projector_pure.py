"""The demo profile's background projector in the web lifespan: started
only in the demo environment, stopped and joined on shutdown, and a
projector that dies on a dead database never takes the web service with
it (the screens keep serving, the failure is loud in the thread's
excepthook).
"""

import threading

import pytest
import sqlalchemy as sa
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


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_dying_projector_never_takes_the_web_service_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Demo environment against a dead database: the projector thread
    # fails loudly and dies; the screens keep serving and shutdown is
    # clean (joining a dead thread is immediate).
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    raised: list[type[BaseException] | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: raised.append(args.exc_type))
    with TestClient(app_module.create_app()) as client:
        assert client.get("/healthz").status_code == 200
        deadline = threading.Event()
        for _ in range(200):  # up to ~10s for the thread's first poll
            if raised:
                break
            deadline.wait(0.05)
    assert raised, "the projector's failure must reach the excepthook, never vanish"
    assert issubclass(raised[0] or Exception, sa.exc.SQLAlchemyError)
