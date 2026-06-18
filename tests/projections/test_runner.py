"""The two looping run modes, with `catch_up` stubbed out: the loops'
own behaviour (stop event honoured, interrupt returns cleanly) is what
needs proving here; what `catch_up` does is proven against the real
ledger in the integration leg."""

import threading
import time

import pytest
import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient
from glasshouse.projections import runner

# Lazy by design: create_engine never connects until used, the client
# never spawns until invoked, and these tests stub the only function
# that would use either.
ENGINE = sa.create_engine("postgresql+psycopg://unused/unused")
CLIENT = GlasshouseClient("unused.morph", "postgres:///unused", binary="/nonexistent")


def test_the_thread_mode_loops_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(runner, "catch_up", lambda client, engine: calls.append(1))
    thread, stop = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.001)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(calls) >= 3


def test_the_worker_mode_loops_then_returns_cleanly_on_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def interrupted(client: object, engine: sa.Engine) -> int:
        calls.append(1)
        if len(calls) >= 2:  # loop once (through the pace wait), then stop
            raise KeyboardInterrupt
        return 0

    monkeypatch.setattr(runner, "catch_up", interrupted)
    runner.follow(CLIENT, ENGINE, interval_seconds=0)  # returns instead of raising
    assert calls == [1, 1]


def test_the_worker_mode_reraises_an_unexpected_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(client: object, engine: sa.Engine) -> int:
        raise RuntimeError("ledger unreachable")

    monkeypatch.setattr(runner, "catch_up", boom)
    # An interrupt is a clean stop; any other failure propagates (after a
    # structured event), it is not swallowed by the loop.
    with pytest.raises(RuntimeError, match="ledger unreachable"):
        runner.follow(CLIENT, ENGINE, interval_seconds=0)


def test_the_thread_mode_dies_on_an_unexpected_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(client: object, engine: sa.Engine) -> int:
        raise RuntimeError("ledger unreachable")

    monkeypatch.setattr(runner, "catch_up", boom)
    # The daemon thread re-raises into the thread excepthook rather than
    # spinning silently; capture it so the failure is observable.
    raised: list[type[BaseException] | None] = []
    monkeypatch.setattr(threading, "excepthook", lambda args: raised.append(args.exc_type))
    thread, stop = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    thread.join(timeout=5)
    stop.set()
    assert not thread.is_alive()
    assert raised == [RuntimeError]
