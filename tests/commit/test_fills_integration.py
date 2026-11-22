"""Fills and breaches against the real binary (glasshouse#114).

A fill the market reports has happened, so `record_fill` books it even
when it breaks a rule, and in the same act records each breach it
brings: the cap truly broken, no approval where the book requires one,
the instruction already ended, a worse price than its limit, more than
it still held. Which of those apply is decided by `if` branches in the
act (Morpholog 0.0.19), so the trade is booked either way and only the
branches taken write. A book over its cap then trades only towards
flat. A breach is answered once, by someone with the right, and never by
the trader who placed the instruction. Overdue instructions are lapsed a
book at a time, by one act that visits every open instruction in it.

Every test works in a book of its own, so the order they run in does
not matter.
"""

import datetime as dt
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from typer.testing import CliRunner, Result

from glasshouse import cli
from glasshouse.api import fills
from glasshouse.commit import (
    MODEL_FILE,
    Committed,
    GlasshouseClient,
    MorphologRequestError,
    Rejected,
    models,
)
from glasshouse.compute.terms import terms_version_id
from glasshouse.imports.report import wire_text
from glasshouse.sweep import overdue_books, sweep_once
from tests.support import BINARY, DB, committed, login_role, needs_live_stack, refused

pytestmark = [needs_live_stack, pytest.mark.usefixtures("cli_binary")]

ORG, MARKET = "fills-org", "de-power"
DAY = dt.datetime(2027, 7, 1, tzinfo=dt.UTC)
ON = dt.date(2026, 7, 1)
BOOKS = (
    "held",
    "kinds",
    "late",
    "priced",
    "approved",
    "breached",
    "answered",
    "swept",
    "quiet",
    "raced",
)


@pytest.fixture(scope="module")
def ledger() -> GlasshouseClient:
    subprocess.run(["psql", DB, "-qc", "DROP SCHEMA IF EXISTS morpholog CASCADE"], check=True)
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    grants: list[object] = [
        models.EstablishLedgerRequest(login_role=login_role()),
        models.EstablishOrganisationRequest(org=ORG),
        models.DeclareMarketRequest(org=ORG, market=MARKET, currency="EUR"),
        models.GrantLimitAuthorityRequest(principal="risk", org=ORG),
        models.GrantFillAuthorityRequest(principal="recon", org=ORG),
        models.GrantBreachAuthorityRequest(principal="risk", org=ORG),
        # The trader may answer breaches in general, so the refusal below
        # is the segregation rule, not a missing right.
        models.GrantBreachAuthorityRequest(principal="trader", org=ORG),
        models.RequireApprovalRequest(org=ORG, book="approved"),
    ]
    for book in BOOKS:
        grants += [
            models.GrantInstructAuthorityRequest(principal="trader", org=ORG, book=book),
            models.GrantCaptureAuthorityRequest(principal="trader", org=ORG, book=book),
        ]
    for grant in grants:
        assert isinstance(client.submit(grant, actor="bootstrap"), Committed), grant  # type: ignore[arg-type]
    for book in BOOKS:
        cap = Decimal("100") if book == "breached" else Decimal("10")
        set_cap = models.SetPositionLimitRequest(org=ORG, book=book, cap=cap)
        assert isinstance(client.submit(set_cap, actor="risk"), Committed)
    return client


def instruct(
    book: str,
    name: str,
    direction: str,
    quantity: str,
    *,
    limit_price: str = "90",
    valid_for: float = 86400,
) -> models.PlaceInstructionRequest:
    return models.PlaceInstructionRequest(
        org=ORG,
        book=book,
        instruction=name,
        market=MARKET,
        direction=direction,
        quantity=Decimal(quantity),
        limit_price=Decimal(limit_price),
        delivery_start=DAY,
        delivery_end=DAY + dt.timedelta(hours=1),
        placed_on=ON,
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=valid_for),
    )


def fill(
    instruction: str, execution: str, quantity: str, price: str = "85"
) -> models.RecordFillRequest:
    # Through the builder the CLI and the seed use, version id and all.
    return fills.fill_request(
        org=ORG,
        instruction=instruction,
        execution=execution,
        trade=f"T-{execution}",
        counterparty="eex",
        quantity=quantity,
        price=price,
        trade_date=ON.isoformat(),
    )


