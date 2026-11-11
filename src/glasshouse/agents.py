"""The agent's way in: Glasshouse as a Model Context Protocol server.

An MCP client (an AI agent, or a plain script) gets a small set of tools:
read the book, ask whether a trade would be accepted, propose one, and
read what happened. Nothing here is a second way to do anything. Reads go
through the query layer the screens and the JSON API share; a proposal is
the same typed request a CSV row becomes, submitted through the commit
layer (law 1); a refusal is the rules' own account of itself, handed back
as data. No tool writes to the database any other way and no rule is
relaxed to make a tool work.

Who the agent is. One server is bound to one actor and one organisation
when it is built, and no tool takes either as an argument: an agent cannot
propose as someone else or read another organisation's book. Over stdio
the operator names the actor at launch; over HTTP the bearer token maps to
the configured one (`api/auth.py`). That is identity only, at the same
grade as the demo login. What the agent may do is decided where it is
decided for everyone, by the capability claims in the ledger (law 5): an
agent with no capture authority in a book is refused there, and a position
limit binds it as it binds a trader.

What is recorded. An accepted proposal is a transition in the audit
history under the agent's actor, like any other. A refusal is not in that
history: the audit log holds what was admitted. The substrate keeps
refusals in a separate log it calls an operational floor (written at most
once, after rollback, so one can be missing). `recent_refusals` reads that
log and says so with every answer.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from glasshouse.api import asof, instructions, queries, refusals
from glasshouse.api import audit as audit_queries
from glasshouse.api.routers.explain import flatten_rejection
from glasshouse.api.schemas import (
    AuditEntry,
    BlotterTrade,
    ExactDecimal,
    InstructionRow,
    PositionHour,
    RefusalLog,
    Rejection,
)
from glasshouse.commit import GlasshouseClient, models
from glasshouse.commit.morpholog_client import envelopes
from glasshouse.imports.report import why
from glasshouse.imports.trades import capture_request
from glasshouse.projections import catch_up

INSTRUCTIONS = (
    "Glasshouse is a book of record for power trading. Every change is a proposal that "
    "the rules accept or refuse. You act as one named actor in one organisation. Read the "
    "book with list_trades, list_positions, list_markets and position_limits. Ask whether "
    "a trade would be accepted with explain_trade (nothing is recorded). Propose one with "
    "propose_trade. An instruction is an intention that has not happened yet, such as an "
    "order sent to market: place_instruction records it and holds room for it under the "
    "book's position limit until it ends; cancel_instruction frees that room. "
    "A refusal is a normal answer, not an error: it names the rule and the figures that "
    "broke it. Quantities are MW. A price is per MWh in the currency of its market, and "
    "every price and valuation you are given names its currency: read it, never assume "
    "one. list_markets says which currency each market is priced in. Numbers are decimal "
    "text, never floating point. Times are UTC instants in ISO 8601."
)

REFUSALS_CAVEAT = refusals.CAVEAT


@dataclass(frozen=True)
class Desk:
    """What a server acts with and as: the commit client, the read
    model's engine, and the one actor and organisation it is bound to."""

    client: GlasshouseClient
    engine: sa.Engine
    actor: str
    org: str


class PositionLimit(BaseModel):
    """The cap on a book's net position in any one delivery hour."""

    book: str
    cap_mw: ExactDecimal


class Market(BaseModel):
    """A market and the currency its prices and valuations are in."""

    market: str
    currency: str


class ProposalOutcome(BaseModel):
    """What the rules said to one proposal. `accepted` is the answer.
    Accepted: `transition_id` names its place in the audit history.
    Refused: `summary` says why in one line and `refusal` carries the
    rule, the comparison that failed and the values it was reading."""

    accepted: bool
    actor: str
    transition_id: str | None = None
    summary: str | None = None
    refusal: Rejection | None = Field(default=None, discriminator="kind")


class WouldBe(BaseModel):
    """The answer to "would this be accepted?". Nothing was recorded."""

    admissible: bool
    actor: str
    summary: str | None = None
    refusal: Rejection | None = Field(default=None, discriminator="kind")


def _past(desk: Desk, as_of: str | None) -> asof.Snapshot | None:
    """The as-of mode of a read tool, with the caller's mistakes and the
    not-yet-final moment said as themselves."""
    try:
        at = asof.parse_instant(as_of)
        return None if at is None else asof.snapshot(desk.client, at)
    except ValueError as refused:
        raise ToolError(f"as_of: {refused}") from refused
    except asof.NotSettledError as unsettled:
        raise ToolError(f"as_of: {unsettled}; ask again in a moment") from unsettled


def _window(raw: str | None, name: str) -> dt.datetime | None:
    try:
        return asof.parse_instant(raw)
    except ValueError as refused:
        raise ToolError(f"{name}: {refused}") from refused


