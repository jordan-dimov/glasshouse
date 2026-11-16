"""The limit gates against an independent, hour-by-hour calculation.

The ledger checks a book's cap only at the hours where the book changes
(the window's first hour, and every start and end of a trade or open
instruction inside it), on the argument that nothing changes in between.
That replaced checking every hour, so it is an evaluation strategy that
must give the same answers, not just a faster one. Here random books are
built (caps, trades, open instructions, some taken past their cap by an
oversized fill), random captures and amendments are put to the ledger
through `explain`, and each verdict is compared with the rules written
plainly in Python and applied to every hour of the window: the same
rule refuses, or both admit.

Seeded, so a failure reproduces; every book is its own, so the order of
cases does not matter.
"""

import datetime as dt
import random
import subprocess
from dataclasses import dataclass
from decimal import Decimal

import pytest

from glasshouse.api import fills
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.compute.terms import terms_version_id
from tests.support import BINARY, DB, committed, login_role, needs_live_stack

pytestmark = needs_live_stack

ORG, MARKET = "differential-org", "de-power"
DAY = dt.datetime(2027, 3, 1, tzinfo=dt.UTC)
ON = dt.date(2026, 7, 1)
HOURS = 12
CASES = 60


@dataclass(frozen=True)
class Booked:
    trade: str
    signed: Decimal
    start: int
    end: int


@dataclass(frozen=True)
class Held:
    side: str
    left: Decimal
    start: int
    end: int


def _at(hour: int) -> dt.datetime:
    return DAY + dt.timedelta(hours=hour)


def _net(trades: list[Booked], h: int, excluded: str | None = None) -> Decimal:
    return sum(
        (t.signed for t in trades if t.trade != excluded and t.start <= h < t.end), Decimal(0)
    )


def _held(open_: list[Held], side: str, h: int) -> Decimal:
    return sum((i.left for i in open_ if i.side == side and i.start <= h < i.end), Decimal(0))


def expected(
    cap: Decimal,
    trades: list[Booked],
    open_: list[Held],
    *,
    excluded: str,
    signed: Decimal,
    start: int,
    end: int,
    only: set[int] | None = None,
) -> str:
    """The three limit rules in their plain per-hour form, in the order
    the act checks them: the first that fails in any hour, or
    `admissible`. `only` checks a subset of the hours instead, which the
    test uses to measure its own reach, never as the oracle."""
    hours = [h for h in range(start, end) if only is None or h in only]

    def figures(h: int) -> tuple[Decimal, Decimal]:
        before = _net(trades, h)
        return _net(trades, h, excluded) + signed, before

    if not all(abs(a) <= max(cap, abs(b)) for a, b in map(figures, hours)):
        return "hour_within_limit"
    if not all(abs(b) <= cap or abs(a) < abs(b) for a, b in map(figures, hours)):
        return "breach_must_shrink"
    for h in hours:
        after, before = figures(h)
        bought, sold = _held(open_, "buy", h), _held(open_, "sell", h)
        exposure = max(bought + before, sold - before)
        if not (bought <= max(cap, exposure) - after and sold <= max(cap, exposure) + after):
            return "room_not_held_by_instructions"
    return "admissible"


@pytest.fixture(scope="module")
def ledger() -> GlasshouseClient:
    subprocess.run(["psql", DB, "-qc", "DROP SCHEMA IF EXISTS morpholog CASCADE"], check=True)
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    for grant in (
        models.EstablishLedgerRequest(login_role=login_role()),
        models.EstablishOrganisationRequest(org=ORG),
        models.DeclareMarketRequest(org=ORG, market=MARKET, currency="EUR"),
        models.GrantLimitAuthorityRequest(principal="risk", org=ORG),
        models.GrantFillAuthorityRequest(principal="recon", org=ORG),
    ):
        committed(client, grant, "bootstrap")
    return client


def _window(rng: random.Random) -> tuple[int, int]:
    start = rng.randrange(HOURS)
    return start, rng.randrange(start + 1, HOURS + 1)


