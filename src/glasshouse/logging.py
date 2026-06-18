"""Operational logging, deliberately distinct from the governed audit.

The audit log in the ledger is the legitimacy-grade record of what was
proposed and admitted; it is the product. This is the *operational*
record of the process doing the proposing: imports run, projector pages
applied, the API starting and stopping, a binary that timed out. The two
never substitute for each other, and nothing here is a write path to
governed state.

structlog renders to a readable console in local development and to JSON
lines in hosted deployments (one event per line, aggregator-ready),
keyed off `settings.environment` so a deployment changes how logs look
without a code change. Configuration is process-global and idempotent (structlog's
own model): the CLI's `main` and the API's lifespan each call
`configure_logging` once at startup; everything else takes a bound logger
through `get_logger` and logs key-value events, never formatted strings.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from glasshouse.config import Settings


def _stderr_logger(*_args: object) -> structlog.PrintLogger:
    # Diagnostics to stderr: stdout is the CLI's data channel (the import
    # report, the projector count), and logs must never bleed into it.
    # sys.stderr is resolved here, at log time, not captured once at
    # configuration: under pytest's per-test capture the stream is
    # swapped out, so a handle bound once would later be a closed file.
    return structlog.PrintLogger(sys.stderr)


def configure_logging(settings: Settings) -> None:
    """Wire structlog once for the running process. Idempotent: a second
    call (a second `create_app` in tests, say) simply re-applies the same
    configuration."""
    # Hosted environments (demo and production on Render) render JSON lines
    # for the log aggregator; local development gets the readable console.
    hosted = settings.environment in ("demo", "production")
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if hosted else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=_stderr_logger,
        # Resolve the stream per call (see `_stderr_logger`); caching
        # would pin the first call's stderr for the process's life.
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    """A bound logger under `name` (a dotted module-style channel, e.g.
    `glasshouse.projector`). Works before `configure_logging`; structlog
    binds lazily on first use."""
    # structlog.get_logger returns a lazy proxy typed as Any; our wrapper
    # class is the filtering bound logger, so name the contract callers get.
    return cast(structlog.typing.FilteringBoundLogger, structlog.get_logger(name))
