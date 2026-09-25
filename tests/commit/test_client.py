"""The hand-written sliver of the commit zone, exercised against a fake
binary: `GlasshouseClient.read` (the typed per-predicate as-of read),
binary discovery under `GLASSHOUSE_MORPHOLOG_BIN`, and that our
constructor wires the operation timeout and credential redaction through
to the now-generated client (the bridges that used to live here are gone
- the generated client carries them, byte-pinned and drift-checked)."""

import datetime as dt
import json
from pathlib import Path

import pytest

from glasshouse.commit import GlasshouseClient, MorphologError, envelopes, models
from tests.support import fake_binary

NAMED_OFFICIAL_CURVE = json.dumps(
    [
        {
            "predicate": "OfficialCurve",
            "args": {
                "org": "acme-energy",
                "market": "de-power",
                "as_of": "2026-06-08",
                "version": "crv-v1",
            },
        }
    ]
)


def client(tmp_path: Path) -> GlasshouseClient:
    binary = fake_binary(tmp_path, NAMED_OFFICIAL_CURVE)
    return GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))


def test_read_decodes_by_declared_kind_through_the_named_surface(tmp_path: Path) -> None:
    (row,) = client(tmp_path).read(models.OfficialCurveClaim)
    assert row.version == "crv-v1"
    assert row.as_of == dt.date(2026, 6, 8)  # a date, not wire text
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["inspect", "claims"]
    assert argv[argv.index("--predicate") + 1] == "OfficialCurve"
    assert argv[argv.index("--named") + 1] == "model.morph"
    assert "--as-of" not in argv


def test_read_as_of_reaches_the_cli(tmp_path: Path) -> None:
    client(tmp_path).read(models.OfficialCurveClaim, as_of="0197-transition-id")
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[argv.index("--as-of") + 1] == "0197-transition-id"


def test_verify_passes_the_views_schema_flag_through(tmp_path: Path) -> None:
    # The sealed view surface: `--views-schema` reaches the binary and
    # the report's views verdict decodes. Generated since upstream #197,
    # so this now pins the surface we depend on rather than a bridge.
    report_json = json.dumps(
        {
            "replay": {"status": "consistent", "transitions": 1, "claims": 1},
            "tree": {"status": "intact", "checkpoints": 0, "tree_size": 0},
            "role_rebindings": {"status": "not_evaluated"},
            "views": {"status": "intact", "views_checked": 10},
        }
    )
    binary = fake_binary(tmp_path, report_json)
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    report = client.audit_verify(views_schema="morpholog_views")
    assert report.views == envelopes.ViewsIntact(views_checked=10)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[argv.index("--views-schema") + 1] == "morpholog_views"


def test_verify_without_the_flag_matches_the_generated_call(tmp_path: Path) -> None:
    # No flag, no verdict: the plain call stays byte-compatible with the
    # generated body it mirrors.
    report_json = json.dumps(
        {
            "replay": {"status": "consistent", "transitions": 1, "claims": 1},
            "tree": {"status": "intact", "checkpoints": 0, "tree_size": 0},
            "role_rebindings": {"status": "not_evaluated"},
        }
    )
    client = GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(fake_binary(tmp_path, report_json))
    )
    assert client.audit_verify().views is None
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert "--views-schema" not in argv


CHECKPOINT = json.dumps(
    {
        "status": "created",
        "tree_size": 3,
        "checkpoint_hash": "sha256:beef",
        "prev_checkpoint_hash": None,
        "root_hash": "sha256:cafe",
    }
)


@pytest.mark.parametrize("call", ["audit", "audit_named"])
def test_the_configured_writer_roles_reach_every_audit_tail(tmp_path: Path, call: str) -> None:
    # The assertion is deployment configuration, so a call site that says
    # nothing about writer roles still makes it: on managed PostgreSQL a
    # tail without it refuses, and forgetting it at one call site would
    # pass every self-hosted test.
    binary = fake_binary(tmp_path, "")
    client = GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(binary), writer_roles=["app_user", "importer"]
    )
    assert getattr(client, call)() == []
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--writer-role"] == [
        "app_user",
        "importer",
    ]


