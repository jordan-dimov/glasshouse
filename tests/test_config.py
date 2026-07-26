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
from pydantic import ValidationError

from glasshouse.api.deps import build_client
from glasshouse.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("app_user", ["app_user"]),
        ("app_user,importer", ["app_user", "importer"]),
        (" app_user , importer ", ["app_user", "importer"]),  # dashboard whitespace
        ('["app_user", "importer"]', ["app_user", "importer"]),  # JSON still works
        ('[" app_user ", "importer"]', ["app_user", "importer"]),  # normalised the same way
        ("", []),
        (",", []),
    ],
)
def test_writer_roles_parse_from_a_dashboard_or_from_json(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("GLASSHOUSE_AUDIT_WRITER_ROLES", raw)
    assert Settings().audit_writer_roles == expected


@pytest.mark.parametrize("bad", ["[app_user", '[{"role": "app_user"}]', "[1, 2]"])
def test_a_broken_json_spelling_is_refused_at_boot(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    # Anything opening with a bracket is JSON and is held to it: broken
    # syntax, or a list of things that are not role names, fails at
    # settings construction. A value that does not open with a bracket is
    # a comma-separated list of names by definition, so there is nothing
    # to be strict about there - a name that is not a role is caught by
    # the substrate's own catalogue census, which knows the roles.
    monkeypatch.setenv("GLASSHOUSE_AUDIT_WRITER_ROLES", bad)
    with pytest.raises(ValidationError):
        Settings()


def test_unset_means_no_assertion() -> None:
    assert Settings().audit_writer_roles == []


def test_the_api_client_carries_the_configured_assertion() -> None:
    # The API's projector thread and audit screen tail the ledger through
    # this client; if the setting stopped here the demo would be dark.
    settings = Settings(audit_writer_roles=["app_user"])
    assert build_client(settings).writer_roles == ["app_user"]
    assert build_client(Settings()).writer_roles is None
