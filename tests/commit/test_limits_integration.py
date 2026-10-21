"""The per-hour position limit against the real binary: a book's cap on
its net position holds in every delivery hour a trade touches, computed
from the trade's own window (`periods(delivery_start, delivery_end,
duration(PT1H))`, morpholog#563, v0.0.16). What is proved here is the
gate's arithmetic and its refusal: the sum over the book's terms in
force, signed by direction, plus the proposal, within the cap either way
round, hour by hour, with the refusal naming the hour; alignment and the
window bound; an uncapped book trading freely; a changed cap taking
effect; and the same gate on an amendment, the amended trade's own prior
terms left out of the sum.
"""

import datetime as dt
import subprocess
from decimal import Decimal

import pytest

from glasshouse import cli
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, Rejected, models, values
from glasshouse.commit.morpholog_client.envelopes import GateRejection
from glasshouse.compute.terms import terms_version_id
from glasshouse.imports.report import why
from tests.support import BINARY, DB, login_role, needs_live_stack

# usefixtures("cli_binary") pins GLASSHOUSE_MORPHOLOG_BIN for the CLI leg.
pytestmark = [needs_live_stack, pytest.mark.usefixtures("cli_binary")]

ORG, CAPPED, UNCAPPED, MARKET = "acme-energy", "spec-de", "hedge-de", "de-power"
DAY = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
TRADE_DATE = dt.date(2026, 6, 1)


def _capture(
    trade: str,
    direction: str,
    quantity: str,
    start: int,
    end: int,
    *,
    book: str = CAPPED,
    minutes: int = 0,
) -> models.CaptureTradeRequest:
    return models.CaptureTradeRequest(
        org=ORG,
        book=book,
        trade=trade,
        counterparty="stadtwerk-x",
        market=MARKET,
        direction=direction,
        version=terms_version_id(trade, 1),
        quantity=Decimal(quantity),
        price=Decimal("80"),
        delivery_start=DAY + dt.timedelta(hours=start, minutes=minutes),
        delivery_end=DAY + dt.timedelta(hours=end),
        trade_date=TRADE_DATE,
    )


@pytest.fixture(scope="module")
def ledger() -> GlasshouseClient:
    subprocess.run(["psql", DB, "-qc", "DROP SCHEMA IF EXISTS morpholog CASCADE"], check=True)
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    for grant in (
        models.EstablishLedgerRequest(login_role=login_role()),
        models.EstablishOrganisationRequest(org=ORG),
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=CAPPED),
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=UNCAPPED),
        models.GrantLimitAuthorityRequest(principal="risk", org=ORG),
    ):
        assert isinstance(client.submit(grant, actor="bootstrap"), Committed)
    # Only risk may cap a book, and a cap is a positive figure.
    usurped = client.submit(
        models.SetPositionLimitRequest(org=ORG, book=CAPPED, cap=Decimal("10")), actor="alice"
    )
    assert isinstance(usurped, Rejected)
    assert usurped.rule == "may_set_limit"
    nothing = client.submit(
        models.SetPositionLimitRequest(org=ORG, book=CAPPED, cap=Decimal("0")),
        actor="risk",
        explain_on_reject=True,
    )
    assert isinstance(nothing, Rejected)
    assert why(nothing.explanation).startswith("would break invariant position_limit_is_positive")
    capped = client.submit(
        models.SetPositionLimitRequest(org=ORG, book=CAPPED, cap=Decimal("10")), actor="risk"
    )
    assert isinstance(capped, Committed), capped
    return client


def _refusal(outcome: object) -> GateRejection:
    assert isinstance(outcome, Rejected), outcome
    assert outcome.explanation is not None
    rejection = outcome.explanation.rejection
    assert isinstance(rejection, GateRejection), rejection
    return rejection