def _capture(
    desk: Desk,
    book: str,
    trade: str,
    counterparty: str,
    market: str,
    direction: str,
    quantity: str,
    price: str,
    delivery_start: str,
    delivery_end: str,
    trade_date: str,
) -> models.CaptureTradeRequest:
    """A trade tool's arguments as the one typed request a CSV row also
    becomes. `explain_trade` and `propose_trade` both come through here,
    so asking and proposing can never read a trade differently."""
    fields = {
        "book": book,
        "trade": trade,
        "counterparty": counterparty,
        "market": market,
        "direction": direction,
        "quantity": quantity,
        "price": price,
        "delivery_start": delivery_start,
        "delivery_end": delivery_end,
        "trade_date": trade_date,
    }
    try:
        return capture_request(fields, desk.org)
    except ValueError as malformed:
        # Not a refusal by the rules: the proposal could not be written
        # down honestly (a quantity that is not a decimal, a time with no
        # offset), so nothing was put to them.
        raise ToolError(f"the trade could not be read: {malformed}") from malformed


def _outcome(at: Desk, outcome: envelopes.Committed | envelopes.Rejected) -> ProposalOutcome:
    """The rules' answer to a proposal, as the data a tool returns: a
    refusal with its rule and figures, or the place in the audit history."""
    if isinstance(outcome, envelopes.Rejected):
        explained = outcome.explanation
        return ProposalOutcome(
            accepted=False,
            actor=at.actor,
            summary=why(explained) or outcome.reason,
            refusal=flatten_rejection(explained.rejection if explained else None),
        )
    return ProposalOutcome(accepted=True, actor=at.actor, transition_id=outcome.transition_id)


