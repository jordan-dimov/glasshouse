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
import hashlib
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


# What each rule's refusal means to someone who has never read the rule
# file. The rule's own name and figures are always shown beside it: this
# is a gloss, never a replacement, and a rule not listed here simply has
# none.
RULES_IN_PLAIN_WORDS = {
    "hour_within_limit": "This would take the book past its position limit in that hour.",
    "room_not_held_by_instructions": (
        "There would be room for this, but open instructions are holding it in that hour."
    ),
    "instruction_fits_under_limit": (
        "With the trades booked and the instructions already open, this does not fit under "
        "the book's position limit in that hour."
    ),
    "may_instruct": "This name is not allowed to place or cancel instructions in that book.",
    "may_approve": "This name is not allowed to approve instructions in that book.",
    "approver_is_not_proposer": (
        "The person who placed an instruction cannot approve or reject it themselves."
    ),
    "instruction_not_yet_approved": "This instruction has already been approved.",
    "approval_not_already_required": "That book already requires approval.",
    "instruction_is_new": "An instruction with this id already exists.",
    "instruction_is_still_valid": "The instruction's valid-until time has already passed.",
    "instruction_has_lapsed": "The instruction is still valid, so it has not lapsed.",
    "may_capture_trade": "This name is not allowed to enter trades in that book.",
    "may_amend_trade": "This name is not allowed to change trades in that book.",
    "may_register_curve": "This name is not allowed to enter prices for that market.",
    "market_has_a_currency": "That market has not been set up yet.",
    "trade_is_new": "A trade with this id is already in the book.",
    "terms_version_is_new": "A trade with this id is already in the book.",
    "curve_version_is_new": "A curve version with this name already exists.",
    "no_official_curve_yet": "That market already has prices for that day. Correct them instead.",
    "delivery_is_forward": "Delivery must end after it starts.",
    "delivery_is_whole_hours": "Delivery must start and end on the hour.",
    "delivery_is_at_most_a_year": "Delivery can last at most a year.",
}

OUTCOMES_IN_PLAIN_WORDS = {
    "admissible": "would be accepted",
    "refused": "would be refused",
    "committed": "recorded",
    "rejected": "refused",
    "quarantined": "could not be read",
    "errored": "failed",
}


def _plain_rule(detail: str) -> str:
    """The plain-words gloss for a refusal line that leads with a rule's
    name (`rule: what it compared`), or nothing."""
    return RULES_IN_PLAIN_WORDS.get(detail.partition(":")[0].strip(), "")


def _viewing(query: QueryParams) -> dict[str, object] | None:
    """The as-of mode a request is in, for the top bar: None for the
    present (or a value no screen could read; the screens that answer for
    the past refuse that themselves). Otherwise the instant in its one
    canonical UTC spelling, however the caller wrote it (`query`, for
    links and hidden fields), as shown (`shown`, to the second; `exact`
    in full), and to the second for
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
        # To the second on screen; the exact instant is one hover away
        # and is what the links carry.
        "shown": asof.canonical(at.replace(microsecond=0)).replace("T", " ") + " UTC",
        "exact": asof.canonical(at).replace("T", " ") + " UTC",
        "picker": asof.canonical(at.replace(microsecond=0)),
        "carried": carried,
    }


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["version"] = __version__
# A stylesheet's address changes when any stylesheet does, so a browser
# never shows a new page with a stylesheet it cached from the last
# release.
templates.env.globals["static_version"] = hashlib.sha256(
    b"".join(
        sheet.read_bytes() for sheet in sorted((Path(__file__).parent / "static/css").glob("*.css"))
    )
).hexdigest()[:10]
templates.env.filters["utc"] = _utc
templates.env.filters["ago"] = _ago
templates.env.globals["viewing"] = _viewing
templates.env.filters["plain_rule"] = _plain_rule
templates.env.globals["outcome_words"] = OUTCOMES_IN_PLAIN_WORDS
