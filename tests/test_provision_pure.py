"""The provision command's deterministic legs: the report renders
stably (with and without the least-privilege floor), the missing-bundle
preflight refuses before any database work, and the CLI threads its
flag through.
"""

from pathlib import Path

import pytest

from glasshouse import cli
from glasshouse.commit.morpholog_client.envelopes import LeastPrivilege
from glasshouse.provision import (
    ProvisionError,
    ProvisionReport,
    alembic_config,
)

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


def test_the_report_renders_stably() -> None:
    plain = ProvisionReport(governed="initialised", least_privilege=None)
    assert plain.render() == (
        "provisioned: app schema at head, governed schema initialised, views applied"
    )
    floored = ProvisionReport(
        governed="already-initialised",
        least_privilege=LeastPrivilege(
            next_steps=("GRANT morpholog_writer TO app_user;",),
            reader_role="morpholog_reader",
            writer_role="morpholog_writer",
        ),
    )
    rendered = floored.render()
    assert "governed schema already-initialised" in rendered
    assert "reader morpholog_reader, writer morpholog_writer" in rendered
    # The membership grants are printed for the operator, never executed.
    assert "next: GRANT morpholog_writer TO app_user;" in rendered


def test_a_missing_migration_bundle_refuses_before_any_database_work(tmp_path: Path) -> None:
    with pytest.raises(ProvisionError, match=r"alembic\.ini"):
        alembic_config(DEAD_DB, ini=tmp_path / "absent" / "alembic.ini")


def test_the_cli_threads_the_flag_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_run(database_url: str, *, least_privilege: bool = False) -> ProvisionReport:
        calls.append((database_url, least_privilege))
        return ProvisionReport(governed="initialised", least_privilege=None)

    monkeypatch.setattr(cli, "run_provision", fake_run)
    assert cli.main(["provision", "--database-url", DEAD_DB]) == 0
    assert cli.main(["provision", "--least-privilege", "--database-url", DEAD_DB]) == 0
    assert calls == [(DEAD_DB, False), (DEAD_DB, True)]
    assert "provisioned: app schema at head" in capsys.readouterr().out
