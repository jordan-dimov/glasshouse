"""The demo profile end to end: with the web service running in the
demo environment, a governed write becomes visible on the read side with
NO manual catch-up - the lifespan's background projector carries it.
Self-provisioned module slate.
"""

import datetime as dt
import time
from decimal import Decimal

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from glasshouse.projections.runner import start_projector_thread
from glasshouse.projections.tables import blotter_trade
from glasshouse.seed import ORG as SEED_ORG
from glasshouse.seed import run_seed
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack

ORG, BOOK, MARKET = "thread-demo", "book-t", "de-power"


def test_a_write_becomes_visible_without_manual_catch_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"
    assert isinstance(
        client.submit(
            models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
            actor="bootstrap",
        ),
        Committed,
    )

    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    with TestClient(create_app()) as web:
        # Write through the commit layer while the app is up: only the
        # lifespan's projector thread can make it readable.
        day = dt.datetime(2026, 7, 1, tzinfo=dt.UTC)
        assert isinstance(
            client.submit(
                models.CaptureTradeRequest(
                    org=ORG,
                    book=BOOK,
                    trade="TH-1",
                    counterparty="cp",
                    market=MARKET,
                    direction="buy",
                    quantity=Decimal("1"),
                    price=Decimal("80"),
                    delivery_start=day,
                    delivery_end=day + dt.timedelta(hours=1),
                ),
                actor="alice",
            ),
            Committed,
        )
        deadline = time.monotonic() + 15
        rows: list[dict[str, str]] = []
        while time.monotonic() < deadline:
            rows = web.get("/trades", params={"org": ORG}).json()
            if rows:
                break
            time.sleep(0.2)
        assert [row["trade"] for row in rows] == ["TH-1"]


def test_the_nightly_reset_never_kills_a_live_projector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exact hosted collision: the web process's projector polls every
    # second while the cron drops and rebuilds the world. The reset holds
    # the projector's advisory lock across the destructive window and the
    # thread retries operational failures, so it must come out alive and
    # projecting the reseeded book.
    engine = provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    monkeypatch.setenv("GLASSHOUSE_ENVIRONMENT", "demo")
    thread, stop = start_projector_thread(client, engine, interval_seconds=0.05)
    try:
        run_seed(DB, reset=True)  # drops schemas and tables mid-poll
        assert thread.is_alive(), "the reset must never kill the projector"
        # The surviving thread projects the reseeded book by itself.
        deadline = time.monotonic() + 15
        count = 0
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                count = connection.execute(
                    sa.select(sa.func.count())
                    .select_from(blotter_trade)
                    .where(blotter_trade.c.org == SEED_ORG)
                ).scalar_one()
            if count == 6:
                break
            time.sleep(0.2)
        assert count == 6
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=10)
        engine.dispose()
    assert not thread.is_alive()