def test_the_cap_holds_in_every_hour_then_changes_then_caps_the_amendment(
    ledger: GlasshouseClient,
) -> None:
    # One story in order (pytest-randomly shuffles functions, not the
    # lines of one): captures under a 10 MW cap, the cap changed, an
    # amendment judged under the new cap.
    #
    # 8 MW bought over 08-12 fits a 10 MW cap; 5 MW more over 10-14
    # would put hours 10 and 11 at 13 MW, so it is refused by name, with
    # the ledger's own arithmetic and the first hour that breaks.
    assert isinstance(ledger.submit(_capture("L-001", "buy", "8", 8, 12), actor="alice"), Committed)
    over = ledger.submit(
        _capture("L-002", "buy", "5", 10, 14), actor="alice", explain_on_reject=True
    )
    refused = _refusal(over)
    assert refused.rule == "hour_within_limit"
    assert refused.compared is not None
    assert (refused.compared.left, refused.compared.op, refused.compared.right) == (
        values.Quantity(Decimal("13"), "MW"),
        "<=",
        values.Quantity(Decimal("10"), "MW"),
    )
    assert {b.var: b.value for b in refused.witness}["h"] == DAY + dt.timedelta(hours=10)
    assert why(over.explanation) == (  # type: ignore[union-attr]
        "hour_within_limit: 13 MW <= 10 MW does not hold (h = 2026-07-01T10:00:00+00:00)"
    )
    # Selling nets the position down: 5 MW sold over 10-14 leaves hours
    # 10-11 at 3 MW and 12-13 at -5 MW, within the cap either way round.
    assert isinstance(
        ledger.submit(_capture("L-003", "sell", "5", 10, 14), actor="alice"), Committed
    )
    # And the hours outside the first trade's window are judged on their
    # own: 9 MW bought over 14-16 meets nothing and fits.
    assert isinstance(
        ledger.submit(_capture("L-004", "buy", "9", 14, 16), actor="alice"), Committed
    )
    # The short side has the same cap: 6 MW more sold over 12-14 would
    # take hours 12-13 to -11 MW.
    short = ledger.submit(
        _capture("L-005", "sell", "6", 12, 14), actor="alice", explain_on_reject=True
    )
    assert _refusal(short).rule == "hour_within_limit"
    assert "11 MW <= 10 MW does not hold (h = 2026-07-01T12:00:00+00:00)" in why(
        short.explanation  # type: ignore[union-attr]
    )

    # The cap changed: the old one stays in the log, the new one governs.
    changed = ledger.submit(
        models.ChangePositionLimitRequest(org=ORG, book=CAPPED, cap=Decimal("20")), actor="risk"
    )
    assert isinstance(changed, Committed), changed
    assert [c.predicate for c in changed.retracted_claims] == ["PositionLimit"]
    # The 5 MW that broke a 10 MW cap over 10-14 (hours 10-11 at 13 MW)
    # fits under 20 MW.
    assert isinstance(
        ledger.submit(_capture("L-002", "buy", "5", 10, 14), actor="alice"), Committed
    )
    (standing,) = ledger.read(models.PositionLimitClaim)
    assert standing.cap == Decimal("20")
    # A second set is refused: changing is the other act.
    again = ledger.submit(
        models.SetPositionLimitRequest(org=ORG, book=CAPPED, cap=Decimal("30")), actor="risk"
    )
    assert isinstance(again, Rejected)
    assert again.rule == "limit_is_new"

    # An amendment under the 20 MW cap, the amended trade left out.
    # Under the 20 MW cap, hours 10-11 hold 8 (L-001) - 5 (L-003) + 5
    # (L-002) = 8 MW. Amending L-002 from 5 to 17 MW over the same window
    # would put them at 20 MW: within the cap only because L-002's prior
    # 5 MW is left out of the sum. 18 MW is one over.
    def amend(new_version: str, quantity: str) -> models.AmendTradeRequest:
        return models.AmendTradeRequest(
            org=ORG,
            trade="L-002",
            prior_version=terms_version_id("L-002", 1),
            new_version=new_version,
            quantity=Decimal(quantity),
            price=Decimal("80"),
            delivery_start=DAY + dt.timedelta(hours=10),
            delivery_end=DAY + dt.timedelta(hours=14),
            effective_from=TRADE_DATE + dt.timedelta(days=1),
        )

    over = ledger.submit(amend("L-002/v-over", "18"), actor="alice", explain_on_reject=True)
    refused = _refusal(over)
    assert refused.rule == "hour_within_limit"
    assert "21 MW <= 20 MW does not hold (h = 2026-07-01T10:00:00+00:00)" in why(
        over.explanation  # type: ignore[union-attr]
    )
    fits = ledger.submit(amend("L-002/v2", "17"), actor="alice")
    assert isinstance(fits, Committed), fits


def test_the_window_is_bounded_and_whole_hours(ledger: GlasshouseClient) -> None:
    # A trade starting at half past meets one hour more than its length
    # holds (grid alignment, the compute zone's own rule now the
    # ledger's); a window over a leap year is beyond any product here.
    skewed = ledger.submit(
        _capture("L-010", "buy", "1", 20, 22, minutes=30), actor="alice", explain_on_reject=True
    )
    assert _refusal(skewed).rule == "delivery_is_whole_hours"
    too_long = ledger.submit(
        _capture("L-011", "buy", "1", 0, 24 * 367), actor="alice", explain_on_reject=True
    )
    assert _refusal(too_long).rule == "delivery_is_at_most_a_year"


def test_an_uncapped_book_trades_freely(ledger: GlasshouseClient) -> None:
    # No PositionLimit claim for hedge-de: the gate ranges over nothing
    # and the capture commits whatever the size.
    big = ledger.submit(_capture("L-020", "buy", "500", 0, 24, book=UNCAPPED), actor="alice")
    assert isinstance(big, Committed), big


def test_the_cli_sets_then_changes_a_cap_and_refuses_the_unentitled(
    ledger: GlasshouseClient, capsys: pytest.CaptureFixture[str]
) -> None:
    # One command for both acts: it reads the standing cap and picks set
    # or change, printing what stood. A book of its own, so the story
    # above and the uncapped book are untouched whatever the order.
    base = ["set-limit", "--org", ORG, "--book", "cli-de", "--database-url", DB]
    assert cli.main([*base, "--cap", "50", "--actor", "risk"]) == 0
    assert "position limit for cli-de in acme-energy: 50 MW; transition" in capsys.readouterr().out
    assert cli.main([*base, "--cap", "60", "--actor", "risk"]) == 0
    assert "60 MW (was 50 MW)" in capsys.readouterr().out
    assert cli.main([*base, "--cap", "70", "--actor", "alice"]) == 1
    assert "refused: may_set_limit" in capsys.readouterr().err
    (standing,) = [
        limit for limit in ledger.read(models.PositionLimitClaim) if limit.book == "cli-de"
    ]
    assert standing.cap == Decimal("60")
