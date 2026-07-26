"""The projector's three run modes, all thin around `catch_up`.

Inline is no machinery at all: whoever owns a write flow calls
`catch_up` after it (the CLI's `--project` flag on the import commands
is the worked example). The background thread suits a single-process
deployment (compose up and you are running); the separate worker is the
`glasshouse project --follow` loop, split out when a real deployment
wants the governed core's process boring.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass, field, replace

import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient, MorphologError
from glasshouse.logging import get_logger
from glasshouse.projections.projector import catch_up

log = get_logger("glasshouse.projector")

# Operational-failure backoff for the background thread: doubling from
# the poll interval, capped, retried indefinitely - a database restart
# or a nightly reset must never permanently kill projection.
MAX_BACKOFF_SECONDS = 30.0


@dataclass(frozen=True)
class ProjectorProgress:
    """What the projector knows about its own progress - an immutable
    snapshot, safe to read from the request thread.

    `polled_at` is the load-bearing one: it advances on every successful
    cycle, INCLUDING the cycles that find nothing to apply. A cursor
    advances only when the ledger moves, so cursor age alone cannot tell
    an idle projector from a stuck one - which is exactly the confusion
    that let a thread failing every poll be reported as healthy."""

    polled_at: dt.datetime | None = None
    applied_at: dt.datetime | None = None
    applied_total: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None


@dataclass
class ProjectorStatus:
    """The live counterpart the loop writes to, under a lock so a reader
    never sees half an update."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _progress: ProjectorProgress = field(default_factory=ProjectorProgress)

    def record_poll(self, applied: int, *, now: dt.datetime | None = None) -> None:
        stamp = now or dt.datetime.now(dt.UTC)
        with self._lock:
            self._progress = replace(
                self._progress,
                polled_at=stamp,
                applied_at=stamp if applied else self._progress.applied_at,
                applied_total=self._progress.applied_total + applied,
                consecutive_failures=0,
                last_error=None,
            )

    def record_failure(self, error: BaseException) -> None:
        with self._lock:
            self._progress = replace(
                self._progress,
                consecutive_failures=self._progress.consecutive_failures + 1,
                last_error=type(error).__name__,
            )

    def progress(self) -> ProjectorProgress:
        with self._lock:
            return self._progress


@dataclass(frozen=True)
class RunningProjector:
    """The background thread and everything a caller needs to answer for
    it: stop it, and ask whether it is getting anywhere."""

    thread: threading.Thread
    stop: threading.Event
    status: ProjectorStatus


def start_projector_thread(
    client: GlasshouseClient,
    engine: sa.Engine,
    *,
    interval_seconds: float = 1.0,
    stop: threading.Event | None = None,
) -> RunningProjector:
    """The background-thread mode: a daemon looping `catch_up` until the
    returned event is set.

    Operational failures (a dead or restarting database, a binary that
    cannot answer - `SQLAlchemyError`, `MorphologError`) are retried with
    bounded exponential backoff: they are conditions that pass, and a
    hosted single-process deployment has no supervisor to restart the
    thread. Anything else - above all `ProjectionError`, which means the
    fold or the cursor is wrong, not the weather - stays fatal and loud.

    Retrying is why the returned `status` exists. A thread that survives
    every failure is the right behaviour and a useless health signal:
    liveness stopped being evidence of progress the moment the retry
    loop landed, and a projector that had refused twenty-one consecutive
    tails once reported itself healthy for a quarter of an hour."""
    stop_event = stop or threading.Event()
    status = ProjectorStatus()

    def _loop() -> None:
        try:
            while not stop_event.is_set():
                try:
                    status.record_poll(catch_up(client, engine))
                except (sa.exc.SQLAlchemyError, MorphologError) as transient:
                    status.record_failure(transient)
                    failures = status.progress().consecutive_failures
                    delay = min(interval_seconds * (2**failures), MAX_BACKOFF_SECONDS)
                    log.warning(
                        "projector.retrying",
                        error=type(transient).__name__,
                        detail=str(transient),
                        consecutive_failures=failures,
                        next_attempt_seconds=delay,
                    )
                    stop_event.wait(delay)
                    continue
                stop_event.wait(interval_seconds)
        except Exception:
            # A daemon thread dying silently is invisible in a hosted
            # deployment; record the failure before it propagates to the
            # thread excepthook.
            log.exception("projector.thread_failed")
            raise

    thread = threading.Thread(target=_loop, name="glasshouse-projector", daemon=True)
    thread.start()
    log.info("projector.thread_started", interval_seconds=interval_seconds)
    return RunningProjector(thread=thread, stop=stop_event, status=status)


def follow(client: GlasshouseClient, engine: sa.Engine, *, interval_seconds: float = 1.0) -> None:
    """The worker mode: poll `catch_up` until interrupted."""
    pace = threading.Event()
    log.info("projector.follow_started", interval_seconds=interval_seconds)
    try:
        while True:
            catch_up(client, engine)
            pace.wait(interval_seconds)
    except KeyboardInterrupt:
        log.info("projector.follow_stopped")
        return
    except Exception:
        log.exception("projector.follow_failed")
        raise
