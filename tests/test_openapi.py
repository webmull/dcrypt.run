"""Drift tests between openapi.yaml and the running app.

The spec is hand-written and served verbatim, which means nothing stops it
falling out of step with the code. These tests are that "nothing".
"""

from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi.yaml"


@pytest.fixture(scope="module")
def spec():
    return yaml.safe_load(SPEC_PATH.read_text())


def test_spec_is_valid_yaml_and_versioned(spec):
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "Decrypt the Narrative API"
    assert spec["info"]["version"]


def test_served_spec_is_the_file(client, spec):
    assert client.get("/openapi.json").json() == spec


def test_docs_page_renders(client):
    res = client.get("/docs")
    assert res.status_code == 200
    assert "swagger" in res.text.lower()


# -------------------------------------------------------------
# Route coverage
# -------------------------------------------------------------
def documented_operations(spec):
    return {
        (path, method.upper())
        for path, ops in spec["paths"].items()
        for method in ops
        if method in {"get", "post", "put", "patch", "delete"}
    }


def implemented_operations(api_mod):
    ops = set()
    for route in api_mod.app.routes:
        if isinstance(route, APIRoute) and route.include_in_schema:
            for method in route.methods:
                if method not in {"HEAD", "OPTIONS"}:
                    ops.add((route.path, method))
    return ops


def test_every_endpoint_is_documented(spec, api_mod):
    missing = implemented_operations(api_mod) - documented_operations(spec)
    assert not missing, f"undocumented endpoints: {sorted(missing)}"


def test_spec_documents_nothing_that_does_not_exist(spec, api_mod):
    phantom = documented_operations(spec) - implemented_operations(api_mod)
    assert not phantom, f"documented but not implemented: {sorted(phantom)}"


def test_dashboard_routes_are_hidden(spec, api_mod):
    """The scoreboard is not part of the teams' API surface."""
    assert "/" not in spec["paths"]
    assert "/dashboard" not in spec["paths"]


# -------------------------------------------------------------
# Schema matches the payloads the code actually returns
# -------------------------------------------------------------
def required_fields(spec, path, method, status="200"):
    schema = spec["paths"][path][method]["responses"][status]["content"]["application/json"]["schema"]
    if "$ref" in schema:
        schema = resolve(spec, schema["$ref"])
    return set(schema["required"])


def resolve(spec, ref):
    node = spec
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def test_auth_response_matches_spec(client, spec):
    body = client.post("/auth", headers={"team": "team-alpha"}).json()
    assert set(body) == required_fields(spec, "/auth", "post")


def test_status_response_matches_spec(client, auth, spec):
    auth()
    body = client.get("/status").json()
    assert set(body) == required_fields(spec, "/status", "get")

    team_schema = resolve(spec, "#/components/schemas/TeamStatus")
    assert set(body["teams"][0]) == set(team_schema["required"])
    assert set(team_schema["properties"]) == set(team_schema["required"])


def test_health_response_matches_spec(client, spec):
    body = client.get("/health").json()
    assert set(body) == required_fields(spec, "/health", "get")


def test_validate_success_matches_spec(client, auth, spec, fully_covered, narrative):
    headers, _ = auth()
    fully_covered("team-alpha")
    body = client.post("/validate", headers=headers, json={"submission": narrative}).json()
    assert set(body) == required_fields(spec, "/validate", "post")


def test_fragment_schema_matches_a_real_fragment(client, auth, spec, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "CHAOS_RATE", 0.0)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()

    fragment = resolve(spec, "#/components/schemas/Fragment")
    assert set(body) == set(fragment["required"])


def test_recent_chaos_enum_is_complete(spec, api_mod):
    """A new chaos type must be added to the spec, not just the code."""
    documented = (
        spec["paths"]["/status"]["get"]["responses"]["200"]["content"]
        ["application/json"]["schema"]["properties"]["recent_chaos"]["items"]
        ["properties"]["type"]["enum"]
    )
    assert sorted(documented) == sorted(api_mod.CHAOS_TYPES)


def test_documented_team_pattern_is_the_enforced_one(spec, api_mod):
    pattern = spec["paths"]["/auth"]["post"]["parameters"][0]["schema"]["pattern"]
    assert pattern == api_mod.TEAM_NAME_RE.pattern


def test_documented_error_codes_are_reachable(spec):
    """Every chaos status code the spec advertises is one the code can raise."""
    documented = {
        code for code in spec["paths"]["/fragment"]["get"]["responses"]
        if code.isdigit() and int(code) >= 418
    }
    assert documented == {"418", "429", "500", "504"}


def test_every_ref_resolves(spec):
    def walk(node, trail="#"):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref":
                    assert isinstance(value, str) and value.startswith("#/"), value
                    resolve(spec, value)  # raises KeyError if dangling
                else:
                    walk(value, f"{trail}/{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{trail}[{i}]")

    walk(spec)


def test_all_operations_are_described_and_tagged(spec):
    declared = {tag["name"] for tag in spec["tags"]}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("summary"), f"{method.upper()} {path} has no summary"
            assert op.get("tags"), f"{method.upper()} {path} has no tags"
            assert set(op["tags"]) <= declared, f"{method.upper()} {path} has an unknown tag"
            assert "200" in op["responses"], f"{method.upper()} {path} documents no success"


def test_challenge_endpoints_document_their_auth_headers(spec):
    for path in ("/fragment", "/validate"):
        method = "get" if path == "/fragment" else "post"
        names = set()
        for param in spec["paths"][path][method]["parameters"]:
            param = resolve(spec, param["$ref"]) if "$ref" in param else param
            names.add(param["name"])
        assert names == {"team", "token"}, path


def test_spec_never_contains_the_narrative(spec, api_mod, narrative):
    """The published spec is the most public artefact here."""
    raw = SPEC_PATH.read_text().lower()
    assert narrative.lower() not in raw
