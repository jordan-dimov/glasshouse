"""Fills the market reports, and the breaches they bring.

A fill has happened, so the ledger books it even when it breaks a rule,
and records the breach in the same act (`record_fill`). The kinds of
breach, and what may answer one, are the ledger's: this module turns
text into the typed proposals and reads back what is unanswered.
Recording a fill is the reconciling service's work, through the CLI; no
agent tool records one.
"""

from __future__ import annotations

import datetime as dt

from glasshouse.api.schemas import BreachRow
from glasshouse.commit import GlasshouseClient, exact_decimals, models
from glasshouse.compute.terms import terms_version_id

#: What `answer_breach` accepts as a decision, in the ledger's own list
#: (`breach_decision_is_one_of_four`); the CLI offers these.
DECISIONS = ("accepted", "unwind", "cap_raised", "disputed")


def fill_request(
    *,
    org: str,
    instruction: str,
    execution: str,
    trade: str,
    counterparty: str,
    quantity: str,
    price: str,
    trade_date: str,
) -> models.RecordFillRequest:
    """Text fields to one typed proposal, or a ValueError naming what
    could not be honestly converted. The trade's first terms version
    takes the app's id convention, as a capture's does."""
    numbers = exact_decimals(quantity=quantity, price=price)
    try:
        on = dt.date.fromisoformat(trade_date)
    except ValueError:
        raise ValueError(f"trade_date must be an ISO date, got {trade_date!r}") from None
    return models.RecordFillRequest(
        org=org,
        instruction=instruction,
        execution=execution,
        trade=trade,
        counterparty=counterparty,
        version=terms_version_id(trade, 1),
        quantity=numbers["quantity"],
        price=numbers["price"],
        trade_date=on,
    )


def list_breaches(
    client: GlasshouseClient, *, org: str, book: str | None = None
) -> list[BreachRow]:
    """The organisation's unanswered breaches, in one book or all, from
    the ledger's own derived read: which are unanswered is the ledger's
    to say."""
    where = {"org": org} if book is None else {"org": org, "book": book}
    rows = client.derived_named("UnansweredBreach", where=where)
    breaches = [models.UnansweredBreachClaim.from_named(row.args) for row in rows]
    return sorted(
        (
            BreachRow(org=b.org, book=b.book, trade=b.trade, kind=b.kind, execution=b.execution)
            for b in breaches
        ),
        key=lambda row: (row.book, row.trade, row.kind),
    )


def breaches_of(client: GlasshouseClient, *, org: str, trade: str) -> set[str]:
    """The kinds of breach recorded with one trade's fill, read by name."""
    rows = client.claims_named("Breach", where={"org": org, "trade": trade})
    return {models.BreachClaim.from_named(row.args).kind for row in rows}


def recorded_as(client: GlasshouseClient, *, org: str, execution: str) -> str | None:
    """The trade an outside execution was booked as, or `None` if it has
    not been recorded. What a reconciler asks after a timeout or a crash,
    before it tries again: one execution is booked once (the ledger holds
    it unique), so a repeat is an answer, not a fresh failure."""
    rows = client.claims_named("InstructionFill", where={"org": org, "execution": execution})
    fills = [models.InstructionFillClaim.from_named(row.args) for row in rows]
    return fills[0].trade if fills else None


def repeat_conflicts(
    client: GlasshouseClient, request: models.RecordFillRequest, trade: str
) -> list[str]:
    """How a repeated report of an execution differs from what was booked
    for it: empty when it is the same report seen again, otherwise the
    fields that disagree, which is a reconciliation conflict to look at,
    never a second booking."""
    (fill,) = [
        models.InstructionFillClaim.from_named(row.args)
        for row in client.claims_named(
            "InstructionFill", where={"org": request.org, "trade": trade}
        )
    ]
    (terms,) = [
        models.TradeTermsClaim.from_named(row.args)
        for row in client.claims_named(
            "TradeTerms", where={"org": request.org, "version": terms_version_id(trade, 1)}
        )
    ]
    differences = []
    if fill.instruction != request.instruction:
        differences.append(f"instruction {fill.instruction} booked, {request.instruction} now")
    if terms.quantity != request.quantity:
        differences.append(f"quantity {terms.quantity} booked, {request.quantity} now")
    if terms.price != request.price:
        differences.append(f"price {terms.price} booked, {request.price} now")
    return differences
