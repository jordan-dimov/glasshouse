"""What the API's own documentation promises: the filters most reads
share are declared once and appear on each address, and the error
answers a caller can really get are listed beside the 200, not left for
them to discover.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from glasshouse.api.app import create_app

DEAD_DB = "postgresql://127.0.0.1:1/nowhere"


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)
        patch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/nonexistent/morpholog")
        patch.delenv("GLASSHOUSE_DEMO_PASSWORD", raising=False)
        patch.delenv("GLASSHOUSE_MCP_TOKEN", raising=False)
        with TestClient(create_app()) as client:
            document: dict[str, Any] = client.get("/openapi.json").json()
    return document


def parameters(spec: dict[str, Any], path: str) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in spec["paths"][path]["get"]["parameters"]}


@pytest.mark.parametrize("path", ["/trades", "/positions", "/valuations"])
def test_the_shared_filters_are_on_every_read_that_takes_them(
    spec: dict[str, Any], path: str
) -> None:
    declared = parameters(spec, path)
    assert {"org", "book", "market", "as_of"} <= set(declared)
    assert declared["org"]["required"] is True
    assert declared["book"]["required"] is False
    # Described once, in one place, and the description reaches each.
    assert declared["org"]["description"] == "the organisation the answer is about"


def test_each_read_keeps_its_own_parameters(spec: dict[str, Any]) -> None:
    assert {"limit", "offset"} <= set(parameters(spec, "/trades"))
    assert {"start", "end"} <= set(parameters(spec, "/positions"))
    assert {"trade", "latest"} <= set(parameters(spec, "/valuations"))


@pytest.mark.parametrize("path", ["/trades", "/positions", "/valuations"])
def test_the_as_of_reads_say_how_they_can_refuse(spec: dict[str, Any], path: str) -> None:
    responses = spec["paths"][path]["get"]["responses"]
    assert {"200", "409", "422", "503"} <= set(responses)
    assert "not final yet" in responses["409"]["description"]
    assert responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/Problem")


def test_the_other_addresses_list_their_real_errors(spec: dict[str, Any]) -> None:
    paths = spec["paths"]
    assert {"404", "409", "422", "503"} <= set(paths["/curves/diff"]["get"]["responses"])
    assert "404" in paths["/trades/{trade}/terms"]["get"]["responses"]
    assert "502" in paths["/explain"]["post"]["responses"]
    for path in ("/audit", "/refusals", "/instructions", "/curves", "/markets"):
        assert "503" in paths[path]["get"]["responses"], path


def test_a_read_still_refuses_a_missing_organisation(spec: dict[str, Any]) -> None:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GLASSHOUSE_DATABASE_URL", DEAD_DB)
        patch.setenv("GLASSHOUSE_MORPHOLOG_BIN", "/nonexistent/morpholog")
        patch.delenv("GLASSHOUSE_DEMO_PASSWORD", raising=False)
        with TestClient(create_app()) as client:
            missing = client.get("/trades", params={"book": "spec-de"})
            bad_limit = client.get("/trades", params={"org": "x", "limit": 0})
    assert missing.status_code == 422
    assert bad_limit.status_code == 422
