"""The needle against the real binary: one trade, one official curve,
one MTM only admissible against an official curve, one correction that
supersedes, one as-of query - driven end to end through the generated
client on a disposable database.

Skips cleanly (CI has no morpholog) unless a binary and a disposable
database are reachable. The run path commits: the morpholog schema in
the test database is dropped every run and re-provisioned through the
binary itself.

    GLASSHOUSE_MORPHOLOG_REPO   default ~/dev/morpholog (for the binary)
    GLASSHOUSE_TEST_DATABASE_URL default postgres:///morpholog_scratch
"""

import datetime as dt
import filecmp
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from glasshouse.commit import (
    MODEL_FILE,
    MODEL_HASH,
    Committed,
    GlasshouseClient,
    MorphologError,
    Rejected,
    models,
    values,
)
from glasshouse.commit.morpholog_client.envelopes import GateRejection
from tests.support import BINARY, DB, needs_live_stack

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"
AS_OF = dt.date(2026, 6, 8)


pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def morpholog() -> GlasshouseClient:
    # Disposable by contract: drop so the lifecycle test can prove
    # provisioning through the binary (`init` is day-zero only).
    subprocess.run(["psql", DB, "-qc", "DROP SCHEMA IF EXISTS morpholog CASCADE"], check=True)
    return GlasshouseClient(str(MODEL_FILE), DB, binary=str(BINARY))


def test_the_committed_client_is_what_the_binary_generates(tmp_path: Path) -> None:
    # The drift contract: the committed package is byte-identical to
    # what the live binary generates from the committed .morph. (CI has
    # no binary, so this leg lives here, next to the hash assertion.)
    proc = subprocess.run(
        [str(BINARY), "generate", "python-client", str(MODEL_FILE), "--out", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    committed_pkg = MODEL_FILE.parent / "morpholog_client"
    regenerated = tmp_path / "morpholog_client"
    names = sorted(p.name for p in committed_pkg.glob("*.py"))
    assert sorted(p.name for p in regenerated.glob("*.py")) == names
    _, mismatch, errors = filecmp.cmpfiles(committed_pkg, regenerated, names, shallow=False)
    assert (mismatch, errors) == ([], []), f"regenerate the client: {mismatch + errors}"


def test_the_needle_lifecycle(morpholog: GlasshouseClient) -> None:
    # Day zero: the binary provisions the exact schema its build expects.
    assert morpholog.init().status == "initialised"
    with pytest.raises(MorphologError, match="already exists"):
        morpholog.init()
    assert morpholog.init(skip_if_exists=True).status == "already-initialised"

    # The closed loop: the .morph on disk, the committed client and the
    # live binary all name the same rules.
    assert morpholog.hash().hash == MODEL_HASH

    # The authoring lint tier rides the same leg: the disciplined model
    # is finding-free even under --strict.
    assert morpholog.check(strict=True).diagnostics == []

    # Authority is governed capability claims, granted by transitions.
    for grant in (
        models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK),
        models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET),
        models.GrantValuationAuthorityRequest(principal="risk-engine", org=ORG, book=BOOK),
    ):
        outcome = morpholog.submit(grant, actor="bootstrap")
        assert isinstance(outcome, Committed), outcome

    # Law 9 at the codec: an instant must carry its offset, so a naive
    # delivery instant is refused before it can reach the wire.
    naive = models.CaptureTradeRequest(
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
    with pytest.raises(values.CodecError, match="naive"):
        naive.to_args_named()

    capture = models.CaptureTradeRequest(
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
    unauthorised = morpholog.submit(capture, actor="mallory")
    assert isinstance(unauthorised, Rejected)

    captured = morpholog.submit(capture, actor="alice")
    assert isinstance(captured, Committed)
    assert {c.predicate for c in captured.asserted_claims} == {"TradeCaptured", "TradeTerms"}
    # Tagged quantities decode to the bare exact amount: the declaration
    # fixes the unit, the wire never re-states it.
    terms = next(c for c in captured.asserted_claims if c.predicate == "TradeTerms")
    assert terms.args[2] == Decimal("10")

    # A duplicate capture is refused, carrying its own same-snapshot
    # explanation.
    duplicate = morpholog.submit(capture, actor="alice", explain_on_reject=True)
    assert isinstance(duplicate, Rejected)
    assert duplicate.explanation is not None

    # Register the official curve; the bulk price array lives in the
    # app schema, anchored by this hash.
    registered = morpholog.submit(
        models.RegisterCurveRequest(
            org=ORG, market=MARKET, as_of=AS_OF, version="crv-v1", payload_hash="sha256:aaaa"
        ),
        actor="carol",
    )
    assert isinstance(registered, Committed)

    # The headline rule: MTM is only admissible against the official
    # curve. Against an unknown version, the rejection explains exactly
    # which claim is missing and which transformations could supply it.
    refused = morpholog.submit(
        models.AdmitValuationRequest(
            org=ORG, book=BOOK, trade="T-001", curve_version="crv-v0", mtm=Decimal("99")
        ),
        actor="risk-engine",
        explain_on_reject=True,
    )
    assert isinstance(refused, Rejected)
    assert refused.explanation is not None
    assert not refused.explanation.admissible
    rejection = refused.explanation.rejection
    assert isinstance(rejection, GateRejection)
    assert any(
        {"register_curve", "correct_curve"} <= set(m.candidate_supplier_transformations)
        for m in rejection.directly_missing_claims
    )

    valued = morpholog.submit(
        models.AdmitValuationRequest(
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
    corrected = morpholog.submit(
        models.CorrectCurveRequest(
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
    stale = morpholog.submit(
        models.AdmitValuationRequest(
            org=ORG, book=BOOK, trade="T-001", curve_version="crv-v1", mtm=Decimal("0")
        ),
        actor="risk-engine",
    )
    assert isinstance(stale, Rejected)
    revalued = morpholog.submit(
        models.AdmitValuationRequest(
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
    (official,) = morpholog.read(models.OfficialCurveClaim)
    assert official.version == "crv-v2"
    assert official.as_of == AS_OF
    (term_row,) = morpholog.read(models.TradeTermsClaim)
    assert term_row.quantity == Decimal("10")  # bare amount; the declaration fixes MW
    assert term_row.price == Decimal("86.25")
    assert term_row.delivery_start == dt.datetime(2026, 7, 1, tzinfo=dt.UTC)

    # As-of the registration transition, v1 was the official curve.
    (was_official,) = morpholog.read(models.OfficialCurveClaim, as_of=registered.transition_id)
    assert was_official.version == "crv-v1"

    # The two read authorities: bare reads answer an unknown predicate
    # with a true zero; named reads refuse it before touching the
    # database.
    assert morpholog.claims("NoSuchPredicate") == []
    with pytest.raises(MorphologError, match="not declared"):
        morpholog.claims_named("NoSuchPredicate")
