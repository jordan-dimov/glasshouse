"""The read endpoints' deterministic legs: org is required, and a dead
database is a 503 (not a 500). The populated 200s live in the env-gated
integration leg, where there is a ledger to read.

The database URL is deliberately dead so these verdicts hold whatever is
running locally; the binary is never reached (reads do not touch it).
"""

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"

ENDPOINTS = ("/trades", "/positions", "/valuations", "/overview")


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_org_is_required(endpoint: str) -> None:
    # The tenancy boundary is structural (law 6): a read with no org is a
    # malformed request, refused before any database work.
    with TestClient(create_app()) as client:
        response = client.get(endpoint)
    assert response.status_code == 422


@pytest.mark.parametrize("endpoint", [*ENDPOINTS, "/orgs"])
def test_a_dead_database_is_503_not_500(endpoint: str) -> None:
    # The projection tables are a cache of the ledger; their
    # unavailability is a readiness verdict, never an internal error.
    with TestClient(create_app()) as client:
        response = client.get(endpoint, params={"org": "acme-energy"})
    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 501}, {"offset": -1}],
)
def test_pagination_bounds_are_refused(params: dict[str, int]) -> None:
    # Malformed pagination is a 422 before any database work: the dead
    # database would otherwise make this a 503.
    with TestClient(create_app()) as client:
        response = client.get("/trades", params={"org": "acme-energy", **params})
    assert response.status_code == 422