def capture(book: str, trade: str, direction: str, quantity: str) -> models.CaptureTradeRequest:
    return models.CaptureTradeRequest(
        org=ORG,
        book=book,
        trade=trade,
        counterparty="cp",
        market=MARKET,
        direction=direction,
        version=terms_version_id(trade, 1),
        quantity=Decimal(quantity),
        price=Decimal("80"),
        delivery_start=DAY,
        delivery_end=DAY + dt.timedelta(hours=1),
        trade_date=ON,
    )


def breaches_of(client: GlasshouseClient, trade: str) -> set[str]:
    return fills.breaches_of(client, org=ORG, trade=trade)


def holding(client: GlasshouseClient, instruction: str) -> Decimal | None:
    held = [
        o.remaining
        for o in client.read(models.InstructionOpenClaim)
        if o.instruction == instruction
    ]
    return held[0] if held else None


def test_a_fill_within_what_was_held_books_the_trade_and_reduces_the_hold(
    ledger: GlasshouseClient,
) -> None:
    committed(ledger, instruct("held", "H-1", "buy", "6"), "trader")
    committed(ledger, fill("H-1", "H1", "4"), "recon")
    assert holding(ledger, "H-1") == Decimal("2")
    committed(ledger, fill("H-1", "H2", "2"), "recon")
    assert holding(ledger, "H-1") is None
    ended = {e.instruction: e.outcome for e in ledger.read(models.InstructionEndedClaim)}
    assert ended["H-1"] == "filled"
    assert breaches_of(ledger, "T-H1") == breaches_of(ledger, "T-H2") == set()
    booked = {t.trade: t.quantity for t in ledger.read(models.TradeTermsClaim)}
    assert (booked["T-H1"], booked["T-H2"]) == (Decimal("4"), Decimal("2"))
    # The same report twice is booked once.
    assert refused(ledger, fill("H-1", "H1", "1"), "recon").rule == "execution_is_new"
    # And only the reconciling service records fills.
    assert refused(ledger, fill("H-1", "H9", "1"), "trader").rule == "may_record_fills"


def test_a_fill_past_what_was_held_and_past_the_cap_brings_both_breaches(
    ledger: GlasshouseClient,
) -> None:
    # 6 MW held under a 10 MW cap; 12 MW fill at a worse price than 90.
    committed(ledger, instruct("kinds", "K-1", "buy", "6"), "trader")
    committed(ledger, fill("K-1", "K1", "12", price="95"), "recon")
    assert breaches_of(ledger, "T-K1") == {"overfill", "limit", "price"}
    assert holding(ledger, "K-1") is None


def test_a_late_fill_is_booked_with_its_breach(ledger: GlasshouseClient) -> None:
    # A fill for an instruction cancelled a minute earlier still happened.
    committed(ledger, instruct("late", "L-1", "sell", "3"), "trader")
    committed(ledger, models.CancelInstructionRequest(org=ORG, instruction="L-1"), "trader")
    committed(ledger, fill("L-1", "L1", "1", price="95"), "recon")
    # A sale above its limit price is a better price, not a breach.
    assert breaches_of(ledger, "T-L1") == {"after_end"}
    ended = {e.instruction: e.outcome for e in ledger.read(models.InstructionEndedClaim)}
    assert ended["L-1"] == "cancelled"


def test_a_worse_price_is_a_breach_either_way_round(ledger: GlasshouseClient) -> None:
    committed(ledger, instruct("priced", "P-B", "buy", "2"), "trader")
    committed(ledger, instruct("priced", "P-S", "sell", "2"), "trader")
    committed(ledger, fill("P-B", "PB", "2", price="90.01"), "recon")
    committed(ledger, fill("P-S", "PS", "2", price="89.99"), "recon")
    assert breaches_of(ledger, "T-PB") == breaches_of(ledger, "T-PS") == {"price"}


def test_an_unapproved_fill_in_a_book_that_requires_approval_is_a_breach(
    ledger: GlasshouseClient,
) -> None:
    committed(ledger, instruct("approved", "A-1", "buy", "1"), "trader")
    committed(ledger, fill("A-1", "A1", "1"), "recon")
    assert breaches_of(ledger, "T-A1") == {"unapproved"}


