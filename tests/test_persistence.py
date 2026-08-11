"""Optional state snapshots, so a restart mid-event is survivable."""

import json


def test_disabled_by_default(api_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "STATE_FILE", None)
    api_mod.team_record("team-alpha")
    api_mod.save_state()
    assert list(tmp_path.iterdir()) == []


def test_round_trip(client, auth, api_mod, tmp_path, monkeypatch, fully_covered):
    state = tmp_path / "state.json"
    monkeypatch.setattr(api_mod, "STATE_FILE", str(state))

    auth("team-alpha")
    record = fully_covered("team-alpha")
    record["submissions"] = 3
    record["requests_ok"] = 41
    api_mod.CHAOS_EVENTS["team-alpha"] = [{"ts": 123.0, "type": "delay"}]
    api_mod.COMPLETED_TEAMS.add("team-alpha")
    token = api_mod.TEAM_TOKENS["team-alpha"]["token"]

    api_mod.save_state()
    assert state.exists()

    api_mod.TEAM_TOKENS.clear()
    api_mod.TEAM_DATA.clear()
    api_mod.CHAOS_EVENTS.clear()
    api_mod.COMPLETED_TEAMS.clear()

    api_mod.load_state()

    assert api_mod.TEAM_TOKENS["team-alpha"]["token"] == token
    restored = api_mod.TEAM_DATA["team-alpha"]
    assert restored["seen"] == set(range(len(api_mod.WORDS)))
    assert isinstance(restored["seen"], set)
    assert restored["submissions"] == 3
    assert restored["requests_ok"] == 41
    assert api_mod.CHAOS_EVENTS["team-alpha"][0]["type"] == "delay"
    assert "team-alpha" in api_mod.COMPLETED_TEAMS


def test_restored_team_can_keep_playing(client, auth, api_mod, tmp_path, monkeypatch,
                                        fully_covered, narrative):
    state = tmp_path / "state.json"
    monkeypatch.setattr(api_mod, "STATE_FILE", str(state))

    headers, _ = auth("team-alpha")
    fully_covered("team-alpha")
    api_mod.save_state()

    api_mod.TEAM_TOKENS.clear()
    api_mod.TEAM_DATA.clear()
    api_mod.load_state()

    res = client.post("/validate", headers=headers, json={"submission": narrative})
    assert res.status_code == 200


def test_snapshot_is_valid_json_with_serialisable_sets(api_mod, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(api_mod, "STATE_FILE", str(state))
    api_mod.team_record("team-alpha")["seen"] = {3, 1, 2}

    api_mod.save_state()
    payload = json.loads(state.read_text())

    assert payload["teams"]["team-alpha"]["seen"] == [1, 2, 3]  # sorted list, not a set
    assert payload["fingerprint"] == api_mod.TEXT_FINGERPRINT
    assert payload["saved_at"] > 0


def test_snapshot_from_a_different_narrative_is_refused(api_mod, tmp_path, monkeypatch):
    """Word positions from another narrative would silently corrupt scores."""
    state = tmp_path / "state.json"
    monkeypatch.setattr(api_mod, "STATE_FILE", str(state))
    state.write_text(json.dumps({
        "fingerprint": "not-ours",
        "tokens": {"ghost": {"token": "x", "remaining": 5, "max": 20, "timestamp": 1.0}},
        "teams": {"ghost": {"seen": [0, 1], "requests_ok": 2, "submissions": 0,
                            "tokens_issued": 1, "start_time": 1.0}},
        "chaos": {},
        "completed": ["ghost"],
    }))

    api_mod.load_state()

    assert api_mod.TEAM_TOKENS == {}
    assert api_mod.COMPLETED_TEAMS == set()


def test_corrupt_snapshot_does_not_crash_startup(api_mod, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(api_mod, "STATE_FILE", str(state))
    state.write_text("{ this is not json")

    api_mod.load_state()  # must not raise

    assert api_mod.TEAM_TOKENS == {}


def test_missing_snapshot_is_not_an_error(api_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(api_mod, "STATE_FILE", str(tmp_path / "absent.json"))
    api_mod.load_state()
    assert api_mod.TEAM_TOKENS == {}


def test_unwritable_path_is_survivable(api_mod, tmp_path, monkeypatch, capsys):
    """A snapshot failure must never take a request down with it."""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    monkeypatch.setattr(api_mod, "STATE_FILE", str(blocker / "state.json"))
    api_mod.team_record("team-alpha")

    api_mod.save_state()  # must not raise

    assert "snapshot failed" in capsys.readouterr().out


def test_completion_snapshots_immediately(client, auth, api_mod, tmp_path, monkeypatch,
                                          fully_covered, narrative):
    """A win is the one event worth flushing to disk straight away."""
    state = tmp_path / "state.json"
    monkeypatch.setattr(api_mod, "STATE_FILE", str(state))
    headers, _ = auth("team-alpha")
    fully_covered("team-alpha")

    client.post("/validate", headers=headers, json={"submission": narrative})

    assert json.loads(state.read_text())["completed"] == ["team-alpha"]
