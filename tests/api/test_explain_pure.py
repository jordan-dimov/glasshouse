"""`/explain` against a canned binary: the admissible verdict, a gate
rejection flattened onto the wire, and an operational failure as a 502.

The fake morpholog plays back a golden `explain --json` envelope, so
these legs pin the endpoint's shape without a live binary or database
(the integration leg drives the real one). The database URL is dead and
never reached: explain runs the binary, which here ignores it.
"""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"

_TRANSITION = {"transformation": "capture_trade", "args": [], "actor": "alice"}

ADMISSIBLE = json.dumps({"transition": _TRANSITION, "verdict": "admissible"})

GATE_REJECTED = json.dumps(
    {
        "transition": dict(_TRANSITION, actor="bob"),
        "verdict": {
            "rejected": {
                "kind": "gate",
                "gate": "MayCaptureTrade(actor, org, book)",
                "statement_kind": "require",
                "directly_missing_claims": [
                    {
                        "predicate": "MayCaptureTrade",
                        "rendered": "MayCaptureTrade(bob, acme-energy, spec-de)",
                        "candidate_supplier_transformations": ["grant_capture_authority"],
                    }
                ],
            }
        },
    }
)


INVARIANT_REJECTED = json.dumps(
    {
        "transition": _TRANSITION,
        "verdict": {
            "rejected": {
                "kind": "invariant",
                "name": "trade_terms_unique_by_trade",
                "rule": "TradeTerms unique by (trade)",
            }
        },
    }
)

ERROR_REJECTED = json.dumps(
    {
        "transition": _TRANSITION,
        "verdict": {"rejected": {"kind": "error", "message": "unknown transformation"}},
    }
)


@pytest.fixture(autouse=True)
def deterministic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)


def _post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: str, **kw: object
) -> httpx.Response:
    binary = fake_binary(tmp_path, stdout, **kw)  # type: ignore[arg-type]
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(binary))
    with TestClient(create_app()) as client:
        response: httpx.Response = client.post(
            "/explain",
            json={"transformation": "capture_trade", "args": {}},
            headers={"X-Actor": "bob"},
        )
    return response


def test_admissible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _post(tmp_path, monkeypatch, ADMISSIBLE)
    assert response.status_code == 200
    assert response.json() == {"admissible": True, "rejection": None}


def test_gate_rejection_is_flattened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _post(tmp_path, monkeypatch, GATE_REJECTED)
    assert response.status_code == 200
    body = response.json()
    assert body["admissible"] is False
    rejection = body["rejection"]
    assert rejection["kind"] == "gate"
    assert rejection["gate"] == "MayCaptureTrade(actor, org, book)"
    ((missing,),) = (rejection["directly_missing_claims"],)
    assert missing["predicate"] == "MayCaptureTrade"
    assert missing["candidate_supplier_transformations"] == ["grant_capture_authority"]


def test_invariant_rejection_is_flattened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _post(tmp_path, monkeypatch, INVARIANT_REJECTED)
    assert response.status_code == 200
    rejection = response.json()["rejection"]
    assert rejection == {
        "kind": "invariant",
        "name": "trade_terms_unique_by_trade",
        "rule": "TradeTerms unique by (trade)",
    }


def test_error_rejection_is_flattened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _post(tmp_path, monkeypatch, ERROR_REJECTED)
    assert response.status_code == 200
    assert response.json()["rejection"] == {"kind": "error", "message": "unknown transformation"}


def test_actor_header_is_required() -> None:
    # X-Actor is the L0 identity the gate evaluates against; absent, the
    # request is malformed and refused at the boundary, no binary reached.
    with TestClient(create_app()) as client:
        response = client.post("/explain", json={"transformation": "capture_trade", "args": {}})
    assert response.status_code == 422


def test_operational_failure_is_502_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Empty stdout with a non-zero exit is the commit layer's "raise"
    # discriminator: an upstream dependency failure, surfaced as a 502.
    # The detail is generic - the underlying message can carry the
    # database URL, so it is logged server-side, never reflected.
    response = _post(tmp_path, monkeypatch, "", stderr="boom", exit_code=1)
    assert response.status_code == 502
    assert response.json() == {"detail": "explain could not be evaluated"}
