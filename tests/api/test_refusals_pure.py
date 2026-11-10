"""The refusal log's read, with a fake binary: every answer carries what
the log is and how far back it reaches, a refusal whose values were not
captured says so rather than claiming there were none, and the same read
serves the API and the Audit screen.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glasshouse.api import refusals
from glasshouse.api.app import create_app
from glasshouse.commit import GlasshouseClient
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


def row(rule: str, actor: str, at: str) -> dict[str, object]:
    return {
        "rejection_id": f"r-{rule}-{actor}",
        "transformation_name": "capture_trade",
        "arguments": [],
        "actor": {"type": "subject", "value": actor},
        "kind": "require",
        "rule": rule,
        "reason": f"require `{rule}` failed",
        "rejected_at": at,
    }


ROWS = [
    row("hour_within_limit", "agent", "2026-07-01T10:05:00Z"),
    row("may_capture_trade", "alice", "2026-07-01T10:00:00Z"),
]


def client(tmp_path: Path, rows: list[dict[str, object]]) -> GlasshouseClient:
    binary = fake_binary(tmp_path, json.dumps(rows))
    return GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))


def test_an_answer_says_what_the_log_is_and_how_far_back_it_reaches(tmp_path: Path) -> None:
    log = refusals.list_refusals(client(tmp_path, ROWS), org="acme-energy", limit=5)
    assert "not the audit history" in log.caveat
    assert "one can be missing" in log.caveat
    assert log.limit == 5
    assert [r.rule for r in log.refusals] == ["hour_within_limit", "may_capture_trade"]
    # The binary is asked for that organisation's refusals and that many.
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["inspect", "rejections"]
    assert argv[argv.index("--mentions") + 1] == "acme-energy"
    assert argv[argv.index("--limit") + 1] == "5"


@pytest.mark.parametrize(
    ("asked", "bound"), [(0, 1), (-3, 1), (7, 7), (10_000, refusals.MAX_LIMIT)]
)
def test_the_bound_is_kept_within_reason(tmp_path: Path, asked: int, bound: int) -> None:
    log = refusals.list_refusals(client(tmp_path, []), org="acme-energy", limit=asked)
    assert log.limit == bound
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[argv.index("--limit") + 1] == str(bound)


def test_values_that_were_not_captured_are_not_turned_into_none_at_all(tmp_path: Path) -> None:
    (first, _) = refusals.list_refusals(client(tmp_path, ROWS), org="acme-energy").refusals
    # No witness in the row: "not captured", never an empty list, which
    # would say the rule was reading nothing.
    assert first.witness is None
    assert first.compared is None
    # With no figures, the line falls back to the rule and its reason.
    assert first.summary == "hour_within_limit: require `hour_within_limit` failed"


def test_an_actor_narrows_the_rows_read(tmp_path: Path) -> None:
    log = refusals.list_refusals(client(tmp_path, ROWS), org="acme-energy", actor="alice")
    assert [r.actor for r in log.refusals] == ["alice"]


def test_the_api_and_the_screen_show_the_same_log_with_its_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = fake_binary(tmp_path, json.dumps(ROWS))
    monkeypatch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(binary))
    with TestClient(create_app()) as app:
        answer = app.get("/refusals", params={"org": "acme-energy", "limit": 2})
        too_many = app.get("/refusals", params={"org": "acme-energy", "limit": 100_000})
    assert answer.status_code == 200
    body = answer.json()
    assert body["limit"] == 2
    assert "not the audit history" in body["caveat"]
    assert [r["rule"] for r in body["refusals"]] == ["hour_within_limit", "may_capture_trade"]
    assert body["refusals"][0]["witness"] is None
    assert too_many.status_code == 422  # a bound is asked for, not assumed
