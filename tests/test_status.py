"""The /status telemetry endpoint and idle eviction."""

from conftest import force_chaos, no_chaos

TEAM_KEYS = {
    "team", "unique_seen", "requests_ok", "submissions", "remaining", "max",
    "completed", "chaos", "tokens_issued", "duration", "idle_seconds",
}
TOP_LEVEL_KEYS = {"teams", "total_chaos", "total_words", "recent_chaos", "uptime"}


def test_empty_scoreboard(client, api_mod):
    body = client.get("/status").json()
    assert body["teams"] == []
    assert body["total_chaos"] == 0
    assert body["total_words"] == len(api_mod.WORDS)
    assert body["recent_chaos"] == []
    assert body["uptime"] > 0


def test_shape_is_stable(client, auth):
    auth()
    body = client.get("/status").json()
    assert set(body) == TOP_LEVEL_KEYS
    assert set(body["teams"][0]) == TEAM_KEYS


def test_new_team_starts_at_zero(client, auth, api_mod):
    auth()
    team = client.get("/status").json()["teams"][0]
    assert team["team"] == "team-alpha"
    assert team["unique_seen"] == 0
    assert team["requests_ok"] == 0
    assert team["submissions"] == 0
    assert team["chaos"] == 0
    assert team["completed"] is False
    assert team["remaining"] == api_mod.TOKEN_LIMIT
    assert team["max"] == api_mod.TOKEN_LIMIT
    assert team["tokens_issued"] == 1


def test_progress_is_reported(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    headers, _ = auth()
    monkeypatch.setattr(api_mod.random, "choice", lambda seq: api_mod.FRAGMENTS[2])

    client.get("/fragment", headers=headers)
    client.get("/fragment", headers=headers)

    team = client.get("/status").json()["teams"][0]
    assert team["unique_seen"] == 1      # same word twice
    assert team["requests_ok"] == 2
    assert team["remaining"] == api_mod.TOKEN_LIMIT - 2


def test_idle_seconds_starts_near_zero(client, auth):
    auth()
    assert client.get("/status").json()["teams"][0]["idle_seconds"] < 5


def test_idle_seconds_tracks_last_request(client, auth, api_mod):
    """This drives the idle/decoding icon on the scoreboard."""
    auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= 300

    assert client.get("/status").json()["teams"][0]["idle_seconds"] >= 300


def test_a_request_resets_idle_seconds(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    headers, _ = auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= 300

    client.get("/fragment", headers=headers)

    assert client.get("/status").json()["teams"][0]["idle_seconds"] < 5


def test_chaos_totals_aggregate_across_teams(client, api_mod, monkeypatch):
    force_chaos(monkeypatch, "empty_response")
    for team in ("one", "two"):
        token = client.post("/auth", headers={"team": team}).json()["token"]
        client.get("/fragment", headers={"team": team, "token": token})

    body = client.get("/status").json()
    assert body["total_chaos"] == 2
    assert {t["team"]: t["chaos"] for t in body["teams"]} == {"one": 1, "two": 1}


def test_recent_chaos_is_newest_first(client, auth, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "CHAOS_RATE", 1.0)
    monkeypatch.setattr(api_mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(api_mod.random, "randint", lambda a, b: a)
    monkeypatch.setattr(api_mod, "TOKEN_LIMIT", 100)
    headers, _ = auth()

    for _ in range(3):
        client.get("/fragment", headers=headers)

    events = client.get("/status").json()["recent_chaos"]
    assert len(events) == 3
    assert events[0]["ts"] >= events[-1]["ts"]
    assert all(set(e) == {"ts", "type", "team"} for e in events)
    assert all(e["type"] in api_mod.CHAOS_TYPES for e in events)


def test_status_never_leaks_the_narrative(client, auth, api_mod, narrative):
    """The scoreboard is public and unauthenticated, so it must not spoil the puzzle."""
    headers, _ = auth()
    api_mod.team_record("team-alpha")["seen"] = set(range(len(api_mod.WORDS)))

    raw = client.get("/status").text.lower()

    assert narrative.lower() not in raw
    # No word ever appears as a JSON string value.
    for word in api_mod.WORDS:
        assert f'"{word.lower()}"' not in raw


def test_status_is_read_only(client, auth, api_mod):
    """Eviction used to live in this handler; it must not any more."""
    auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= api_mod.IDLE_TIMEOUT + 1

    assert len(client.get("/status").json()["teams"]) == 1
    assert "team-alpha" in api_mod.TEAM_TOKENS


def test_eviction_removes_idle_teams(client, auth, api_mod):
    auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= api_mod.IDLE_TIMEOUT + 1

    assert api_mod.evict_idle_teams() == 1
    assert api_mod.TEAM_TOKENS == {}
    assert api_mod.TEAM_DATA == {}
    assert api_mod.CHAOS_EVENTS == {}
    assert api_mod.COMPLETED_TEAMS == set()
    assert client.get("/status").json()["teams"] == []


def test_eviction_spares_active_teams(client, auth, api_mod):
    auth("busy")
    auth("idle")
    api_mod.TEAM_TOKENS["idle"]["timestamp"] -= api_mod.IDLE_TIMEOUT + 1

    assert api_mod.evict_idle_teams() == 1
    assert set(api_mod.TEAM_TOKENS) == {"busy"}


def test_completed_team_appears_completed(client, auth, api_mod, fully_covered, narrative):
    headers, _ = auth()
    fully_covered("team-alpha")
    client.post("/validate", headers=headers, json={"submission": narrative})

    team = client.get("/status").json()["teams"][0]
    assert team["completed"] is True
    assert team["submissions"] == 1
    assert team["unique_seen"] == len(api_mod.WORDS)
