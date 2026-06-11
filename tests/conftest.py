"""Shared fixtures.

`cli_binary` pins the CLI's binary discovery at the test stack's
morpholog for integration modules that drive `cli.main` (the CLI builds
its own client, and CI has no morpholog on PATH). Modules opt in with
`pytest.mark.usefixtures("cli_binary")` in their `pytestmark`.
"""

from collections.abc import Iterator

import pytest

from tests.support import BINARY


@pytest.fixture(scope="module")
def monkeypatch_module() -> Iterator[pytest.MonkeyPatch]:
    patcher = pytest.MonkeyPatch()
    yield patcher
    patcher.undo()


@pytest.fixture(scope="module")
def cli_binary(monkeypatch_module: pytest.MonkeyPatch) -> None:
    monkeypatch_module.setenv("GLASSHOUSE_MORPHOLOG_BIN", str(BINARY))
