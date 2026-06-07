"""The needle against the real binary: one trade, one official curve,
one MTM only admissible against an official curve, one correction that
supersedes, one as-of query - driven end to end through the generated
typed models on a disposable database.

Skips cleanly (CI has no morpholog) unless a binary and a disposable
database are reachable. The run path commits: the morpholog schema in
the test database is dropped every run and re-provisioned through the
binary itself.

    GLASSHOUSE_MORPHOLOG_REPO   default ~/dev/morpholog (for the binary)
    GLASSHOUSE_TEST_DATABASE_URL default postgres:///morpholog_scratch
"""

import datetime as dt
import subprocess
from decimal import Decimal

import pytest
from pydantic import ValidationError

from glasshouse.commit import (
    MODEL_FILE,
    Committed,
    GateRejection,
    MorphologAdapter,
    MorphologOperationalError,
    Quantity,
    Rejected,
    RejectedVerdict,
)
from glasshouse.commit.generated import (
    MODEL_HASH,
    AdmitValuation,
    CaptureTrade,
    CorrectCurve,
    GrantCaptureAuthority,
    GrantCurveAuthority,
    GrantValuationAuthority,
    OfficialCurveClaim,
    RegisterCurve,
    TradeTermsClaim,
)
from tests.support import BINARY, DB, needs_live_stack

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
AS_OF = dt.date(2026, 6, 8)


pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def morpholog() -> MorphologAdapter:
    # Disposable by contract: drop so the lifecycle test can prove
    # provisioning through the binary (`init` is day-zero only).
    subprocess.run(["psql", DB, "-qc", "DROP SCHEMA IF EXISTS morpholog CASCADE"], check=True)
    return MorphologAdapter(model_file=MODEL_FILE, database_url=DB, binary=str(BINARY))


