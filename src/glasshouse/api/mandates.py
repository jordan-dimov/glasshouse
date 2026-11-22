"""Agents' mandates as one read: each mandate's bounds, who proposed and
approved it, whether it stands (open, suspended, expired, or awaiting
approval), how much of its volume is used, and whether the model its
agent runs on is the approved one.

Nothing here decides anything. The rules decide at each trade
(`trade_under_mandate` in the rule file); this read says what a person,
or the agent itself, needs to know before asking them. `used` is the sum
of the trades booked under the mandate, as the rule sums it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from glasshouse.commit import GlasshouseClient
from glasshouse.commit.morpholog_client import values

Standing = Literal["awaiting approval", "open", "suspended", "expired"]

PREDICATES = (
    "Mandate",
    "MandateApproved",
    "MandateSuspended",
    "TradeUnderMandate",
    "AgentModel",
    "AgentModelApproved",
)


@dataclass(frozen=True)
class MandateRow:
    mandate: str
    agent: str
    book: str
    market: str
    side: str
    max_order: Decimal
    max_volume: Decimal
    used: Decimal
    trades: int
    price_floor: Decimal
    price_ceiling: Decimal
    deliver_from: dt.datetime
    deliver_until: dt.datetime
    valid_until: dt.datetime
    proposer: str
    approver: str | None
    suspended_by: str | None
    standing: Standing
    # The agent's model record: what it names, and whether it is approved.
    # A mandate admits trades only from a door that reports this model.
    model: str | None
    model_approved: bool


def _amount(raw: object) -> Decimal:
    """A quantity as the named read spells it (`"2 MW"` or `"2"`)."""
    return values.parse_decimal(str(raw).split()[0])


def standing_of(
    *, approved: bool, suspended: bool, valid_until: dt.datetime, now: dt.datetime
) -> Standing:
    if suspended:
        return "suspended"
    if now > valid_until:
        return "expired"
    return "open" if approved else "awaiting approval"


def list_mandates(
    client: GlasshouseClient, *, org: str, agent: str | None = None, now: dt.datetime | None = None
) -> list[MandateRow]:
    """Every mandate in `org` (or only `agent`'s), oldest id first, read
    at one instant so the totals and the standings agree."""
    at = now or dt.datetime.now(dt.UTC)
    rows = {
        name: [claim.args for claim in client.claims_named(name, where={"org": org})]
        for name in PREDICATES
    }
    approvers = {str(r["mandate"]): str(r["approver"]) for r in rows["MandateApproved"]}
    suspenders = {str(r["mandate"]): str(r["suspended_by"]) for r in rows["MandateSuspended"]}
    used: dict[str, tuple[Decimal, int]] = {}
    for r in rows["TradeUnderMandate"]:
        total, count = used.get(str(r["mandate"]), (Decimal(0), 0))
        used[str(r["mandate"])] = (total + _amount(r["quantity"]), count + 1)
    recorded = {str(r["agent"]): str(r["model"]) for r in rows["AgentModel"]}
    approved_models = {(str(r["agent"]), str(r["model"])) for r in rows["AgentModelApproved"]}
    out = []
    for r in rows["Mandate"]:
        if agent is not None and r["agent"] != agent:
            continue
        mandate = str(r["mandate"])
        valid_until = values.parse_timestamp(str(r["valid_until"]))
        total, count = used.get(mandate, (Decimal(0), 0))
        model = recorded.get(str(r["agent"]))
        out.append(
            MandateRow(
                mandate=mandate,
                agent=str(r["agent"]),
                book=str(r["book"]),
                market=str(r["market"]),
                side=str(r["side"]),
                max_order=_amount(r["max_order"]),
                max_volume=_amount(r["max_volume"]),
                used=total,
                trades=count,
                price_floor=values.parse_decimal(str(r["price_floor"])),
                price_ceiling=values.parse_decimal(str(r["price_ceiling"])),
                deliver_from=values.parse_timestamp(str(r["deliver_from"])),
                deliver_until=values.parse_timestamp(str(r["deliver_until"])),
                valid_until=valid_until,
                proposer=str(r["proposer"]),
                approver=approvers.get(mandate),
                suspended_by=suspenders.get(mandate),
                standing=standing_of(
                    approved=mandate in approvers,
                    suspended=mandate in suspenders,
                    valid_until=valid_until,
                    now=at,
                ),
                model=model,
                model_approved=model is not None and (str(r["agent"]), model) in approved_models,
            )
        )
    return sorted(out, key=lambda row: row.mandate)
