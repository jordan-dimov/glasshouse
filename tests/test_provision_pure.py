"""The provision command's deterministic legs: the report renders
stably (with and without the least-privilege floor), the missing-bundle
preflight refuses before any database work, the CLI threads its flag
through, and a misfit programme or an index conflict fails the deploy.
"""

import json
from pathlib import Path

import pytest

from glasshouse import cli
from glasshouse.commit import GlasshouseClient
from glasshouse.commit.morpholog_client import envelopes
from glasshouse.commit.morpholog_client.envelopes import LeastPrivilege
from glasshouse.provision import (
    ProvisionError,
    ProvisionReport,
    alembic_config,
    fit_check,
    reconcile_indexes,
)
from tests.support import fake_binary

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


def _entry(
    action: str, name: str, *, detail: str | None = None, statistics: bool = False
) -> dict[str, object]:
    """One index (or statistics object) entry of the envelope; the tests
    read only action, name and detail."""
    entry: dict[str, object] = {
        "action": action,
        "name": name,
        "position": 0,
        "required_by": ["glasshouse"],
    }
    if not statistics:
        entry["predicate"] = "TradeTerms"
        # v0.0.16: each index names the rules behind it (morpholog#578).
        entry["needed_by"] = []
    if detail is not None:
        entry["detail"] = detail
    return entry


def provision_envelope(
    *,
    indexes: list[dict[str, object]],
    statistics: list[dict[str, object]] | None = None,
    applied: bool = True,
) -> dict[str, object]:
    """The `provision_report` envelope as the binary prints it (upstream
    embedder-integration, "The deploy sequence")."""
    return {
        "applied": applied,
        "dry_run": False,
        "prune": True,
        "programs": [{"program": "glasshouse", "hash": "sha256:299c"}],
        "indexes": indexes,
        "statistics": statistics or [],
        "required_elsewhere": [],
        "positions_unknown_for": [],
    }


def test_the_report_renders_stably() -> None:
    plain = ProvisionReport(governed="initialised", least_privilege=None)
    assert plain.render() == (
        "provisioned: app schema at head, governed schema initialised, views applied"
    )
    indexed = ProvisionReport(
        governed="already-initialised",
        least_privilege=None,
        governed_migrations=("16", "17"),
        indexes=envelopes.ProvisionReport.from_json(
            provision_envelope(
                indexes=[
                    _entry("create", "morpholog_ci_tradeterms_0"),
                    _entry("create", "morpholog_ci_tradeterms_1"),
                    _entry("stale", "morpholog_ci_tradeterms_2_old", detail="dropped"),
                ],
                statistics=[
                    _entry("keep", "morpholog_cs_vk1_p0", statistics=True),
                    _entry("create", "morpholog_cs_vk1_p1", statistics=True),
                ],
            )
        ),
    )
    assert indexed.render() == (
        "provisioned: app schema at head, governed schema already-initialised, "
        "migrated (16, 17), indexes (2 create, 1 stale; statistics 1 keep, 1 create), "
        "views applied"
    )
    floored = ProvisionReport(
        governed="already-initialised",
        least_privilege=LeastPrivilege(
            executor_role="morpholog_executor",
            next_steps=("GRANT morpholog_writer TO app_user;",),
            reader_role="morpholog_reader",
            writer_role="morpholog_writer",
        ),
    )
    rendered = floored.render()
    assert "governed schema already-initialised" in rendered
    assert (
        "reader morpholog_reader, writer morpholog_writer, executor morpholog_executor" in rendered
    )
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


