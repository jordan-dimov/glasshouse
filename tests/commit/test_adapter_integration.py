"""The adapter against the real binary and a disposable database: the
worked trade lifecycle, end to end, typed.

Skips cleanly (CI has no morpholog) unless a local morpholog checkout and
a disposable database are reachable. The run path commits: the morpholog
schema in the test database is dropped and recreated every run.

    GLASSHOUSE_MORPHOLOG_REPO   default ~/dev/morpholog
    GLASSHOUSE_TEST_DATABASE_URL default postgres:///morpholog_scratch
"""

import datetime as dt
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from glasshouse.commit import (
    Committed,
    GateRejection,
    MorphologAdapter,
    NamedArgs,
    Rejected,
    RejectedVerdict,
)

REPO = Path(os.environ.get("GLASSHOUSE_MORPHOLOG_REPO", "~/dev/morpholog")).expanduser()
DB = os.environ.get("GLASSHOUSE_TEST_DATABASE_URL", "postgres:///morpholog_scratch")
BINARY = REPO / "target" / "release" / "morpholog"
MODEL = REPO / "examples" / "10_trade_lifecycle" / "trade_lifecycle.morph"
SCHEMA_SQL = REPO / "crates" / "morpholog-core" / "sql" / "schema.sql"


def _database_reachable() -> bool:
    try:
        ok = subprocess.run(["psql", DB, "-qc", "select 1"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ok.returncode == 0


pytestmark = pytest.mark.skipif(
    not (BINARY.exists() and MODEL.exists() and _database_reachable()),
    reason=f"needs a morpholog checkout at {REPO} and a database at {DB}",
)


@pytest.fixture(scope="module")
def morpholog() -> MorphologAdapter:
    # Disposable by contract: recreate the morpholog schema for a
    # reproducible run (provisioning from the binary is an upstream ask).
    subprocess.run(["psql", DB, "-qc", "DROP SCHEMA IF EXISTS morpholog CASCADE"], check=True)
    subprocess.run(["psql", DB, "-qf", str(SCHEMA_SQL)], check=True)
    return MorphologAdapter(model_file=MODEL, database_url=DB, binary=str(BINARY))


def test_the_needle_lifecycle(morpholog: MorphologAdapter) -> None:
    captured = morpholog.run(
        "capture_trade",
        actor="trader",
        args={
            "trade": "t1",
            "commodity": "power",
            "direction": "buy",
            "version_id": "v1",
            "quantity": Decimal("100"),
            "delivery_period": "2026Q4",
            "captured_on": dt.date(2026, 6, 1),
            "price": Decimal("45.20"),
        },
    )
    assert isinstance(captured, Committed)
    terms = next(c for c in captured.asserted_claims if c.predicate == "TradeTerms")
    assert terms.args[2] == Decimal("100")
    assert terms.args[4] == dt.date(2026, 6, 1)

    # A duplicate capture is a lawful rejection, not an error.
    duplicate = morpholog.run(
        "capture_trade",
        actor="trader",
        args={
            "trade": "t1",
            "commodity": "power",
            "direction": "buy",
            "version_id": "v2",
            "quantity": Decimal("100"),
            "delivery_period": "2026Q4",
            "captured_on": dt.date(2026, 6, 1),
            "price": Decimal("45.20"),
        },
    )
    assert isinstance(duplicate, Rejected)

    # Diagnosis before action: settlement is refused for a named reason,
    # and the explanation names the transformations that would cure it.
    verdict = morpholog.explain(
        "settle_trade",
        actor="middle_office",
        args={
            "trade": "t1",
            "settled_qty": Decimal("60"),
            "settlement_id": "s1",
            "official_price_id": "op1",
            "effective_on": dt.date(2026, 12, 31),
        },
    )
    assert isinstance(verdict.verdict, RejectedVerdict)
    detail = verdict.verdict.rejected
    assert isinstance(detail, GateRejection)
    assert any(
        "confirm_trade" in m.candidate_supplier_transformations
        for m in detail.directly_missing_claims
    )

    # Confirm, then correct: the official price supersedes, never erases.
    steps: tuple[tuple[str, NamedArgs], ...] = (
        (
            "grant_confirm_authority",
            {"principal": "middle_office", "commodity": "power"},
        ),
        (
            "confirm_trade",
            {
                "trade": "t1",
                "counterparty": "acme",
                "confirmation_id": "c1",
                "official_price_id": "op1",
                "confirmed_price": Decimal("45.20"),
            },
        ),
        (
            "correct_official_price",
            {
                "trade": "t1",
                "prior_official_price_id": "op1",
                "new_official_price_id": "op2",
                "corrected_price": Decimal("46.00"),
            },
        ),
    )
    for transformation, args in steps:
        outcome = morpholog.run(transformation, actor="middle_office", args=args)
        assert isinstance(outcome, Committed), (transformation, outcome)

    # Read back by name: the in-force pointer moved, both figures stand.
    (pointer,) = morpholog.read_claims("CurrentOfficialPrice")
    assert pointer == {"trade": "t1", "official_price_id": "op2"}
    figures = {
        row["official_price_id"]: row["price"] for row in morpholog.read_claims("OfficialPrice")
    }
    assert figures == {"op1": Decimal("45.20"), "op2": Decimal("46.00")}

    # As-of the capture transition, the correction has not happened yet.
    as_of = str(captured.transition_id)
    assert morpholog.read_claims("CurrentOfficialPrice", as_of=as_of) == []
    assert morpholog.read_claims("TradeCaptured", as_of=as_of) == [
        {"trade": "t1", "commodity": "power", "direction": "buy"}
    ]

    # An unknown predicate is a true zero, not an error.
    assert morpholog.inspect_claims("NoSuchPredicate") == []
