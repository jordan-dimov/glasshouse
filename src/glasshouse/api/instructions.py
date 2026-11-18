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
from decimal import Decimal

from glasshouse.api.schemas import InstructionRow
from glasshouse.commit import GlasshouseClient, exact_decimals, models


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
    the lapse or cancelled it, and from that instant it holds nothing
    (the ledger's room rule reads the act's decision time); otherwise how
    it ended. Open or overdue is the one thing worked out here, because
    it is the reader's clock. `holding_mw` is what counts against the
    limit at `now`: the remaining quantity while the instruction is
    open, nothing once it is overdue or has ended. `open_only` keeps the
    instructions holding room at `now`."""
    at = now or dt.datetime.now(dt.UTC)
    where = {"org": org} if book is None else {"org": org, "book": book}
    standings = [
        models.InstructionStandingClaim.from_named(row.args)
        for row in client.derived_named("InstructionStanding", where=where)
    ]
    listed = []
    for standing in standings:
        status = (
            ("open" if at <= standing.valid_until else "overdue")
            if standing.outcome == "open"
            else standing.outcome
        )
        if open_only and status != "open":
            continue
        listed.append(
            InstructionRow(
                org=standing.org,
                instruction=standing.instruction,
                book=standing.book,
                market=standing.market,
                direction=standing.direction,
                quantity=standing.quantity,
                holding_mw=standing.holding if status == "open" else Decimal(0),
                limit_price=standing.limit_price,
                currency=standing.currency,
                delivery_start=standing.delivery_start,
                delivery_end=standing.delivery_end,
                placed_on=standing.placed_on,
                valid_until=standing.valid_until,
                proposer=standing.proposer,
                status=status,
                # The ledger spells `not_required` and `nobody`; the API
                # says `not required` and leaves the approver out.
                approval=standing.approval.replace("_", " "),
                approved_by=None if standing.approved_by == "nobody" else standing.approved_by,
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
    numbers = exact_decimals(quantity=fields["quantity"], limit_price=fields["limit_price"])
    quantity, limit_price = numbers["quantity"], numbers["limit_price"]
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
