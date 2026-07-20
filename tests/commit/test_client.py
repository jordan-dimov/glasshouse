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
    # The one bridge back in the sliver: the binary's `--views-schema`
    # (the sealed view surface) is not on the generated verify() yet, so
    # our override carries it and the report's views verdict decodes.
    report_json = json.dumps(
        {
            "replay": {"status": "consistent", "transitions": 1, "claims": 1},
            "tree": {"status": "intact", "checkpoints": 0, "tree_size": 0},
            "views": {"status": "intact", "views_checked": 10},
        }
    )
    binary = fake_binary(tmp_path, report_json)
    client = GlasshouseClient("model.morph", "postgres:///x", binary=str(binary))
    report = client.verify(views_schema="morpholog_views")
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
        }
    )
    client = GlasshouseClient(
        "model.morph", "postgres:///x", binary=str(fake_binary(tmp_path, report_json))
    )
    assert client.verify().views is None
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert "--views-schema" not in argv


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
        client.verify()
    assert "s3cr3t" not in str(caught.value)


def test_a_driver_echoing_the_conninfo_in_stderr_is_redacted(tmp_path: Path) -> None:
    # The scarier case the redaction is for: a database driver echoes the
    # full connection string in stderr. The generated client masks it
    # there too, so the credential reaches neither a log nor a client.
    secret = "postgresql://user:s3cr3t@db:5432/x"
    binary = fake_binary(tmp_path, "", stderr=f"FATAL: could not connect to {secret}", exit_code=1)
    client = GlasshouseClient("model.morph", secret, binary=str(binary))
    with pytest.raises(MorphologError) as caught:
        client.verify()
    assert "s3cr3t" not in str(caught.value)
    assert "<redacted>" in str(caught.value)
