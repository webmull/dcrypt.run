"""Auth, team-name validation, and the token lifecycle."""

import pytest


def test_missing_team_header_is_rejected(client):
    res = client.post("/auth")
    assert res.status_code == 400
    assert res.json()["detail"] == "Missing team header"


def test_valid_team_receives_full_quota(client, api_mod):
    res = client.post("/auth", headers={"team": "team-alpha"})
    assert res.status_code == 200
    body = res.json()
    assert body["team"] == "team-alpha"
    assert body["remaining"] == api_mod.TOKEN_LIMIT
    assert len(body["token"]) == 36  # uuid4


@pytest.mark.parametrize(
    "name",
    [
        "team alpha",
        "team_alpha",
        "team-alpha",
        "Team99",
        "a",
        "x" * 20,
    ],
)
def test_accepted_team_names(client, name):
    assert client.post("/auth", headers={"team": name}).status_code == 200


@pytest.mark.parametrize(
    "name",
    [
        "x" * 21,                 # over the length cap
        "<svg onload=alert()>",   # exactly 20 chars, and this used to reach the DOM
        "<script src=//x.yz>",    # 19 chars, likewise
        "team<b>",
        "team&amp;",
        'team"quote',
        "team/slash",
        "team.dot",
        "team\\back",
        "team'quote",
        " ",                      # whitespace-only would render as a blank row
        "   ",
        " leading",
        "trailing ",
        "-leading-dash",
        "_leading_score",
    ],
)
def test_rejected_team_names(client, name):
    """The dashboard renders team names, so the server is the first line of defence."""
    res = client.post("/auth", headers={"team": name})
    assert res.status_code == 400, f"{name!r} should have been rejected"
    assert "Invalid team name" in res.json()["detail"]


@pytest.mark.parametrize("name", ["teamé", "team🎉", "тим"])
def test_non_ascii_names_cannot_be_expressed(api_mod, name):
    """HTTP headers are ASCII, so these never arrive — but the regex refuses them too."""
    assert not api_mod.TEAM_NAME_RE.fullmatch(name)


def test_name_rule_is_a_single_source_of_truth(api_mod):
    """Boundary cases, asserted against the regex directly."""
    assert api_mod.TEAM_NAME_RE.fullmatch("a")
    assert api_mod.TEAM_NAME_RE.fullmatch("x" * 20)
    assert not api_mod.TEAM_NAME_RE.fullmatch("x" * 21)
    assert not api_mod.TEAM_NAME_RE.fullmatch("")


def test_rejected_name_never_reaches_state(client, api_mod):
    client.post("/auth", headers={"team": "<svg onload=alert()>"})
    assert api_mod.TEAM_TOKENS == {}
    assert api_mod.TEAM_DATA == {}


def test_reauth_returns_same_token_while_quota_remains(client):
    first = client.post("/auth", headers={"team": "team-alpha"}).json()
    second = client.post("/auth", headers={"team": "team-alpha"}).json()
    assert first["token"] == second["token"]
    assert second["remaining"] == first["remaining"]


def test_reauth_issues_new_token_once_quota_is_spent(client, api_mod):
    first = client.post("/auth", headers={"team": "team-alpha"}).json()
    api_mod.TEAM_TOKENS["team-alpha"]["remaining"] = 0

    second = client.post("/auth", headers={"team": "team-alpha"}).json()
    assert second["token"] != first["token"]
    assert second["remaining"] == api_mod.TOKEN_LIMIT
    assert api_mod.TEAM_DATA["team-alpha"]["tokens_issued"] == 2


def test_reauth_preserves_progress(client, api_mod, fully_covered):
    client.post("/auth", headers={"team": "team-alpha"})
    fully_covered("team-alpha")
    api_mod.TEAM_TOKENS["team-alpha"]["remaining"] = 0

    client.post("/auth", headers={"team": "team-alpha"})
    assert len(api_mod.TEAM_DATA["team-alpha"]["seen"]) == len(api_mod.WORDS)


def test_start_time_is_not_reset_by_reauth(client, api_mod):
    client.post("/auth", headers={"team": "team-alpha"})
    original = api_mod.TEAM_DATA["team-alpha"]["start_time"]
    api_mod.TEAM_TOKENS["team-alpha"]["remaining"] = 0
    client.post("/auth", headers={"team": "team-alpha"})
    assert api_mod.TEAM_DATA["team-alpha"]["start_time"] == original


def test_active_team_limit(client, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "ACTIVE_LIMIT", 2)
    assert client.post("/auth", headers={"team": "one"}).status_code == 200
    assert client.post("/auth", headers={"team": "two"}).status_code == 200

    res = client.post("/auth", headers={"team": "three"})
    assert res.status_code == 403
    assert "Team limit reached" in res.json()["detail"]

    # An already-registered team is still served at the limit.
    assert client.post("/auth", headers={"team": "one"}).status_code == 200


def test_token_quota_is_configurable(client, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "TOKEN_LIMIT", 3)
    body = client.post("/auth", headers={"team": "team-alpha"}).json()
    assert body["remaining"] == 3
    assert api_mod.TEAM_TOKENS["team-alpha"]["max"] == 3
