"""The provision command's deterministic legs: the report renders
stably (with and without the least-privilege floor), the missing-bundle
preflight refuses before any database work, and the CLI threads its
flag and this deployment's writer-role assertion through.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from glasshouse import cli, provision
from glasshouse.commit import MorphologError
from glasshouse.commit.morpholog_client.envelopes import LeastPrivilege
from glasshouse.provision import (
    ProvisionError,
    ProvisionReport,
    alembic_config,
)
from tests.support import fake_binary

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
    roles: list[list[str] | None] = []

    def fake_run(
        database_url: str,
        *,
        least_privilege: bool = False,
        writer_roles: list[str] | None = None,
    ) -> ProvisionReport:
        calls.append((database_url, least_privilege))
        roles.append(writer_roles)
        return ProvisionReport(governed="initialised", least_privilege=None)

    monkeypatch.setattr(cli, "run_provision", fake_run)
    monkeypatch.setenv("GLASSHOUSE_AUDIT_WRITER_ROLES", "app_user")
    assert cli.main(["provision", "--database-url", DEAD_DB]) == 0
    assert cli.main(["provision", "--least-privilege", "--database-url", DEAD_DB]) == 0
    assert calls == [(DEAD_DB, False), (DEAD_DB, True)]
    # The pre-deploy command proves the audit tail is readable, so it
    # needs the same assertion the running services use - configured in
    # one place, never re-typed per command.
    assert roles == [["app_user"], ["app_user"]]
    assert "provisioned: app schema at head" in capsys.readouterr().out


def test_a_ledger_that_cannot_be_tailed_fails_the_deploy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The outage this preflight exists for, in miniature. On managed
    # PostgreSQL a misconfigured writer assertion makes every ledger read
    # refuse while the service still comes up healthy; provisioning is
    # the pre-deploy command, so the refusal belongs HERE, where a deploy
    # fails loudly with the substrate's own remedy in the message.
    refusing = fake_binary(
        tmp_path,
        "",
        stderr=(
            "Error: opening the audit tail\n\nCaused by:\n    1 session(s) in "
            "pg_stat_activity are hidden from this role, so a lossless audit resume "
            "horizon cannot be computed"
        ),
        exit_code=1,
    )
    monkeypatch.setattr(
        provision, "command", SimpleNamespace(upgrade=lambda config, revision: None)
    )
    monkeypatch.setattr(provision, "apply_views", lambda engine: None)
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(refusing))

    with pytest.raises(MorphologError, match="hidden from this role"):
        provision.run_provision(DEAD_DB, writer_roles=["app_user"])

    # And the CLI turns it into exit 1 with the remedy on stderr, not a
    # traceback and not a silent success.
    monkeypatch.setattr(cli, "run_provision", provision.run_provision)
    assert cli.main(["provision", "--database-url", DEAD_DB]) == 1
    assert "hidden from this role" in capsys.readouterr().err
