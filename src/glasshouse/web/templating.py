"""The one Jinja2 environment for the Control Room templates.

Templates resolve from this package's directory (never the working
directory), the application version rides as a global for the footer,
the `utc` filter renders every instant the same way (date, minute
precision, an explicit Z), and `ago` renders an operational age in
words. Delivery periods are UTC instants (law 9), so the rendering
converts and says so rather than trusting the row was already aware of
it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi.templating import Jinja2Templates

from glasshouse import __version__


def _utc(instant: dt.datetime) -> str:
    return instant.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M") + "Z"


def _ago(instant: dt.datetime | None, *, now: dt.datetime | None = None) -> str:
    """How long ago, in the coarsest honest unit. Operational wall-clock
    only - never a delivery period, whose instants are exact by law."""
    if instant is None:
        return "never"
    seconds = ((now or dt.datetime.now(dt.UTC)) - instant.astimezone(dt.UTC)).total_seconds()
    if seconds < 0:
        # A clock that disagrees with the database is worth showing as
        # itself rather than rendering a negative age as "just now".
        return "ahead of this clock"
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds // 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["version"] = __version__
templates.env.filters["utc"] = _utc
templates.env.filters["ago"] = _ago
