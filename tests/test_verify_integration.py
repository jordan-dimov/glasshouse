"""`glasshouse verify` against the real stack: all five legs consistent
after the Monday-morning flow, then each tamperable leg caught and
restored. The committed history is a module fixture; tests are
state-based and order-independent (tampers restore in finally)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa

from glasshouse import cli
from glasshouse.commit import (
    MODEL_FILE,
    VIEWS_SCHEMA,
    Committed,
    GlasshouseClient,
    apply_views,
    models,
)
from glasshouse.compute.curves import HourlyCurve
from glasshouse.compute.marking import register_curve_version, value_trade
from glasshouse.compute.store import CurveStore
from glasshouse.projections import catch_up
from glasshouse.verify import verify
from tests.support import BINARY, DB, needs_live_stack, provision

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
T0 = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

pytestmark = [needs_live_stack, pytest.mark.usefixtures("cli_binary")]


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    return provision()


@pytest.fixture(scope="module")
def morpholog(engine: sa.Engine) -> GlasshouseClient:
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    return client


@pytest.fixture(scope="module")
def store(engine: sa.Engine) -> CurveStore:
    return CurveStore(engine)


@pytest.fixture(scope="module")
def monday(morpholog: GlasshouseClient, engine: sa.Engine, store: CurveStore) -> None:
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=BOOK),
    ):
        assert isinstance(morpholog.submit(grant, actor="bootstrap"), Committed)
    assert isinstance(
        morpholog.submit(
            models.CaptureTradeRequest(
                org=ORG,
                book=BOOK,
                trade="T-001",
                counterparty="stadtwerk-x",
                market=MARKET,
                direction="buy",
                quantity=Decimal("10"),
                price=Decimal("86.25"),
                delivery_start=T0,
                delivery_end=T0 + dt.timedelta(hours=3),
            ),
            actor="alice",
        ),
        Committed,
    )
    assert isinstance(
        register_curve_version(
            morpholog,
            store,
            actor="carol",
            org=ORG,
            market=MARKET,
            as_of=dt.date(2026, 6, 8),
            version="crv-v1",
            curve=HourlyCurve(
                tuple(
                    (T0 + dt.timedelta(hours=i), p)
                    for i, p in enumerate(map(Decimal, ["90", "88", "86.25"]))
                )
            ),
        ),
        Committed,
    )
    assert isinstance(
        value_trade(morpholog, store, actor="risk-engine", org=ORG, book=BOOK, trade="T-001"),
        Committed,
    )
    catch_up(morpholog, engine)
    # The official inspection model is part of the provisioned substrate
    # the views leg checks (init created the governed schema it reads).
    apply_views(engine)


def _leg(report: Any, name: str) -> Any:
    (leg,) = [leg for leg in report.legs if leg.name == name]
    return leg


def test_a_consistent_stack_verifies_with_five_ok_legs(
    monday: None,
    morpholog: GlasshouseClient,
    engine: sa.Engine,
    store: CurveStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    catch_up(morpholog, engine)  # current from any prior state
    report = verify(morpholog, engine, store)
    assert report.ok, report.render()
    assert [leg.name for leg in report.legs] == [
        "model",
        "ledger",
        "projections",
        "payloads",
        "views",
    ]

    # And through the CLI seam, with the verdict as the exit code.
    assert cli.main(["verify", "--database-url", DB]) == 0
    out = capsys.readouterr().out
    assert "glasshouse verify: consistent" in out


def test_a_dropped_inspection_model_fails_the_views_leg(
    monday: None, morpholog: GlasshouseClient, engine: sa.Engine, store: CurveStore
) -> None:
    catch_up(morpholog, engine)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA IF EXISTS morpholog_views CASCADE"))
    try:
        report = verify(morpholog, engine, store)
        assert not report.ok
        leg = _leg(report, "views")
        assert not leg.ok
        assert "not applied" in leg.detail
        assert _leg(report, "ledger").ok  # the legs are independent
    finally:
        apply_views(engine)  # CREATE OR REPLACE: re-application restores it


def test_a_dropped_single_view_fails_the_views_leg(
    monday: None, morpholog: GlasshouseClient, engine: sa.Engine, store: CurveStore
) -> None:
    # The catalogue survives but one view is gone: the hash check alone
    # would still pass, so the inventory check is what catches it.
    catch_up(morpholog, engine)
    with engine.begin() as connection:
        connection.execute(sa.text(f'DROP VIEW "{VIEWS_SCHEMA}".trade_terms'))
    try:
        report = verify(morpholog, engine, store)
        leg = _leg(report, "views")
        assert not leg.ok
        assert "trade_terms" in leg.detail
        assert _leg(report, "ledger").ok  # the legs are independent
    finally:
        apply_views(engine)  # CREATE OR REPLACE restores the dropped view


def test_a_tampered_payload_fails_the_payload_leg(
    monday: None, morpholog: GlasshouseClient, engine: sa.Engine, store: CurveStore
) -> None:
    catch_up(morpholog, engine)
    tamper = sa.text(
        "UPDATE curve_payload_period SET price = price + :delta "
        "WHERE curve_version = 'crv-v1' AND org = :org "
        "AND period_start = (SELECT min(period_start) FROM curve_payload_period "
        "WHERE curve_version = 'crv-v1' AND org = :org)"
    )
    with engine.begin() as connection:
        connection.execute(tamper, {"org": ORG, "delta": 1})
    try:
        report = verify(morpholog, engine, store)
        assert not report.ok
        assert not _leg(report, "payloads").ok
        assert "acme-energy/crv-v1" in _leg(report, "payloads").detail
        assert _leg(report, "ledger").ok  # the legs are independent
        assert cli.main(["verify", "--database-url", DB]) == 1
    finally:
        with engine.begin() as connection:
            connection.execute(tamper, {"org": ORG, "delta": -1})


def test_missing_and_orphaned_payloads_are_told_apart(
    monday: None, morpholog: GlasshouseClient, engine: sa.Engine, store: CurveStore
) -> None:
    catch_up(morpholog, engine)
    # An orphan (content no claim anchors) is a warning, not divergence.
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO curve_payload_period (org, curve_version, period_start, price) "
                "VALUES (:org, 'crv-orphan', :start, 50)"
            ),
            {"org": ORG, "start": T0},
        )
    try:
        report = verify(morpholog, engine, store)
        leg = _leg(report, "payloads")
        assert leg.ok
        assert "orphaned payloads" in leg.detail
        assert "acme-energy/crv-orphan" in leg.detail
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM curve_payload_period WHERE curve_version = 'crv-orphan'")
            )

    # A registered curve whose payload vanished IS divergence.
    with engine.begin() as connection:
        snapshot = connection.execute(
            sa.text(
                "SELECT org, curve_version, period_start, price "
                "FROM curve_payload_period WHERE curve_version = 'crv-v1'"
            )
        ).fetchall()
        connection.execute(
            sa.text("DELETE FROM curve_payload_period WHERE curve_version = 'crv-v1'")
        )
    try:
        report = verify(morpholog, engine, store)
        leg = _leg(report, "payloads")
        assert not leg.ok
        assert "missing payload: acme-energy/crv-v1" in leg.detail
    finally:
        with engine.begin() as connection:
            for row in snapshot:
                connection.execute(
                    sa.text(
                        "INSERT INTO curve_payload_period "
                        "(org, curve_version, period_start, price) "
                        "VALUES (:org, :version, :start, :price)"
                    ),
                    {
                        "org": row.org,
                        "version": row.curve_version,
                        "start": row.period_start,
                        "price": row.price,
                    },
                )


def test_a_tampered_projection_fails_the_projection_leg(
    monday: None, morpholog: GlasshouseClient, engine: sa.Engine, store: CurveStore
) -> None:
    catch_up(morpholog, engine)
    tamper = sa.text("UPDATE blotter_trade SET quantity = quantity + :delta WHERE trade = 'T-001'")
    with engine.begin() as connection:
        connection.execute(tamper, {"delta": 1})
    try:
        report = verify(morpholog, engine, store)
        assert not report.ok
        leg = _leg(report, "projections")
        assert not leg.ok
        assert "blotter_trade: 1 missing, 1 unexpected" in leg.detail
        assert _leg(report, "payloads").ok
    finally:
        with engine.begin() as connection:
            connection.execute(tamper, {"delta": -1})
