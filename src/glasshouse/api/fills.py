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
from decimal import InvalidOperation

from glasshouse.api.schemas import BreachRow
from glasshouse.commit import GlasshouseClient, models
from glasshouse.compute.terms import terms_version_id
from glasshouse.imports.trades import EXACT_DECIMALS, exact_decimal

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
    try:
        filled = exact_decimal(quantity)
        at_price = exact_decimal(price)
    except InvalidOperation:
        raise ValueError(
            f"quantity/price must be {EXACT_DECIMALS}, got {quantity!r}/{price!r}"
        ) from None
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
        quantity=filled,
        price=at_price,
        trade_date=on,
    )


def list_breaches(client: GlasshouseClient, *, org: str) -> list[BreachRow]:
    """The organisation's unanswered breaches, from the ledger's own
    derived read: which are unanswered is the ledger's to say."""
    rows = client.derived_named("UnansweredBreach", where={"org": org})
    breaches = [models.UnansweredBreachClaim.from_named(row.args) for row in rows]
    return sorted(
        (
            BreachRow(org=b.org, book=b.book, trade=b.trade, kind=b.kind, execution=b.execution)
            for b in breaches
        ),
        key=lambda row: (row.book, row.trade, row.kind),
    )
