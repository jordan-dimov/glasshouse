"""The read side: projections and the projector.

Every table in the app schema is one of exactly two things: a projection
(derived state carrying the transition id it came from, rebuilt by
replaying the transition log from zero) or a hash-anchored payload (bulk
content whose hash was admitted in a governed claim). That law is what
makes ``glasshouse verify`` possible.

The projector is a library with three run modes chosen by the caller:
inline after each write (`catch_up`), background thread
(`start_projector_thread`), or separate worker (`follow`, surfaced as
`glasshouse project --follow`). It tails the transition log, not the
outbox.
"""

from glasshouse.projections.projector import ProjectionError, catch_up, fold_transition, rebuild
from glasshouse.projections.runner import follow, start_projector_thread
from glasshouse.projections.tables import (
    blotter_trade,
    position_hour,
    projection_progress,
    trade_valuation,
)

__all__ = [
    "ProjectionError",
    "blotter_trade",
    "catch_up",
    "fold_transition",
    "follow",
    "position_hour",
    "projection_progress",
    "rebuild",
    "start_projector_thread",
    "trade_valuation",
]
