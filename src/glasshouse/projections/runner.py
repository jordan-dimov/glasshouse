"""The projector's three run modes, all thin around `catch_up`.

Inline is no machinery at all: whoever owns a write flow calls
`catch_up` after it (the CLI's `--project` flag on the import commands
is the worked example). The background thread suits a single-process
deployment (compose up and you are running); the separate worker is the
`glasshouse project --follow` loop, split out when a real deployment
wants the governed core's process boring.
"""

from __future__ import annotations

import threading

import sqlalchemy as sa

from glasshouse.commit import GlasshouseClient, MorphologError
from glasshouse.logging import get_logger
from glasshouse.projections.projector import catch_up

log = get_logger("glasshouse.projector")

# Operational-failure backoff for the background thread: doubling from
# the poll interval, capped, retried indefinitely - a database restart
# or a nightly reset must never permanently kill projection.
MAX_BACKOFF_SECONDS = 30.0


def start_projector_thread(
    client: GlasshouseClient,
    engine: sa.Engine,
    *,
    interval_seconds: float = 1.0,
    stop: threading.Event | None = None,
) -> tuple[threading.Thread, threading.Event]:
    """The background-thread mode: a daemon looping `catch_up` until the
    returned event is set.

    Operational failures (a dead or restarting database, a binary that
    cannot answer - `SQLAlchemyError`, `MorphologError`) are retried with
    bounded exponential backoff: they are conditions that pass, and a
    hosted single-process deployment has no supervisor to restart the
    thread. Anything else - above all `ProjectionError`, which means the
    fold or the cursor is wrong, not the weather - stays fatal and loud."""
    stop_event = stop or threading.Event()

    def _loop() -> None:
        failures = 0
        try:
            while not stop_event.is_set():
                try:
                    catch_up(client, engine)
                    failures = 0
                except (sa.exc.SQLAlchemyError, MorphologError) as transient:
                    failures += 1
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
    return thread, stop_event


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
