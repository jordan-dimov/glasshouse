"""The adapter's discrimination rule, exercised against a fake binary:
envelopes on stdout become typed outcomes whatever the exit code; empty
stdout raises with the stderr text; the named-args encoding reaches the
CLI exactly as the schema expects."""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from glasshouse.commit import Committed, MorphologAdapter, MorphologOperationalError, Rejected
from tests.commit import envelopes


def fake_binary(tmp_path: Path, *, stdout: str = "", stderr: str = "", exit_code: int = 0) -> Path:
    """A stand-in morpholog: records its argv, plays back a canned reply."""
    script = tmp_path / "fake-morpholog"
    out, err = tmp_path / "stdout.json", tmp_path / "stderr.txt"
    out.write_text(stdout)
    err.write_text(stderr)
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" > "{tmp_path}/argv.txt"\n'
        f'cat "{out}"\ncat "{err}" >&2\nexit {exit_code}\n'
    )
    script.chmod(0o755)
    return script


def adapter(binary: Path) -> MorphologAdapter:
    return MorphologAdapter(
        model_file=Path("model.morph"), database_url="postgres:///x", binary=str(binary)
    )


def test_committed_envelope_with_exit_zero(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, stdout=envelopes.COMMITTED_CAPTURE)
    outcome = adapter(binary).run("capture_trade", actor="trader", args={"trade": "t2"})
    assert isinstance(outcome, Committed)


def test_rejected_envelope_with_exit_one_is_an_outcome_not_an_error(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, stdout=envelopes.REJECTED_DUPLICATE, exit_code=1)
    outcome = adapter(binary).run("capture_trade", actor="trader", args={"trade": "t2"})
    assert isinstance(outcome, Rejected)


def test_empty_stdout_raises_with_the_stderr_text(tmp_path: Path) -> None:
    binary = fake_binary(
        tmp_path, stderr="Error: parameter `quantity` is Decimal but ...", exit_code=1
    )
    with pytest.raises(MorphologOperationalError, match="parameter `quantity`"):
        adapter(binary).run("capture_trade", actor="trader", args={"trade": "t2"})


def test_non_json_stdout_raises(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, stdout="not json at all")
    with pytest.raises(MorphologOperationalError, match="non-JSON stdout"):
        adapter(binary).run("capture_trade", actor="trader", args={"trade": "t2"})


def test_missing_binary_raises(tmp_path: Path) -> None:
    with pytest.raises(MorphologOperationalError, match="could not run"):
        adapter(tmp_path / "no-such-binary").inspect_predicates()


def test_named_args_reach_the_cli_in_wire_form(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, stdout=envelopes.COMMITTED_CAPTURE)
    adapter(binary).run(
        "capture_trade",
        actor="trader",
        args={"quantity": Decimal("100.5"), "captured_on": dt.date(2026, 6, 7)},
    )
    argv = (tmp_path / "argv.txt").read_text().splitlines()
    assert argv[0] == "run"
    assert argv[argv.index("--actor") + 1] == "trader"
    assert json.loads(argv[argv.index("--args-named") + 1]) == {
        "quantity": "100.5",
        "captured_on": "2026-06-07",
    }
