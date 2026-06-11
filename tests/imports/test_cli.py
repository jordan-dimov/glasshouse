"""The CLI seam: argument wiring, the report on stdout, exit codes by
contract (0 = file processed whatever the per-row outcomes; 1 = the file
itself refused or an operational failure)."""

from pathlib import Path

import pytest

from glasshouse import cli
from tests.imports.test_trades import HEADER, MIXED, RECEIPTS, fake_binary


def test_import_trades_prints_the_report_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(fake_binary(tmp_path, RECEIPTS)))
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(MIXED)

    code = cli.main(
        [
            "import-trades",
            str(csv_file),
            "--org",
            "acme-energy",
            "--actor",
            "alice",
            "--database-url",
            "postgres:///x",
        ]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert "5 processed: 1 committed, 1 rejected, 0 error, 3 quarantined" in out
    assert "line 2: tr-1" in out


def test_a_file_that_breaks_the_contract_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(fake_binary(tmp_path, RECEIPTS)))
    csv_file = tmp_path / "trades.csv"
    csv_file.write_text(HEADER.replace("price", "cost") + "\n")

    code = cli.main(
        ["import-trades", str(csv_file), "--org", "o", "--actor", "a", "--database-url", "x"]
    )

    assert code == 1
    assert "missing: price" in capsys.readouterr().err


def test_a_missing_file_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["import-trades", str(tmp_path / "nope.csv"), "--org", "o", "--actor", "a"])
    assert code == 1
    assert "error:" in capsys.readouterr().err