def test_the_configured_writer_roles_reach_both_checkpoint_paths(tmp_path: Path) -> None:
    # `audit_checkpoint` shares the audit tail's resume horizon, and
    # `write_checkpoint` builds its own argv - both carry the assertion,
    # and both must name the same subcommand the binary now answers to.
    #
    # The subcommand assertion is not incidental. Our overrides carry the
    # deployment's writer-role assertion, so when upstream v0.0.8 gathered
    # the family under `morpholog audit`, the renamed base left our
    # `checkpoint` override overriding nothing: a dead method, the flag
    # silently unsent, and mypy content. Pinning the argv here is what
    # makes that a failing pure test rather than a managed-PostgreSQL
    # surprise.
    client = GlasshouseClient(
        "model.morph",
        "postgres:///x",
        binary=str(fake_binary(tmp_path, CHECKPOINT)),
        writer_roles=["app_user"],
    )
    client.audit_checkpoint()
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["audit", "checkpoint"]
    assert argv[argv.index("--writer-role") + 1] == "app_user"

    anchor = tmp_path / "anchor.json"
    client.write_checkpoint(anchor)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:2] == ["audit", "checkpoint"]
    assert argv[argv.index("--writer-role") + 1] == "app_user"
    assert json.loads(anchor.read_text())["tree_size"] == 3


INDEX_PLAN = (
    "program: glasshouse (sha256:7045)\n"
    "CREATE               morpholog_ci_tradeterms_0_vk1_2785  TradeTerms[0] value_key_v1_digest\n"
    "KEEP                 morpholog_ci_tradeterms_1_vk1_4298  TradeTerms[1] value_key_v1_digest\n"
    "SATISFIED EXTERNALLY morpholog_ci_tradevalued_0_vk1_bd45  TradeValued[0] value_key_v1_digest\n"
    "STALE                morpholog_ci_tradevalued_0_old_dead  TradeValued[0] arg_digest\n"
)


def test_provision_indexes_names_the_subcommand_and_parses_the_plan(tmp_path: Path) -> None:
    # The one hand-built argv left (the generator emits no method for
    # `provision indexes`), pinned so a regrouping upstream is caught by a
    # pure test rather than only in a live one. The plan is text, not
    # JSON, so its parser lives on our side and is held to the binary's
    # own action vocabulary, multi-word actions included.
    binary = fake_binary(tmp_path, INDEX_PLAN + "applied\n")
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    plan = client.provision_indexes(prune=True)
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[:3] == ["provision", "indexes", "model.morph"]
    assert "--prune" in argv
    assert "--dry-run" not in argv
    assert plan.applied
    assert [(a.action, a.index.split("_")[2]) for a in plan.actions] == [
        ("CREATE", "tradeterms"),
        ("KEEP", "tradeterms"),
        ("SATISFIED EXTERNALLY", "tradevalued"),
        ("STALE", "tradevalued"),
    ]
    assert plan.summary() == "1 keep, 1 create, 1 satisfied externally, 1 stale"


def test_a_dry_run_plan_is_not_applied(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, INDEX_PLAN + "dry run: nothing changed\n")
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    plan = client.provision_indexes(dry_run=True)
    assert "--dry-run" in (tmp_path / "argv.txt").read_text().splitlines()
    assert not plan.applied


def test_an_unrecognised_plan_line_is_drift_not_silence(tmp_path: Path) -> None:
    # A new action word upstream must not be counted as nothing: the
    # plan is refused by name, the way the generated envelopes refuse an
    # unknown key.
    binary = fake_binary(tmp_path, "program: x\nREBUILD  morpholog_ci_x  X[0]\napplied\n")
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    with pytest.raises(MorphologError, match="not a provision plan line"):
        client.provision_indexes()