def test_a_book_over_its_cap_trades_only_towards_flat(ledger: GlasshouseClient) -> None:
    # etrmbiz's cases: cap 100, booked +120 after an oversized fill, an
    # open buy still holding 10 MW of room.
    committed(ledger, instruct("breached", "B-1", "buy", "90"), "trader")
    committed(ledger, instruct("breached", "B-2", "buy", "10"), "trader")
    committed(ledger, fill("B-1", "B1", "120"), "recon")
    assert "limit" in breaches_of(ledger, "T-B1")
    assert holding(ledger, "B-2") == Decimal("10")

    def asked(request: object) -> str:
        # What the rules would say, recording nothing.
        answer = ledger.explain(request.TRANSFORMATION, "trader", request.to_args_named())  # type: ignore[attr-defined]
        return "admissible" if answer.admissible else str(answer.rejection.rule)  # type: ignore[union-attr]

    # Further out: refused, by trade or by instruction.
    assert (
        refused(ledger, capture("breached", "B-UP", "buy", "1"), "trader").rule
        == "hour_within_limit"
    )
    assert refused(ledger, instruct("breached", "B-3", "buy", "1"), "trader").rule == (
        "breach_must_shrink"
    )
    # Flipped to the same size on the other side, +120 to -120: as far
    # out as before, so refused, by trade or by an instruction to sell
    # judged as if it filled.
    assert asked(capture("breached", "B-FLIP", "sell", "240")) == "breach_must_shrink"
    # (An instruction cannot be asked about: placing one reads the clock,
    # and `explain` has none. A refusal records nothing either.)
    flip = refused(ledger, instruct("breached", "B-FLIP", "sell", "240"), "trader")
    assert flip.rule == "breach_must_shrink"
    # The rule is an `or` of two comparisons, and from v0.0.21 the refusal
    # names both, in the rule's order: within the cap, or smaller than
    # before. Neither held.
    assert flip.compared is None
    assert [(b.op, wire_text(b.left), wire_text(b.right)) for b in flip.compared_branches] == [
        ("<=", "120 MW", "100 MW"),
        ("<", "120 MW", "120 MW"),
    ]
    # Across flat but smaller, +120 to -110: admissible.
    assert asked(capture("breached", "B-CROSS", "sell", "230")) == "admissible"
    # Towards flat: an instruction to sell, judged as if it filled, and a
    # sale from +120 to +70, both admitted.
    committed(ledger, instruct("breached", "B-4", "sell", "30"), "trader")
    committed(ledger, capture("breached", "B-DOWN", "sell", "50"), "trader")
    # Past flat and out the other side: -150 against the cap of 100.
    assert refused(ledger, capture("breached", "B-OVER", "sell", "220"), "trader").rule == (
        "hour_within_limit"
    )


def test_a_breach_is_answered_once_by_risk_and_never_by_its_trader(
    ledger: GlasshouseClient,
) -> None:
    committed(ledger, instruct("answered", "N-1", "buy", "1"), "trader")
    committed(ledger, fill("N-1", "N1", "1", price="99"), "recon")

    def answer(decision: str = "accepted") -> models.AnswerBreachRequest:
        return models.AnswerBreachRequest(
            org=ORG, trade="T-N1", kind="price", decision=decision, note="the market moved"
        )

    def unanswered() -> set[tuple[str, str]]:
        return {(b.trade, b.kind) for b in fills.list_breaches(ledger, org=ORG)}

    assert ("T-N1", "price") in unanswered()
    assert refused(ledger, answer(), "trader").rule == "answerer_is_not_the_trader"
    assert refused(ledger, answer("maybe"), "risk").rule == "breach_decision_is_one_of_four"
    committed(ledger, answer(), "risk")
    assert ("T-N1", "price") not in unanswered()
    assert refused(ledger, answer(), "risk").rule == "breach_not_yet_answered"
    (row,) = [b for b in ledger.read(models.BreachAnsweredClaim) if b.trade == "T-N1"]
    assert (row.answered_by, row.decision, row.note) == ("risk", "accepted", "the market moved")


