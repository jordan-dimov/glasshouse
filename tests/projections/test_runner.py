"""The two looping run modes, with `catch_up` stubbed out: the loops'
own behaviour (stop event honoured, interrupt returns cleanly) is what
needs proving here; what `catch_up` does is proven against the real
ledger in the integration leg."""

import threading
import time

import pytest
import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient, MorphologError
from glasshouse.projections import runner

# Lazy by design: create_engine never connects until used, the client
# never spawns until invoked, and these tests stub the only function
# that would use either.
ENGINE = sa.create_engine("postgresql+psycopg://unused/unused")
CLIENT = GlasshouseClient("unused.morph", "postgres:///unused", binary="/nonexistent")


def test_the_thread_mode_loops_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def counted(client: object, engine: sa.Engine) -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr(runner, "catch_up", counted)
    projector = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    thread, stop = projector.thread, projector.stop
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.001)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(calls) >= 3


def test_operational_failures_are_retried_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A dead or restarting database (or a binary that cannot answer) is
    # weather: the thread backs off, retries, and recovers - it must
    # never die on the nightly reset's window or a managed-Postgres
    # restart.
    outcomes: list[str] = []

    def flaky(client: object, engine: sa.Engine) -> int:
        if len(outcomes) < 2:
            outcomes.append("fail")
            raise sa.exc.OperationalError("select 1", None, Exception("connection refused"))
        outcomes.append("ok")
        return 0

    monkeypatch.setattr(runner, "catch_up", flaky)
    projector = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    thread, stop = projector.thread, projector.stop
    deadline = time.monotonic() + 5
    while "ok" not in outcomes and time.monotonic() < deadline:
        time.sleep(0.001)
    assert thread.is_alive()  # two failures did not kill it
    assert outcomes[:3] == ["fail", "fail", "ok"]  # ...and it recovered
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()


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
    projector = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    thread, stop = projector.thread, projector.stop
    thread.join(timeout=5)
    stop.set()
    assert not thread.is_alive()
    assert raised == [RuntimeError]


def test_progress_distinguishes_an_idle_projector_from_a_stuck_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole point of the status: a poll that found nothing to apply
    # is still progress. A cursor would not move here, and neither would
    # `applied_at` - but `polled_at` does, which is what makes "idle" and
    # "stuck" distinguishable at all.
    monkeypatch.setattr(runner, "catch_up", lambda client, engine: 0)
    projector = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    deadline = time.monotonic() + 5
    while projector.status.progress().polled_at is None and time.monotonic() < deadline:
        time.sleep(0.001)
    projector.stop.set()
    projector.thread.join(timeout=5)

    progress = projector.status.progress()
    assert progress.polled_at is not None  # polling
    assert progress.applied_at is None  # but nothing to apply
    assert progress.applied_total == 0
    assert progress.consecutive_failures == 0


def test_progress_counts_failures_and_forgets_them_on_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The counter the readiness verdict reads: it must rise while the
    # condition lasts and reset the moment a poll succeeds, so a
    # recovered projector stops reporting itself unready.
    outcomes: list[str] = []

    def flaky(client: object, engine: sa.Engine) -> int:
        if len(outcomes) < 2:
            outcomes.append("fail")
            raise MorphologError("inspect audit failed: hidden sessions")
        outcomes.append("ok")
        return 3

    monkeypatch.setattr(runner, "catch_up", flaky)
    projector = runner.start_projector_thread(CLIENT, ENGINE, interval_seconds=0.001)
    deadline = time.monotonic() + 5
    while "ok" not in outcomes and time.monotonic() < deadline:
        time.sleep(0.001)
    projector.stop.set()
    projector.thread.join(timeout=5)

    progress = projector.status.progress()
    assert progress.consecutive_failures == 0  # forgotten on recovery
    assert progress.last_error is None
    assert progress.applied_total == 3
    assert progress.applied_at is not None


def test_a_status_never_recovers_on_its_own_while_the_condition_lasts() -> None:
    # Recorded directly, without a thread: the reader must see a rising
    # count, because this is exactly the state that reported itself
    # healthy for a quarter of an hour on the live demo.
    status = runner.ProjectorStatus()
    for _ in range(21):
        status.record_failure(MorphologError("hidden sessions"))
    progress = status.progress()
    assert progress.consecutive_failures == 21
    assert progress.last_error == "MorphologError"
    assert progress.polled_at is None  # it has never once got through