def test_an_index_conflict_refuses_rather_than_reporting_done(tmp_path: Path) -> None:
    # The generated client hands the report back whatever the exit code
    # and leaves the decision to the caller (upstream: "gate on
    # has_conflict"). A conflict applied nothing and needs an operator,
    # so provisioning refuses by name instead of rendering a report that
    # reads as a completed deploy.
    conflicted = fake_binary(
        tmp_path,
        json.dumps(
            provision_envelope(
                indexes=[
                    _entry("keep", "morpholog_ci_tradeterms_0"),
                    _entry(
                        "conflict",
                        "morpholog_ci_tradeterms_1",
                        detail="an index of this name exists with another definition",
                    ),
                ],
                applied=False,
            )
        ),
        exit_code=1,
    )
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(conflicted))
    with pytest.raises(ProvisionError, match="another definition") as refused:
        reconcile_indexes(client)
    assert "morpholog_ci_tradeterms_1" in str(refused.value)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:3] == ["provision", "indexes", "model.morph"]
    assert "--json" in argv
    assert "--prune" in argv


def test_a_programme_that_does_not_fit_the_ledger_fails_the_deploy(tmp_path: Path) -> None:
    # Upstream's deploy sequence (v0.0.16): `check --strict --database-url`
    # between `migrate` and the index plan, because a programme that does
    # not fit the stored claims answers wrongly, not slowly. The report
    # comes back whatever the exit code; the diagnostic is the binary's
    # own, captured from a probe that redeclared TradeValued.mtm in GBP
    # against a ledger holding EUR, so the deploy refuses by name.
    misfit = fake_binary(
        tmp_path,
        json.dumps(
            {
                "file": "model.morph",
                "diagnostics": [
                    {
                        "severity": "error",
                        "message": (
                            "field `mtm` of predicate `TradeValued` is declared Decimal[GBP], "
                            "but 4 stored claims hold Decimal[EUR] there"
                        ),
                        "start": 6821,
                        "end": 7022,
                        "line": 117,
                        "column": 1,
                    }
                ],
            }
        ),
        exit_code=1,
    )
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(misfit))
    with pytest.raises(ProvisionError, match="does not fit this ledger") as refused:
        fit_check(client)
    assert "4 stored claims hold Decimal[EUR]" in str(refused.value)
    assert "(line 117)" in str(refused.value)
    # The argv is the deploy sequence's own line: strict, and against
    # this client's database, through the generated method.
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv == ["check", "--json", "model.morph", "--strict", "--database-url", "postgres:///x"]


def test_a_programme_that_fits_passes_the_fit_check(tmp_path: Path) -> None:
    fits = fake_binary(
        tmp_path, json.dumps({"file": "model.morph", "diagnostics": [], "invariants": []})
    )
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(fits))
    assert fit_check(client).diagnostics == []


def test_a_clean_plan_is_returned_for_the_report(tmp_path: Path) -> None:
    clean = fake_binary(
        tmp_path,
        json.dumps(provision_envelope(indexes=[_entry("keep", "morpholog_ci_tradeterms_0")])),
    )
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(clean))
    assert reconcile_indexes(client).applied
    # The last call is the gate: the check, after the apply.
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert "--check" in argv
    assert "--prune" not in argv


def test_an_apply_the_check_still_finds_incomplete_fails_the_deploy(tmp_path: Path) -> None:
    # Upstream's deploy sequence (v0.0.15): apply, then `--check` before
    # anything writes. The fake binary answers both calls alike, so the
    # check meets an index still to create and the deploy refuses by name
    # rather than reporting done.
    outstanding = fake_binary(
        tmp_path,
        json.dumps(
            provision_envelope(
                indexes=[
                    _entry("keep", "morpholog_ci_tradeterms_0"),
                    _entry("create", "morpholog_ci_tradeterms_1"),
                ],
                statistics=[_entry("create", "morpholog_cs_vk1_p1", statistics=True)],
            )
        ),
    )
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(outstanding))
    with pytest.raises(ProvisionError, match="check still finds work outstanding") as refused:
        reconcile_indexes(client)
    assert "morpholog_ci_tradeterms_1 (create)" in str(refused.value)
    assert "morpholog_cs_vk1_p1 (create)" in str(refused.value)
    assert "morpholog_ci_tradeterms_0" not in str(refused.value)
