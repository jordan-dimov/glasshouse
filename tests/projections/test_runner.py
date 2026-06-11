"""The two looping run modes, with `catch_up` stubbed out: the loops'
own behaviour (stop event honoured, interrupt returns cleanly) is what
needs proving here; what `catch_up` does is proven against the real
ledger in the integration leg."""

import time

import pytest
import sqlalchemy as sa

from glasshouse.projections import runner

# Lazy by design: create_engine never connects until used, and these
# tests stub the only function that would use it.
ENGINE = sa.create_engine("postgresql+psycopg://unused/unused")


def test_the_thread_mode_loops_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(runner, "catch_up", lambda engine: calls.append(1))
    thread, stop = runner.start_projector_thread(ENGINE, interval_seconds=0.001)
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.001)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(calls) >= 3


def test_the_worker_mode_returns_cleanly_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def interrupted(engine: sa.Engine) -> int:
        calls.append(1)
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "catch_up", interrupted)
    runner.follow(ENGINE, interval_seconds=0)  # returns instead of raising
    assert calls == [1]