def test_the_sweep_lapses_only_the_books_with_something_overdue(ledger: GlasshouseClient) -> None:
    committed(ledger, instruct("swept", "S-1", "buy", "1", valid_for=1.5), "trader")
    committed(ledger, instruct("swept", "S-2", "buy", "1"), "trader")
    committed(ledger, instruct("quiet", "Q-1", "buy", "1"), "trader")
    sweep = models.LapseOverdueInstructionsRequest(org=ORG, book="swept")
    assert refused(ledger, sweep, "trader").rule == "something_has_lapsed"
    time.sleep(2)
    assert (ORG, "swept") in overdue_books(ledger, now=dt.datetime.now(dt.UTC))
    assert (ORG, "quiet") not in overdue_books(ledger, now=dt.datetime.now(dt.UTC))
    outcomes = sweep_once(ledger, actor="recon")
    assert isinstance(outcomes[ORG, "swept"], Committed)
    assert (ORG, "quiet") not in outcomes
    assert holding(ledger, "S-1") is None
    assert holding(ledger, "S-2") == Decimal("1")
    assert (ORG, "swept") not in sweep_once(ledger, actor="recon")
    # A desk without the right cannot sweep a book.
    assert (
        refused(
            ledger, models.LapseOverdueInstructionsRequest(org=ORG, book="quiet"), "nobody"
        ).rule
        == "may_lapse"
    )


def test_the_cli_records_a_fill_and_answers_its_breach(ledger: GlasshouseClient) -> None:
    committed(ledger, instruct("priced", "C-1", "buy", "1"), "trader")
    runner = CliRunner()
    common = ["--org", ORG, "--database-url", DB]
    recorded = runner.invoke(
        cli.app,
        [
            "record-fill",
            *common,
            "--instruction", "C-1",
            "--execution", "C1",
            "--trade", "T-C1",
            "--counterparty", "eex",
            "--quantity", "1",
            "--price", "91",
            "--trade-date", "2026-07-01",
            "--actor", "recon",
        ],
    )  # fmt: skip
    assert recorded.exit_code == 0, recorded.output
    assert "breaches: price" in recorded.output
    answered = runner.invoke(
        cli.app,
        [
            "answer-breach",
            *common,
            "--trade", "T-C1",
            "--kind", "price",
            "--decision", "unwind",
            "--note", "outside tolerance",
            "--actor", "risk",
        ],
    )  # fmt: skip
    assert answered.exit_code == 0, answered.output
    again = runner.invoke(
        cli.app,
        ["lapse-instructions", *common, "--book", "quiet", "--actor", "trader"],
    )
    assert again.exit_code == 1


def test_one_execution_is_booked_once_even_when_two_attempts_race(
    ledger: GlasshouseClient,
) -> None:
    # The same outside report offered twice at once under two trade ids:
    # the ledger holds the execution unique, so one books and the other
    # is refused (or loses a serialisation race and is refused on retry).
    committed(ledger, instruct("raced", "R-1", "buy", "4"), "trader")

    def attempt(trade: str) -> object:
        client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
        request = fills.fill_request(
            org=ORG,
            instruction="R-1",
            execution="RACE-1",
            trade=trade,
            counterparty="eex",
            quantity="2",
            price="85",
            trade_date=ON.isoformat(),
        )
        for _ in range(5):
            try:
                return client.submit(request, actor="recon")
            except MorphologRequestError as raced:
                if not raced.retriable:
                    raise
        raise AssertionError("still racing after five attempts")

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, ["T-RACE-A", "T-RACE-B"]))
    booked = [o for o in outcomes if isinstance(o, Committed)]
    assert len(booked) == 1
    assert all(isinstance(o, Rejected) for o in outcomes if o not in booked)
    winner = fills.recorded_as(ledger, org=ORG, execution="RACE-1")
    assert winner in {"T-RACE-A", "T-RACE-B"}
    trades = {t.trade for t in ledger.read(models.TradeCapturedClaim)}
    assert len({"T-RACE-A", "T-RACE-B"} & trades) == 1
    assert holding(ledger, "R-1") == Decimal("2")

    # The reconciler retrying after a timeout: the same report is
    # already recorded; the same id with other figures is a conflict.
    runner = CliRunner()

    def report(quantity: str) -> Result:
        return runner.invoke(
            cli.app,
            [
                "record-fill",
                "--org", ORG,
                "--database-url", DB,
                "--instruction", "R-1",
                "--execution", "RACE-1",
                "--trade", "T-RACE-C",
                "--counterparty", "eex",
                "--quantity", quantity,
                "--price", "85",
                "--trade-date", ON.isoformat(),
                "--actor", "recon",
            ],
        )  # fmt: skip

    same = report("2")
    assert same.exit_code == 0, same.output
    assert f"already recorded, as {winner}" in same.output
    other = report("3")
    assert other.exit_code == 1
    assert "conflict" in other.output
    assert "quantity 2 booked, 3 now" in other.output
    assert holding(ledger, "R-1") == Decimal("2")  # nothing booked by either
