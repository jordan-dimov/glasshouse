"""The hand-written sliver of the commit zone: `GlasshouseClient.read`,
the typed per-predicate read with `--as-of` that bridges the generated
client's gap (see client.py), exercised against a fake binary."""

import datetime as dt
import json
from pathlib import Path

import pytest

from glasshouse.commit import GlasshouseClient, models

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


def fake_binary(tmp_path: Path, stdout: str) -> Path:
    """A stand-in morpholog: records its argv, plays back a canned reply."""
    script = tmp_path / "fake-morpholog"
    (tmp_path / "stdout.json").write_text(stdout)
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/argv.txt"\n'
        f'cat "{tmp_path}/stdout.json"\nexit 0\n'
    )
    script.chmod(0o755)
    return script


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