def _capture(book: str, trade: str, side: str, quantity: Decimal, start: int, end: int):  # type: ignore[no-untyped-def]
    return models.CaptureTradeRequest(
        org=ORG,
        book=book,
        trade=trade,
        counterparty="cp",
        market=MARKET,
        direction=side,
        version=terms_version_id(trade, 1),
        quantity=quantity,
        price=Decimal("80"),
        delivery_start=_at(start),
        delivery_end=_at(end),
        trade_date=ON,
    )


def _book(client: GlasshouseClient, rng: random.Random, book: str) -> tuple[Decimal, list[Booked]]:
    """A capped book with some trades, some open instructions, and
    sometimes an oversized fill that takes it past its cap. Whatever the
    gates refuse while building it is simply not in the book."""
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="t", org=ORG, book=book),
        models.GrantInstructAuthorityRequest(principal="t", org=ORG, book=book),
    ):
        committed(client, grant, "bootstrap")
    cap = Decimal(rng.choice(("4", "6", "10")))
    committed(client, models.SetPositionLimitRequest(org=ORG, book=book, cap=cap), "risk")
    booked: list[Booked] = []
    for n in range(rng.randrange(2, 7)):
        side = rng.choice(("buy", "sell"))
        quantity = Decimal(rng.randrange(1, 7))
        start, end = _window(rng)
        trade = f"{book}-T{n}"
        if isinstance(
            client.submit(_capture(book, trade, side, quantity, start, end), actor="t"), Committed
        ):
            booked.append(Booked(trade, quantity if side == "buy" else -quantity, start, end))
    if rng.random() < 0.5:
        # An order that fills for more than it held: the book may end up
        # over its cap, which is where the ways back are tested.
        start, end = _window(rng)
        side = rng.choice(("buy", "sell"))
        placed = client.submit(
            models.PlaceInstructionRequest(
                org=ORG,
                book=book,
                instruction=f"{book}-F",
                market=MARKET,
                direction=side,
                quantity=Decimal("1"),
                limit_price=Decimal("90"),
                delivery_start=_at(start),
                delivery_end=_at(end),
                placed_on=ON,
                valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
            ),
            actor="t",
        )
        if isinstance(placed, Committed):
            quantity = Decimal(rng.randrange(cap.to_integral_value().__int__(), 15))
            fill = fills.fill_request(
                org=ORG,
                instruction=f"{book}-F",
                execution=f"{book}-X",
                trade=f"{book}-TF",
                counterparty="eex",
                quantity=str(quantity),
                price="85",
                trade_date=ON.isoformat(),
            )
            committed(client, fill, "recon")
            booked.append(
                Booked(f"{book}-TF", quantity if side == "buy" else -quantity, start, end)
            )
    for n in range(rng.randrange(0, 3)):
        start, end = _window(rng)
        client.submit(
            models.PlaceInstructionRequest(
                org=ORG,
                book=book,
                instruction=f"{book}-I{n}",
                market=MARKET,
                direction=rng.choice(("buy", "sell")),
                quantity=Decimal(rng.randrange(1, 5)),
                limit_price=Decimal("90"),
                delivery_start=_at(start),
                delivery_end=_at(end),
                placed_on=ON,
                valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(days=1),
            ),
            actor="t",
        )
    return cap, booked


def _shaped_book(
    client: GlasshouseClient, rng: random.Random, book: str
) -> tuple[Decimal, list[Booked], tuple[str, Decimal, int, int]]:
    """A book whose answer turns on an end point: a long trade on one
    side, an opposite trade starting with it and ending part-way, and a
    candidate on the long side across the whole span, sized so the cap
    holds while the opposite trade lasts and breaks once it has ended.
    Nothing new starts there, so only the end point shows it."""
    committed(
        client, models.GrantCaptureAuthorityRequest(principal="t", org=ORG, book=book), "bootstrap"
    )
    side, other = rng.choice((("buy", "sell"), ("sell", "buy")))
    sign = Decimal(1) if side == "buy" else Decimal(-1)
    long_q, short_q = Decimal(rng.randrange(2, 6)), Decimal(rng.randrange(1, 4))
    ends = rng.randrange(2, HOURS - 1)
    candidate = Decimal(rng.randrange(1, 5))
    # The book itself must fit (cap >= the long trade alone); the
    # candidate must fit while the opposite trade lasts and not after.
    lowest = long_q + max(Decimal(0), candidate - short_q)
    cap = Decimal(rng.randrange(int(lowest), int(long_q + candidate)))
    committed(client, models.SetPositionLimitRequest(org=ORG, book=book, cap=cap), "risk")
    committed(client, _capture(book, f"{book}-L", side, long_q, 0, HOURS), "t")
    committed(client, _capture(book, f"{book}-S", other, short_q, 0, ends), "t")
    booked = [
        Booked(f"{book}-L", sign * long_q, 0, HOURS),
        Booked(f"{book}-S", -sign * short_q, 0, ends),
    ]
    return cap, booked, (side, candidate, 0, HOURS)