def test_an_index_conflict_raises_with_the_plan(tmp_path: Path) -> None:
    # Exit non-zero is the binary's "an operator must decide"; the plan
    # it printed is the evidence, so it rides in the message.
    binary = fake_binary(
        tmp_path,
        "program: x\nCONFLICT             morpholog_ci_x_0_vk1_00  X[0] value_key_v1_digest\n",
        stderr="Error: 1 conflict",
        exit_code=1,
    )
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    with pytest.raises(MorphologError, match="CONFLICT"):
        client.provision_indexes()


def test_without_configured_roles_the_horizon_stays_the_blessed_default(tmp_path: Path) -> None:
    # A self-hosted database computes the horizon over every session and
    # is sound without help: no flag must appear, and an explicit
    # per-call assertion still wins.
    binary = fake_binary(tmp_path, "")
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    client.audit()
    assert "--writer-role" not in (tmp_path / "argv.txt").read_text().splitlines()
    client.audit(writer_roles=["one_off"])
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[argv.index("--writer-role") + 1] == "one_off"


def test_an_explicit_empty_assertion_turns_the_configured_one_off(tmp_path: Path) -> None:
    # None means "this deployment's assertion"; an empty list keeps the
    # generated meaning, no flag at all. Without the distinction a caller
    # could not ask for the all-sessions horizon on a configured client -
    # and would silently get the configured one back instead.
    client = GlasshouseClient(
        "model.morph",
        "postgres:///x",
        binary=str(fake_binary(tmp_path, "")),
        writer_roles=["app_user"],
    )
    client.audit(writer_roles=[])
    assert "--writer-role" not in (tmp_path / "argv.txt").read_text().splitlines()


def test_binary_discovery_honours_the_glasshouse_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One name across app, docs and commit zone: GLASSHOUSE_MORPHOLOG_BIN
    # wins when no binary is passed; an explicit argument still wins over
    # the environment.
    monkeypatch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/opt/glasshouse/morpholog")
    assert GlasshouseClient("m.morph", "postgres:///x").binary == "/opt/glasshouse/morpholog"
    assert GlasshouseClient("m.morph", "postgres:///x", binary="explicit").binary == "explicit"
    monkeypatch.delenv("GLASSHOUSE_MORPHOLOG_BIN")
    monkeypatch.setenv("MORPHOLOG_BIN", "/usr/local/bin/morpholog")
    assert GlasshouseClient("m.morph", "postgres:///x").binary == "/usr/local/bin/morpholog"


def test_our_constructor_wires_the_operation_timeout(tmp_path: Path) -> None:
    # timeout_seconds flows through __init__ to the generated client,
    # which bounds the call - a hung binary becomes a fast verdict.
    sleeper = tmp_path / "fake-morpholog"
    sleeper.write_text("#!/bin/sh\nsleep 5\n")
    sleeper.chmod(0o755)
    bounded = GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(sleeper), timeout_seconds=0.1
    )
    with pytest.raises(MorphologError, match=r"timed out after 0\.1"):
        bounded.hash()


def test_our_client_redacts_the_database_url_in_errors(tmp_path: Path) -> None:
    # The generated client masks the --database-url argument in raised
    # messages; this proves an operational failure on a client we
    # constructed (with our real conninfo) never leaks the credential.
    binary = fake_binary(tmp_path, "", stderr="connection refused", exit_code=1)
    secret = "postgresql://user:s3cr3t@db/x"
    client = GlasshouseClient("model.morph", secret, binary=str(binary))
    with pytest.raises(MorphologError) as caught:
        client.audit_verify()
    assert "s3cr3t" not in str(caught.value)


def test_a_driver_echoing_the_conninfo_in_stderr_is_redacted(tmp_path: Path) -> None:
    # The scarier case the redaction is for: a database driver echoes the
    # full connection string in stderr. The generated client masks it
    # there too, so the credential reaches neither a log nor a client.
    secret = "postgresql://user:s3cr3t@db:5432/x"
    binary = fake_binary(tmp_path, "", stderr=f"FATAL: could not connect to {secret}", exit_code=1)
    client = GlasshouseClient("model.morph", secret, binary=str(binary))
    with pytest.raises(MorphologError) as caught:
        client.audit_verify()
    assert "s3cr3t" not in str(caught.value)
    assert "<redacted>" in str(caught.value)
