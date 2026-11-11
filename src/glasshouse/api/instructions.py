"""Instructions: intentions to buy or sell that have not happened yet.

An order sent to market and not yet filled is an instruction. Until one
ends it holds room under its book's position limit, so a trade or
another instruction cannot take that room (the rule file's
`hour_has_room_for_instructions`). It ends by being cancelled, or by
its lapse being recorded once `valid_until` has passed. This module is the read side: what
was instructed, what each instruction still holds, and how each ended.

Instructions are read from governed state through the commit client,
like the limits they hold room under. They are not a projection yet.

What an instruction is not, yet: it is not approved by anyone, and it is
not connected to fills. Whether it may be sent, and what happens when
the market reports a fill, are rules still to come.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from glasshouse.api.schemas import InstructionRow
from glasshouse.commit import GlasshouseClient, models

_PREDICATES = ("Instruction", "InstructionOpen", "InstructionEnded", "MarketCurrency")


def list_instructions(
    client: GlasshouseClient,
    *,
    org: str,
    book: str | None = None,
    open_only: bool = False,
    now: dt.datetime | None = None,
) -> list[InstructionRow]:
    """Every instruction of the organisation, newest validity first
    within a book, from ONE read of the ledger so the four facts agree.

    `status` is `open` while the instruction is valid and holds room;
    `overdue` once its `valid_until` has passed but nobody has recorded
    the lapse or cancelled it, in which case it STILL holds its room
    (the ledger's limit rule reads no clock, so that a trade can be
    previewed); otherwise how it ended. `holding_mw` is what counts
    against the limit right now: the remaining quantity while the
    instruction has not ended, nothing after. `open_only` keeps the
    instructions holding room, the overdue ones included."""
    at = now or dt.datetime.now(dt.UTC)
    rows = client.claims_named(*_PREDICATES)

    def of(predicate: str) -> list[dict[str, object]]:
        return [row.args for row in rows if row.predicate == predicate]

    remaining = {
        claim.instruction: claim.remaining
        for claim in map(models.InstructionOpenClaim.from_named, of("InstructionOpen"))
        if claim.org == org
    }
    ended = {
        claim.instruction: claim.outcome
        for claim in map(models.InstructionEndedClaim.from_named, of("InstructionEnded"))
        if claim.org == org
    }
    currencies = {
        (claim.org, claim.market): claim.currency
        for claim in map(models.MarketCurrencyClaim.from_named, of("MarketCurrency"))
    }
    listed = []
    for placed in map(models.InstructionClaim.from_named, of("Instruction")):
        if placed.org != org or (book is not None and placed.book != book):
            continue
        left = remaining.get(placed.instruction)
        if left is None:
            # No longer open: how it ended. An instruction always ends
            # by an act that records the outcome in the same step.
            status = ended.get(placed.instruction, "ended")
        else:
            status = "open" if at <= placed.valid_until else "overdue"
        if open_only and left is None:
            continue
        listed.append(
            InstructionRow(
                org=placed.org,
                instruction=placed.instruction,
                book=placed.book,
                market=placed.market,
                direction=placed.direction,
                quantity=placed.quantity,
                holding_mw=left if left is not None else Decimal(0),
                limit_price=placed.limit_price,
                currency=currencies[placed.org, placed.market],
                delivery_start=placed.delivery_start,
                delivery_end=placed.delivery_end,
                placed_on=placed.placed_on,
                valid_until=placed.valid_until,
                proposer=placed.proposer,
                status=status,
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
        quantity = Decimal(fields["quantity"])
        limit_price = Decimal(fields["limit_price"])
    except InvalidOperation:
        raise ValueError(
            "quantity/limit_price must be exact decimals, got "
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
