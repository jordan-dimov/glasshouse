"""Currency in the ledger: a market is declared once with the currency
it is priced in, nothing trades or is priced in an undeclared market, and
a mark states its currency, which must be its market's. Each refusal is
named by the rule that made it. Two markets in two currencies in one
organisation, side by side.
"""

import datetime as dt
from decimal import Decimal

import pytest

from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, Rejected, models
from tests.support import BINARY, DB, login_role, needs_live_stack, provision

pytestmark = needs_live_stack

ORG, DE, GB = "two-currencies", "de-power", "gb-power"
AS_OF = dt.date(2026, 7, 1)
DAY = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)


def capture(trade: str, book: str, market: str) -> models.CaptureTradeRequest:
    return models.CaptureTradeRequest(
        org=ORG,
        book=book,
        trade=trade,
        counterparty="someone",
        market=market,
        direction="buy",
        version=f"{trade}/v1",
        quantity=Decimal("10"),
        price=Decimal("70"),
        delivery_start=DAY,
        delivery_end=DAY + dt.timedelta(hours=2),
        trade_date=dt.date(2026, 6, 30),
    )


def mark(trade: str, book: str, curve: str, currency: str) -> models.AdmitValuationRequest:
    return models.AdmitValuationRequest(
        org=ORG,
        book=book,
        trade=trade,
        curve_version=curve,
        terms_version=f"{trade}/v1",
        mtm=Decimal("12.5"),
        currency=currency,
    )


@pytest.fixture(scope="module")
def ledger() -> GlasshouseClient:
    """One organisation with a euro market and a pound market declared,
    a book in each, a trade and an official curve in each."""
    provision().dispose()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    client.init()
    for request, actor in (
        (models.EstablishLedgerRequest(login_role=login_role()), "bootstrap"),
        (models.EstablishOrganisationRequest(org=ORG), "bootstrap"),
        (models.DeclareMarketRequest(org=ORG, market=DE, currency="EUR"), "bootstrap"),
        (models.DeclareMarketRequest(org=ORG, market=GB, currency="GBP"), "bootstrap"),
        (models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book="de"), "bootstrap"),
        (models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book="gb"), "bootstrap"),
        (models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=DE), "bootstrap"),
        (models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=GB), "bootstrap"),
        (models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market="fr"), "bootstrap"),
        (models.GrantValuationAuthorityRequest(principal="risk", org=ORG, book="de"), "bootstrap"),
        (models.GrantValuationAuthorityRequest(principal="risk", org=ORG, book="gb"), "bootstrap"),
        (capture("D-1", "de", DE), "alice"),
        (capture("G-1", "gb", GB), "alice"),
        (
            models.RegisterCurveRequest(
                org=ORG, market=DE, as_of=AS_OF, version="crv-de", payload_hash="sha256:de"
            ),
            "carol",
        ),
        (
            models.RegisterCurveRequest(
                org=ORG, market=GB, as_of=AS_OF, version="crv-gb", payload_hash="sha256:gb"
            ),
            "carol",
        ),
    ):
        outcome = client.submit(request, actor=actor)
        assert isinstance(outcome, Committed), (request, outcome)
    return client


def refused(client: GlasshouseClient, request: object, actor: str) -> Rejected:
    outcome = client.submit(request, actor=actor, explain_on_reject=True)  # type: ignore[arg-type]
    assert isinstance(outcome, Rejected), outcome
    return outcome


def test_a_mark_must_state_its_markets_currency(ledger: GlasshouseClient) -> None:
    # The mark is computed outside the ledger, so it says what it is in,
    # and the ledger checks that against the trade's market. Euros for a
    # British trade is refused by the rule's own name; pounds is admitted.
    wrong = refused(ledger, mark("G-1", "gb", "crv-gb", "EUR"), "risk")
    assert wrong.rule == "mark_is_in_market_currency"
    wrong_way = refused(ledger, mark("D-1", "de", "crv-de", "GBP"), "risk")
    assert wrong_way.rule == "mark_is_in_market_currency"
    for request in (mark("G-1", "gb", "crv-gb", "GBP"), mark("D-1", "de", "crv-de", "EUR")):
        assert isinstance(ledger.submit(request, actor="risk"), Committed)
    stated = {v.trade: (v.mtm, v.currency) for v in ledger.read(models.TradeValuedClaim)}
    assert stated == {"G-1": (Decimal("12.5"), "GBP"), "D-1": (Decimal("12.5"), "EUR")}


def test_a_trade_cannot_be_marked_against_another_markets_curve(
    ledger: GlasshouseClient,
) -> None:
    # The older rule, unchanged, is what keeps a trade and its curve in
    # one market, and so in one currency: a British trade against the
    # German curve is refused before any question of currency.
    crossed = refused(ledger, mark("G-1", "gb", "crv-de", "GBP"), "risk")
    assert crossed.rule == "valued_against_official_curve_and_terms_in_force"


def test_nothing_trades_or_is_priced_in_an_undeclared_market(ledger: GlasshouseClient) -> None:
    trade = refused(ledger, capture("F-1", "de", "fr"), "alice")
    assert trade.rule == "market_has_a_currency"
    curve = refused(
        ledger,
        models.RegisterCurveRequest(
            org=ORG, market="fr", as_of=AS_OF, version="crv-fr", payload_hash="sha256:fr"
        ),
        "carol",
    )
    assert curve.rule == "market_has_a_currency"


def test_a_market_is_declared_once_by_whoever_may_grant_and_in_a_listed_currency(
    ledger: GlasshouseClient,
) -> None:
    again = refused(
        ledger, models.DeclareMarketRequest(org=ORG, market=GB, currency="EUR"), "bootstrap"
    )
    assert again.rule == "market_is_new"
    unentitled = refused(
        ledger, models.DeclareMarketRequest(org=ORG, market="nl", currency="EUR"), "alice"
    )
    assert unentitled.rule == "may_grant_authority"
    # A code that is not one power is priced in (here a typo for GBP)
    # never becomes a market's currency.
    typo = refused(
        ledger, models.DeclareMarketRequest(org=ORG, market="nl", currency="GPB"), "bootstrap"
    )
    assert typo.rule == "currency_is_one_power_is_priced_in"
    declared = {m.market: m.currency for m in ledger.read(models.MarketCurrencyClaim)}
    assert declared == {DE: "EUR", GB: "GBP"}
