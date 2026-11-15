"""Instructions: intentions to buy or sell that have not happened yet.

An order sent to market and not yet filled is an instruction. Until one
ends it holds room under its book's position limit, so a trade or
another instruction cannot take that room (the rule file's
`hour_has_room_for_instructions`). It ends by being cancelled, or by
its lapse being recorded once `valid_until` has passed. This module is the read side: what
was instructed, what each instruction still holds, and how each ended.

Instructions are read from governed state through the commit client,
like the limits they hold room under. They are not a projection yet.

A book can require approval. An instruction there is approved by a
second person, who must hold the right and must not be whoever placed
it; both conditions are rules in the ledger. The approval is recorded.
It is not enforced on the market: the ledger cannot stop an order
leaving the building, so a client reads `approval` before it sends.

When the market reports a fill, the reconciling service records it
(`api/fills.py`, `record_fill`): the trade is booked and what the
instruction holds goes down, ending it as filled when nothing is left.
"""

from __future__ import annotations

import datetime as dt
from decimal import InvalidOperation

from glasshouse.api.schemas import InstructionRow
from glasshouse.commit import GlasshouseClient, models
from glasshouse.imports.trades import EXACT_DECIMALS, exact_decimal

# How the ledger spells two answers the API spells in words.
_APPROVAL = {"approved": "approved", "awaiting": "awaiting", "not_required": "not required"}
_NOBODY = "nobody"


def list_instructions(
    client: GlasshouseClient,
    *,
    org: str,
    book: str | None = None,
    open_only: bool = False,
    now: dt.datetime | None = None,
) -> list[InstructionRow]:
    """Every instruction of the organisation, from ONE read: the ledger's
    own `InstructionStanding`, which works out what each holds, how it
    ended and whether it is approved, so the facts behind it agree.

    `status` is `open` while the instruction is valid and holds room;
    `overdue` once its `valid_until` has passed but nobody has recorded
    the lapse or cancelled it, in which case it STILL holds its room
    (the ledger's limit rule reads no clock, so that a trade can be
    previewed); otherwise how it ended. Open or overdue is the one thing
    worked out here, because it is the reader's clock. `holding_mw` is
    what counts against the limit right now: the remaining quantity
    while the instruction has not ended, nothing after. `open_only`
    keeps the instructions holding room, the overdue ones included."""
    at = now or dt.datetime.now(dt.UTC)
    where = {"org": org} if book is None else {"org": org, "book": book}
    standings = [
        models.InstructionStandingClaim.from_named(row.args)
        for row in client.derived_named("InstructionStanding", where=where)
    ]
    listed = []
    for standing in standings:
        still_open = standing.outcome == "open"
        if open_only and not still_open:
            continue
        listed.append(
            InstructionRow(
                org=standing.org,
                instruction=standing.instruction,
                book=standing.book,
                market=standing.market,
                direction=standing.direction,
                quantity=standing.quantity,
                holding_mw=standing.holding,
                limit_price=standing.limit_price,
                currency=standing.currency,
                delivery_start=standing.delivery_start,
                delivery_end=standing.delivery_end,
                placed_on=standing.placed_on,
                valid_until=standing.valid_until,
                proposer=standing.proposer,
                status=(
                    ("open" if at <= standing.valid_until else "overdue")
                    if still_open
                    else standing.outcome
                ),
                approval=_APPROVAL[standing.approval],
                approved_by=None if standing.approved_by == _NOBODY else standing.approved_by,
            )
        )
    return sorted(listed, key=lambda row: (row.book, row.instruction))


def place_request(
    fields: dict[str, str], *, org: str, placed_on: dt.date
) -> models.PlaceInstructionRequest:
    """Text fields to one typed proposal, or a ValueError naming what
    could not be honestly converted: the instruction's twin of the trade
    import's `capture_request`, with the same refusals for the same
    mistakes. `placed_on` is the caller's to supply because it is not
    the instructing party's to choose: it is the day the instruction is
    placed."""
    try:
        quantity = exact_decimal(fields["quantity"])
        limit_price = exact_decimal(fields["limit_price"])
    except InvalidOperation:
        raise ValueError(
            f"quantity/limit_price must be {EXACT_DECIMALS}, got "
            f"{fields['quantity']!r}/{fields['limit_price']!r}"
        ) from None
    try:
        delivery_start = dt.datetime.fromisoformat(fields["delivery_start"])
        delivery_end = dt.datetime.fromisoformat(fields["delivery_end"])
        valid_until = dt.datetime.fromisoformat(fields["valid_until"])
    except ValueError:
        raise ValueError(
            "delivery_start/delivery_end/valid_until must be RFC 3339 instants, got "
            f"{fields['delivery_start']!r}/{fields['delivery_end']!r}/{fields['valid_until']!r}"
        ) from None
    request = models.PlaceInstructionRequest(
        org=org,
        book=fields["book"],
        instruction=fields["instruction"],
        market=fields["market"],
        direction=fields["direction"],
        quantity=quantity,
        limit_price=limit_price,
        delivery_start=delivery_start,
        delivery_end=delivery_end,
        placed_on=placed_on,
        valid_until=valid_until,
    )
    try:
        request.to_args_named()  # the codec is the final validator (naive instants, ...)
    except ValueError:
        raise ValueError(
            "delivery_start/delivery_end/valid_until must carry an offset from UTC, like "
            "2026-07-01T12:00:00+00:00"
        ) from None
    return request
