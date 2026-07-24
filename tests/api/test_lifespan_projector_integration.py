"""The demo profile end to end: with the web service running in the
demo environment, a governed write becomes visible on the read side with
NO manual catch-up - the lifespan's background projector carries it.
Self-provisioned module slate.
"""

import datetime as dt
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
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