def build_server(desk: Callable[[], Desk]) -> MCPServer:
    """The server and its tools. `desk` is asked on every call, so the
    HTTP mount can hand over the client and engine the app's lifespan
    built after this server was constructed."""
    server = MCPServer("glasshouse", instructions=INSTRUCTIONS)

    @server.tool()
    def list_trades(
        book: str | None = None, market: str | None = None, as_of: str | None = None
    ) -> list[BlotterTrade]:
        """Every captured trade under its current terms, each with its
        price per MWh and the currency that price is in. Narrow by book or
        market. With as_of (a past UTC instant, ISO 8601) the answer is
        the book as it stood then."""
        at = desk()
        return queries.list_trades(
            at.engine,
            org=at.org,
            book=book,
            market=market,
            limit=None,
            offset=None,
            past=_past(at, as_of),
        )

    @server.tool()
    def list_positions(
        book: str | None = None,
        market: str | None = None,
        start: str | None = None,
        end: str | None = None,
        as_of: str | None = None,
    ) -> list[PositionHour]:
        """Net position in MW per UTC delivery hour (buy +, sell -), per
        book and market. start and end bound the delivery hours
        (start <= hour < end). With as_of the answer is the position as
        it stood at that past instant."""
        at = desk()
        return queries.list_positions(
            at.engine,
            org=at.org,
            book=book,
            market=market,
            start=_window(start, "start"),
            end=_window(end, "end"),
            past=_past(at, as_of),
        )

    @server.tool()
    def list_markets() -> list[Market]:
        """The markets this organisation trades in, each with the currency
        its prices are in (EUR for German power, GBP for British). A
        trade's price is per MWh in its market's currency."""
        at = desk()
        return sorted(
            (
                Market(market=declared.market, currency=declared.currency)
                for declared in at.client.read(models.MarketCurrencyClaim)
                if declared.org == at.org
            ),
            key=lambda market: market.market,
        )

    @server.tool()
    def position_limits() -> list[PositionLimit]:
        """The cap on each book's net position in any one delivery hour,
        in MW, either side of flat. A book with no entry is not capped. A
        trade that would take any hour past the cap is refused."""
        at = desk()
        return sorted(
            (
                PositionLimit(book=limit.book, cap_mw=limit.cap)
                for limit in at.client.read(models.PositionLimitClaim)
                if limit.org == at.org
            ),
            key=lambda limit: limit.book,
        )

    @server.tool()
    def explain_trade(
        book: str,
        trade: str,
        counterparty: str,
        market: str,
        direction: Literal["buy", "sell"],
        quantity: str,
        price: str,
        delivery_start: str,
        delivery_end: str,
        trade_date: str,
    ) -> WouldBe:
        """Ask whether a trade would be accepted, without proposing it.
        Nothing is recorded. Arguments are as for propose_trade."""
        at = desk()
        request = _capture(
            at,
            book,
            trade,
            counterparty,
            market,
            direction,
            quantity,
            price,
            delivery_start,
            delivery_end,
            trade_date,
        )
        result = at.client.explain(request.TRANSFORMATION, at.actor, request.to_args_named())
        return WouldBe(
            admissible=result.admissible,
            actor=at.actor,
            summary=None if result.admissible else why(result),
            refusal=flatten_rejection(result.rejection),
        )

    @server.tool()
    def propose_trade(
        book: str,
        trade: str,
        counterparty: str,
        market: str,
        direction: Literal["buy", "sell"],
        quantity: str,
        price: str,
        delivery_start: str,
        delivery_end: str,
        trade_date: str,
    ) -> ProposalOutcome:
        """Propose one fixed-price power trade. The rules accept it or
        refuse it; a refusal is a normal answer and names the rule. A
        refusal by `hour_within_limit` means the booked trades leave no
        room. One by `room_not_held_by_instructions` means there would
        be room, but open instructions are holding it.

        trade: your identifier for it, unique in the organisation.
        quantity: MW as decimal text ("2.5"). price: per MWh, as decimal
        text, in the currency of the market (see list_markets); the
        trade takes that currency, you do not state one. A market that
        has not been declared is refused. delivery_start, delivery_end:
        UTC instants on the hour with an offset
        ("2026-07-01T12:00:00+00:00"); the end is exclusive.
        trade_date: the ISO date the trade was struck, on or before
        delivery starts."""
        at = desk()
        request = _capture(
            at,
            book,
            trade,
            counterparty,
            market,
            direction,
            quantity,
            price,
            delivery_start,
            delivery_end,
            trade_date,
        )
        outcome = at.client.submit(request, actor=at.actor, explain_on_reject=True)
        if isinstance(outcome, envelopes.Committed):
            # The read tools answer from the projections: bring them up
            # to this transition before returning, so the agent's next
            # read sees its own trade whichever process runs the
            # projector.
            catch_up(at.client, at.engine)
        return _outcome(at, outcome)

    @server.tool()
    def place_instruction(
        book: str,
        instruction: str,
        market: str,
        direction: Literal["buy", "sell"],
        quantity: str,
        limit_price: str,
        delivery_start: str,
        delivery_end: str,
        valid_until: str,
    ) -> ProposalOutcome:
        """Record an intention to buy or sell that has not happened yet,
        such as an order you are about to send to market. From now until
        it ends it holds room under the book's position limit, so no
        trade or other instruction can use that room. It is refused if
        it would not fit.

        instruction: your identifier for it, unique in the organisation.
        quantity: MW as decimal text. limit_price: per MWh as decimal
        text, in the currency of the market. delivery_start,
        delivery_end: UTC instants on the hour with an offset; the end is
        exclusive. valid_until: a UTC instant with an offset; after it
        the instruction is overdue. An overdue instruction still holds
        its room until it is cancelled or its lapse is recorded.

        An instruction is not approved by anyone and is not connected to
        fills yet. Booking the trade that results is still
        propose_trade; cancel the instruction first, or its room stays
        held."""
        at = desk()
        try:
            request = instructions.place_request(
                {
                    "book": book,
                    "instruction": instruction,
                    "market": market,
                    "direction": direction,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "delivery_start": delivery_start,
                    "delivery_end": delivery_end,
                    "valid_until": valid_until,
                },
                org=at.org,
                placed_on=dt.datetime.now(dt.UTC).date(),
            )
        except ValueError as malformed:
            raise ToolError(f"the instruction could not be read: {malformed}") from malformed
        return _outcome(at, at.client.submit(request, actor=at.actor, explain_on_reject=True))

    @server.tool()
    def cancel_instruction(instruction: str) -> ProposalOutcome:
        """Cancel an instruction that has not ended (open or overdue).
        The room it held under the book's position limit is free again
        at once. Refused if it has already ended or you may not instruct
        in its book."""
        at = desk()
        request = models.CancelInstructionRequest(org=at.org, instruction=instruction)
        return _outcome(at, at.client.submit(request, actor=at.actor, explain_on_reject=True))

    @server.tool()
    def list_instructions(book: str | None = None, open_only: bool = False) -> list[InstructionRow]:
        """Instructions in this organisation: what was intended, how much
        each still holds under its book's limit (holding_mw), and its
        status: open, overdue (past valid_until but still holding room
        until cancelled), cancelled or lapsed. open_only keeps those
        holding room now."""
        at = desk()
        return instructions.list_instructions(at.client, org=at.org, book=book, open_only=open_only)

    @server.tool()
    def audit_history(limit: int = 20, actor: str | None = None) -> list[AuditEntry]:
        """The accepted changes that touch this organisation, newest
        first: who made each, by which rule, and the facts it added or
        withdrew. Narrow to one actor to see what it did. Refusals are not
        here; see recent_refusals."""
        at = desk()
        entries = (
            entry
            for entry in audit_queries.list_audit(at.client)
            if audit_queries.mentions_org(entry, at.org) and (actor is None or entry.actor == actor)
        )
        return [entry for entry, _ in zip(entries, range(max(1, min(limit, 500))), strict=False)]

    @server.tool()
    def recent_refusals(limit: int = 20, actor: str | None = None) -> RefusalLog:
        """Recent proposals the rules refused in this organisation, newest
        first, with the rule and the figures. A best-effort log: read the
        caveat it returns."""
        at = desk()
        return refusals.list_refusals(at.client, org=at.org, limit=limit, actor=actor)

    return server