def test_the_needle_lifecycle(morpholog: MorphologAdapter) -> None:
    # Day zero: the binary provisions the exact schema its build expects.
    assert morpholog.init() is True
    with pytest.raises(MorphologOperationalError, match="already exists"):
        morpholog.init()
    assert morpholog.init(skip_if_exists=True) is False

    # The closed loop: the .morph on disk, the committed manifest, and
    # the generated models all name the same rules.
    assert morpholog.model_hash() == MODEL_HASH

    # Authority is governed capability claims, granted by transitions.
    for grant in (
        GrantCaptureAuthority(principal="alice", org=ORG, book=BOOK),
        GrantCurveAuthority(principal="carol", org=ORG, market=MARKET),
        GrantValuationAuthority(principal="risk-engine", org=ORG, book=BOOK),
    ):
        outcome = morpholog.propose(grant, actor="bootstrap")
        assert isinstance(outcome, Committed), outcome

    # Law 9 is typed: a naive delivery instant cannot even construct.
    with pytest.raises(ValidationError):
        CaptureTrade(
            org=ORG,
            book=BOOK,
            trade="T-bad",
            counterparty="stadtwerk-x",
            market=MARKET,
            direction="buy",
            quantity=Decimal("10"),
            price=Decimal("86.25"),
            delivery_start=dt.datetime(2026, 7, 1),  # naive
            delivery_end=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        )

    capture = CaptureTrade(
        org=ORG,
        book=BOOK,
        trade="T-001",
        counterparty="stadtwerk-x",
        market=MARKET,
        direction="buy",
        quantity=Decimal("10"),
        price=Decimal("86.25"),
        delivery_start=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        delivery_end=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )

    # Without the capability claim, capture is a lawful rejection.
    unauthorised = morpholog.propose(capture, actor="mallory")
    assert isinstance(unauthorised, Rejected)

    captured = morpholog.propose(capture, actor="alice")
    assert isinstance(captured, Committed)
    assert {c.predicate for c in captured.asserted_claims} == {"TradeCaptured", "TradeTerms"}
    # The self-describing tagged codec spells the unit out; named reads
    # return the bare amount because the declaration fixes it.
    terms = next(c for c in captured.asserted_claims if c.predicate == "TradeTerms")
    assert terms.args[2] == Quantity(Decimal("10"), "MW")

    # A duplicate capture is refused, carrying its own same-snapshot
    # explanation.
    duplicate = morpholog.propose(capture, actor="alice", explain_on_reject=True)
    assert isinstance(duplicate, Rejected)
    assert duplicate.explanation is not None

    # Register the official curve; the bulk price array lives in the
    # app schema, anchored by this hash.
    registered = morpholog.propose(
        RegisterCurve(
            org=ORG, market=MARKET, as_of=AS_OF, version="crv-v1", payload_hash="sha256:aaaa"
        ),
        actor="carol",
    )
    assert isinstance(registered, Committed)

    # The headline rule: MTM is only admissible against the official
    # curve. Against an unknown version, the rejection explains exactly
    # which claim is missing and which transformations could supply it.
    refused = morpholog.propose(
        AdmitValuation(
            org=ORG, book=BOOK, trade="T-001", curve_version="crv-v0", mtm=Decimal("99")
        ),
        actor="risk-engine",
        explain_on_reject=True,
    )
    assert isinstance(refused, Rejected)
    assert refused.explanation is not None
    assert isinstance(refused.explanation.verdict, RejectedVerdict)
    detail = refused.explanation.verdict.rejected
    assert isinstance(detail, GateRejection)
    assert any(
        {"register_curve", "correct_curve"} <= set(m.candidate_supplier_transformations)
        for m in detail.directly_missing_claims
    )

    valued = morpholog.propose(
        AdmitValuation(
            org=ORG,
            book=BOOK,
            trade="T-001",
            curve_version="crv-v1",
            mtm=Decimal("-1250.50"),
        ),
        actor="risk-engine",
    )
    assert isinstance(valued, Committed)

    # Correct the curve: v2 supersedes v1, the official pointer moves,
    # v1 and its valuation stay on the record.
    corrected = morpholog.propose(
        CorrectCurve(
            org=ORG,
            market=MARKET,
            as_of=AS_OF,
            prior_version="crv-v1",
            new_version="crv-v2",
            payload_hash="sha256:bbbb",
        ),
        actor="carol",
    )
    assert isinstance(corrected, Committed)
    assert [c.predicate for c in corrected.retracted_claims] == ["OfficialCurve"]

    # MTM against the superseded version is now structurally refused;
    # against the new official version it is admissible.
    stale = morpholog.propose(
        AdmitValuation(org=ORG, book=BOOK, trade="T-001", curve_version="crv-v1", mtm=Decimal("0")),
        actor="risk-engine",
    )
    assert isinstance(stale, Rejected)
    revalued = morpholog.propose(
        AdmitValuation(
            org=ORG,
            book=BOOK,
            trade="T-001",
            curve_version="crv-v2",
            mtm=Decimal("-1180.00"),
        ),
        actor="risk-engine",
    )
    assert isinstance(revalued, Committed)

    # Typed reads: the generated models parse wire-true strings into
    # Decimal, date and aware datetime.
    (official,) = morpholog.read(OfficialCurveClaim)
    assert official.version == "crv-v2"
    assert official.as_of == AS_OF
    (term_row,) = morpholog.read(TradeTermsClaim)
    assert term_row.quantity == Decimal("10")  # bare amount; the declaration fixes MW
    assert term_row.price == Decimal("86.25")
    assert term_row.delivery_start == dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

    # As-of the registration transition, v1 was the official curve.
    (was_official,) = morpholog.read(OfficialCurveClaim, as_of=str(registered.transition_id))
    assert was_official.version == "crv-v1"

    # The two read authorities: bare reads answer an unknown predicate
    # with a true zero; named reads refuse it before touching the
    # database.
    assert morpholog.inspect_claims("NoSuchPredicate") == []
    with pytest.raises(MorphologOperationalError, match="not declared"):
        morpholog.read_claims("NoSuchPredicate")
