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

from glasshouse.projections.projector import catch_up


def start_projector_thread(
    engine: sa.Engine, *, interval_seconds: float = 1.0, stop: threading.Event | None = None
) -> tuple[threading.Thread, threading.Event]:
    """The background-thread mode: a daemon looping `catch_up` until the
    returned event is set."""
    stop_event = stop or threading.Event()

    def _loop() -> None:
        while not stop_event.is_set():
            catch_up(engine)
            stop_event.wait(interval_seconds)

    thread = threading.Thread(target=_loop, name="glasshouse-projector", daemon=True)
    thread.start()
    return thread, stop_event


def follow(engine: sa.Engine, *, interval_seconds: float = 1.0) -> None:
    """The worker mode: poll `catch_up` until interrupted."""
    pace = threading.Event()
    try:
        while True:
            catch_up(engine)
            pace.wait(interval_seconds)
    except KeyboardInterrupt:
        return
