"""The one Jinja2 environment for the Control Room templates.

Templates resolve from this package's directory (never the working
directory), the application version rides as a global for the footer,
the `utc` filter renders every instant the same way (date, minute
precision, an explicit Z), `ago` renders an operational age in
words, and `viewing` reads the as-of instant a request carries, once,
for the top bar every screen shares. Delivery periods are UTC instants (law 9), so the rendering
converts and says so rather than trusting the row was already aware of
it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.datastructures import QueryParams

from glasshouse import __version__
from glasshouse.api import asof


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


def _viewing(query: QueryParams) -> dict[str, object] | None:
    """The as-of mode a request is in, for the top bar: None for the
    present (or a value no screen could read; the screens that answer for
    the past refuse that themselves). Otherwise the instant in its one
    canonical UTC spelling, however the caller wrote it (`query`, for
    links and hidden fields), as shown (`shown`), and to the second for
    the picker, which can hold no finer (`picker`). `carried` is every
    other filter of the screen, so a change of moment keeps the book and
    window being looked at; the page offset is dropped, since the rows at
    another moment are other rows."""
    try:
        at = asof.parse_instant(query.get("as_of"))
    except ValueError:
        at = None
    carried = [
        (key, value) for key, value in query.multi_items() if key not in {"org", "as_of", "offset"}
    ]
    if at is None:
        return {"at": None, "carried": carried}
    return {
        "at": at,
        "query": asof.canonical(at),
        "shown": asof.canonical(at).replace("T", " ") + " UTC",
        "picker": asof.canonical(at.replace(microsecond=0)),
        "carried": carried,
    }


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["version"] = __version__
templates.env.filters["utc"] = _utc
templates.env.filters["ago"] = _ago
templates.env.globals["viewing"] = _viewing
