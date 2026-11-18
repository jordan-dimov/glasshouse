"""Recording lapses on a schedule.

An instruction past its `valid_until` stops holding room under the limit
at that instant, by the ledger's own room rule (it reads the act's
decision time; since Morpholog v0.0.20 a preview can be judged at one).
Until its lapse is recorded it is still open on the record, so how often
the sweep runs decides how soon the record says so: bookkeeping, not
correctness.

The sweep asks the ledger which books hold an overdue instruction and
proposes `lapse_overdue_instructions` for those books only, one act per
book. A blind sweep over every book would be refused by
`something_has_lapsed` for every book with nothing overdue, and fill the
refusal log. The actor is the reconciling service, which the ledger lets
lapse instructions in any book of an organisation where it may record
fills; it has no other say here.
"""

from __future__ import annotations

import datetime as dt
import threading
from dataclasses import dataclass

from glasshouse.commit import Committed, GlasshouseClient, MorphologError, Rejected, models
from glasshouse.logging import get_logger

log = get_logger("glasshouse.sweep")


def overdue_books(client: GlasshouseClient, *, now: dt.datetime) -> list[tuple[str, str]]:
    """Every (organisation, book) holding an open instruction whose
    `valid_until` is before `now`, in a stable order."""
    open_claims = map(
        models.InstructionOpenClaim.from_named,
        (row.args for row in client.claims_named("InstructionOpen")),
    )
    return sorted({(c.org, c.book) for c in open_claims if c.valid_until < now})


def lapsed(outcome: Committed) -> int:
    """How many instructions one lapse act ended."""
    return sum(1 for c in outcome.asserted_claims if c.predicate == "InstructionEnded")


def sweep_once(
    client: GlasshouseClient, *, actor: str, now: dt.datetime | None = None
) -> dict[tuple[str, str], Committed | Rejected]:
    """Propose the lapse for each book that needs it, as `actor`. A
    refusal is an answer, not a failure: the book may have been swept by
    a desk a moment before, or the actor may hold no right in that
    organisation. Each is logged and the sweep goes on."""
    at = now or dt.datetime.now(dt.UTC)
    outcomes: dict[tuple[str, str], Committed | Rejected] = {}
    for org, book in overdue_books(client, now=at):
        request = models.LapseOverdueInstructionsRequest(org=org, book=book)
        outcome = client.submit(request, actor=actor)
        outcomes[org, book] = outcome
        if isinstance(outcome, Committed):
            log.info("sweep.lapsed", org=org, book=book, instructions=lapsed(outcome))
        else:
            log.warning("sweep.refused", org=org, book=book, rule=outcome.rule)
    return outcomes


@dataclass(frozen=True)
class RunningSweep:
    thread: threading.Thread
    stop: threading.Event


def start_sweep_thread(
    client: GlasshouseClient,
    *,
    actor: str,
    interval_seconds: float = 60.0,
    stop: threading.Event | None = None,
) -> RunningSweep:
    """The demo profile's sweep: a daemon running `sweep_once` every
    `interval_seconds` until the event is set. An operational failure
    (the database restarting for the nightly reset, say) is logged and
    tried again next time; the next minute is soon enough."""
    stop_event = stop or threading.Event()

    def _loop() -> None:
        while not stop_event.wait(interval_seconds):
            try:
                sweep_once(client, actor=actor)
            except MorphologError as transient:
                log.warning("sweep.retrying", error=type(transient).__name__, detail=str(transient))

    thread = threading.Thread(target=_loop, name="glasshouse-sweep", daemon=True)
    thread.start()
    log.info("sweep.thread_started", interval_seconds=interval_seconds, actor=actor)
    return RunningSweep(thread=thread, stop=stop_event)
