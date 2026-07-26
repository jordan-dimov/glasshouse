"""Settings that a deployment gets wrong once and pays for nightly.

The writer-role assertion is one of those: it is empty on every
self-hosted database (where the audit tail's resume horizon reads all
sessions and is sound unaided) and load-bearing on managed PostgreSQL
(where the platform's hidden sessions make that horizon uncomputable, so
the tail refuses rather than risk skipping a transition). It is
therefore configured in exactly one place per deployment, by an operator
typing into a hosting dashboard - so both spellings parse, and the value
must actually reach the client the API builds.
"""

import pytest

from glasshouse.api.deps import build_client
from glasshouse.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("app_user", ["app_user"]),
        ("app_user,importer", ["app_user", "importer"]),
        (" app_user , importer ", ["app_user", "importer"]),  # dashboard whitespace
        ('["app_user", "importer"]', ["app_user", "importer"]),  # JSON still works
        ("", []),
    ],
)
def test_writer_roles_parse_from_a_dashboard_or_from_json(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_AUDIT_WRITER_ROLES", raw)
    assert Settings().audit_writer_roles == expected


def test_unset_means_no_assertion() -> None:
    assert Settings().audit_writer_roles == []


def test_the_api_client_carries_the_configured_assertion() -> None:
    # The API's projector thread and audit screen tail the ledger through
    # this client; if the setting stopped here the demo would be dark.
    settings = Settings(audit_writer_roles=["app_user"])
    assert build_client(settings).writer_roles == ["app_user"]
    assert build_client(Settings()).writer_roles is None