def _open(client: GlasshouseClient, book: str) -> list[Held]:
    return [
        Held(
            o.direction,
            o.remaining,
            int((o.delivery_start - DAY).total_seconds()) // 3600,
            int((o.delivery_end - DAY).total_seconds()) // 3600,
        )
        for o in client.read(models.InstructionOpenClaim)
        if o.org == ORG and o.book == book
    ]


def _verdict(client: GlasshouseClient, request: object) -> str:
    answer = client.explain(
        request.TRANSFORMATION,  # type: ignore[attr-defined]
        "t",
        request.to_args_named(),  # type: ignore[attr-defined]
    )
    return "admissible" if answer.admissible else str(answer.rejection.rule)  # type: ignore[union-attr]


def test_the_gates_decide_as_every_hour_checked_one_by_one(ledger: GlasshouseClient) -> None:
    rng = random.Random(142)
    disagreements = []
    seen: set[str] = set()
    needs_ends = 0
    for case in range(CASES):
        book = f"book-{case}"
        if case % 5 == 0:
            cap, booked, (side, quantity, start, end) = _shaped_book(ledger, rng, book)
        else:
            cap, booked = _book(ledger, rng, book)
            side = rng.choice(("buy", "sell"))
            quantity = Decimal(rng.randrange(1, 13))
            start, end = _window(rng)
        open_ = _open(ledger, book)
        signed = quantity if side == "buy" else -quantity
        if case % 5 and booked and rng.random() < 0.4:
            # An amendment of a booked trade: its own earlier terms leave
            # the sum, its new terms join it.
            target = rng.choice(booked)
            request: object = models.AmendTradeRequest(
                org=ORG,
                trade=target.trade,
                prior_version=terms_version_id(target.trade, 1),
                new_version=terms_version_id(target.trade, 2),
                quantity=quantity,
                price=Decimal("80"),
                delivery_start=_at(start),
                delivery_end=_at(end),
                effective_from=ON + dt.timedelta(days=1),
            )
            signed = quantity if target.signed > 0 else -quantity
            excluded = target.trade
        else:
            request = _capture(book, f"{book}-NEW", side, quantity, start, end)
            excluded = f"{book}-NEW"
        want = expected(cap, booked, open_, excluded=excluded, signed=signed, start=start, end=end)
        got = _verdict(ledger, request)
        seen.add(want)
        # Would checking only the window's first hour and the starts
        # inside it have answered differently? Then this case depends on
        # an end point, the one kind of point a start-only check misses.
        starts = {start} | {t.start for t in booked} | {i.start for i in open_}
        if (
            expected(
                cap,
                booked,
                open_,
                excluded=excluded,
                signed=signed,
                start=start,
                end=end,
                only=starts,
            )
            != want
        ):
            needs_ends += 1
        if got != want:
            disagreements.append((case, want, got, cap, booked, open_, request))
    assert disagreements == []
    # The cases reached every outcome, so agreement means something.
    assert seen >= {"admissible", "hour_within_limit", "room_not_held_by_instructions"}
    # And enough of them turn on an end point that a gate checking only
    # starts would be caught here (checked by deleting the end checks
    # from `keeps_to_cap`: this test then fails).
    assert needs_ends >= 3
