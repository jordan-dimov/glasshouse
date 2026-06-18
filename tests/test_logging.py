"""The operational-logging contract: events go to stderr (stdout is the
CLI's data channel), hosted deployments render JSON lines and local
development a console line, and reconfiguring never strands a captured
stream (the closed-handle regression that pytest's per-test capture would
otherwise trip)."""

import json
from collections.abc import Iterator

import pytest
import structlog

from glasshouse.config import Settings
from glasshouse.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    yield
    # Leave global structlog state clean so a shuffled run order cannot
    # carry one test's renderer into another's expectations.
    structlog.reset_defaults()


def test_dev_logs_a_console_line_to_stderr_not_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(Settings(environment="dev"))
    get_logger("glasshouse.test").info("test.event", answer=42)

    captured = capsys.readouterr()
    assert "test.event" in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("environment", ["demo", "production"])
def test_hosted_logs_json_to_stderr(environment: str, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(Settings(environment=environment))  # type: ignore[arg-type]
    get_logger("glasshouse.test").info("test.event", answer=42)

    captured = capsys.readouterr()
    event = json.loads(captured.err)
    assert event["event"] == "test.event"
    assert event["answer"] == 42
    assert event["level"] == "info"
    assert captured.out == ""


def test_reconfiguring_does_not_pin_a_stale_stream(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(Settings(environment="production"))
    get_logger("glasshouse.test").info("first")
    capsys.readouterr()

    # A logger that had cached the first call's stderr would raise
    # "I/O operation on closed file" here; per-call resolution does not.
    configure_logging(Settings(environment="production"))
    get_logger("glasshouse.test").info("second")
    assert "second" in capsys.readouterr().err
