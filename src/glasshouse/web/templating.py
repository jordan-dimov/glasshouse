"""The one Jinja2 environment for the Control Room templates.

Templates resolve from this package's directory (never the working
directory), the application version rides as a global for the footer,
and the `utc` filter renders every instant the same way: date, minute
precision, an explicit Z. Delivery periods are UTC instants (law 9), so
the rendering converts and says so rather than trusting the row was
already aware of it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fastapi.templating import Jinja2Templates

from glasshouse import __version__


def _utc(instant: dt.datetime) -> str:
    return instant.astimezone(dt.UTC).strftime("%Y-%m-%d %H:%M") + "Z"


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["version"] = __version__
templates.env.filters["utc"] = _utc
