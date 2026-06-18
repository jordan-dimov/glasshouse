"""The all-ok readiness verdict against the real stack: binary present
and speaking, database answering, and the commit layer agreeing through
a governed read on a provisioned ledger."""

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from glasshouse.commit import MODEL_FILE, GlasshouseClient
from tests.support import BINARY, DB, needs_live_stack, provision

pytestmark = needs_live_stack


def test_readyz_is_200_when_everything_agrees(monkeypatch: pytest.MonkeyPatch) -> None:
    provision()
    client = GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))
    assert client.init().status == "initialised"

    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    with TestClient(create_app()) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"morpholog": "ok", "database": "ok", "commit": "ok"}
