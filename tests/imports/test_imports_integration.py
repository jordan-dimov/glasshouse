"""The import path against the real binary and a real database, through
the CLI entry point: a trades file with a quarantined row and an
in-file duplicate (a lawful rejection), then a curves file with one
unbuildable curve, then the same curves file again (the payload store's
immutability stops re-registration before the ledger is asked).

Same gates and provisioning contract as the other integration legs."""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from glasshouse import cli
from glasshouse.commit import MODEL_FILE, Committed, GlasshouseClient, models
from tests.support import BINARY, DB, needs_live_stack, provision

ORG, BOOK, MARKET = "acme-energy", "spec-de", "de-power"

TRADES = "\n".join(
    [
        "book,trade,counterparty,market,direction,quantity,price,delivery_start,delivery_end",
        f"{BOOK},T-1,stadtwerk-x,{MARKET},buy,10,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",
        f"{BOOK},T-2,stadtwerk-x,{MARKET},sell,5,84.00,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",
        f"{BOOK},T-3,stadtwerk-x,{MARKET},long,5,84.00,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",
        f"{BOOK},T-1,stadtwerk-x,{MARKET},buy,10,86.25,2026-07-01T00:00:00Z,2026-07-02T00:00:00Z",
    ]
)

CURVES = "\n".join(
    [
        "market,as_of,version,period_start,price",
        f"{MARKET},2026-06-08,crv-mon,2026-07-01T00:00:00Z,90",
        f"{MARKET},2026-06-08,crv-mon,2026-07-01T01:00:00Z,88",
        f"{MARKET},2026-06-09,crv-tue,2026-07-01T00:00:00Z,91",
        f"{MARKET},2026-06-09,crv-tue,2026-07-01T01:00:00Z,89",
        f"{MARKET},2026-06-10,crv-gap,2026-07-01T00:00:00Z,90",
        f"{MARKET},2026-06-10,crv-gap,2026-07-01T02:00:00Z,88",
    ]
)


pytestmark = needs_live_stack


@pytest.fixture(scope="module", autouse=True)
def provisioned(monkeypatch_module: pytest.MonkeyPatch) -> None:
    provision()
    monkeypatch_module.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
    client = GlasshouseClient(str(MODEL_FILE), DB)
    assert client.init().status == "initialised"
    for grant, actor in (
        (models.GrantCaptureAuthorityRequest(principal="alice", org=ORG, book=BOOK), "bootstrap"),
        (models.GrantCurveAuthorityRequest(principal="carol", org=ORG, market=MARKET), "bootstrap"),
    ):
        assert isinstance(client.submit(grant, actor=actor), Committed)


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    patcher = pytest.MonkeyPatch()
    yield patcher
    patcher.undo()


def _run(args: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    assert cli.main([*args, "--database-url", DB]) == 0
    return capsys.readouterr().out


def test_trades_import_partial_admission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(TRADES)
    out = _run(
        ["import-trades", str(csv_file), "--org", ORG, "--actor", "alice", "--project"], capsys
    )

    # 2 committed, the in-file duplicate lawfully rejected with the
    # gate's why attached, the bad direction quarantined - and the
    # exit code was 0 throughout.
    assert "2 committed" in out
    assert "1 rejected" in out
    assert "1 quarantined" in out
    # The duplicate trips a negative gate (nothing is "missing");
    # the why names the gate that refused.
    assert "gate not TradeCaptured" in out
    # The inline projector mode rode the same invocation. The count is
    # whatever was unprojected (this module makes no ordering promises),
    # so the claim is that the catch-up happened, not its size.
    assert re.search(r"projected: applied \d+ transition\(s\)", out)

    client = GlasshouseClient(str(MODEL_FILE), DB)
    captured = {row.trade for row in client.read(models.TradeCapturedClaim)}
    assert captured == {"T-1", "T-2"}


def test_preview_as_an_ungranted_actor_refuses_everything_and_commits_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The workbench's validate step, ledger-true: mallory holds no
    # capability claims, so every honest row is refused with the
    # missing grant named and its supplier suggested - and the ledger
    # is untouched.
    csv_file = tmp_path / "preview.csv"
    csv_file.write_text(TRADES)
    out = _run(
        ["import-trades", str(csv_file), "--org", ORG, "--actor", "mallory", "--preview"], capsys
    )
    assert "3 refused" in out
    assert "1 quarantined" in out
    assert "missing MayCaptureTrade(mallory" in out
    assert "supplied by grant_capture_authority" in out

    client = GlasshouseClient(str(MODEL_FILE), DB)
    assert all(row.trade != "T-3" for row in client.read(models.TradeCapturedClaim))


def test_curves_preview_dry_runs_registration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No payload is stored by a preview; an ungranted actor is refused
    # with the missing grant named.
    csv_file = tmp_path / "curves-preview.csv"
    csv_file.write_text(
        "\n".join(
            [
                "market,as_of,version,period_start,price",
                f"{MARKET},2026-06-12,crv-preview,2026-07-01T00:00:00Z,92",
                f"{MARKET},2026-06-12,crv-preview,2026-07-01T01:00:00Z,90",
            ]
        )
    )
    out = _run(
        ["import-curves", str(csv_file), "--org", ORG, "--actor", "mallory", "--preview"], capsys
    )
    assert "1 refused" in out
    assert "missing MayRegisterCurve(mallory" in out

    client = GlasshouseClient(str(MODEL_FILE), DB)
    assert all(row.version != "crv-preview" for row in client.read(models.CurveRegisteredClaim))


def test_curves_import_and_the_immutable_rerun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_file = tmp_path / "curves.csv"
    csv_file.write_text(CURVES)
    args = ["import-curves", str(csv_file), "--org", ORG, "--actor", "carol"]

    out = _run(args, capsys)
    assert "3 processed: 2 committed, 1 quarantined" in out
    assert "crv-gap: " in out
    assert "contiguous" in out

    client = GlasshouseClient(str(MODEL_FILE), DB)
    registered = {row.version for row in client.read(models.CurveRegisteredClaim)}
    # Subset, not equality: other tests in this module register their
    # own curves, and order is not promised.
    assert {"crv-mon", "crv-tue"} <= registered

    # Re-running the same file: the payload store refuses overwrites
    # before the ledger is asked, and the report says so per curve.
    rerun = _run(args, capsys)
    assert "2 error" in rerun
    assert "immutable" in rerun


def test_a_second_version_for_an_official_curve_is_a_lawful_rejection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Self-seeded: register an official curve for this test's own as-of
    # date, then import a NEW version for the same (org, market, as-of).
    # The payload stores (new identity), the ledger refuses - the honest
    # move is a correction, and the report says so.
    def curve_csv(version: str) -> Path:
        csv_file = tmp_path / f"{version}.csv"
        csv_file.write_text(
            "\n".join(
                [
                    "market,as_of,version,period_start,price",
                    f"{MARKET},2026-06-11,{version},2026-07-01T00:00:00Z,92",
                    f"{MARKET},2026-06-11,{version},2026-07-01T01:00:00Z,90",
                ]
            )
        )
        return csv_file

    seeded = _run(
        ["import-curves", str(curve_csv("crv-thu")), "--org", ORG, "--actor", "carol"], capsys
    )
    assert "1 processed: 1 committed" in seeded

    out = _run(
        ["import-curves", str(curve_csv("crv-thu-b")), "--org", ORG, "--actor", "carol"], capsys
    )
    assert "1 processed: 1 rejected" in out
