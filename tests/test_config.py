"""Configuration resolved at import time: the narrative and the env knobs."""

from fastapi.testclient import TestClient

from conftest import load_fresh_module


def test_narrative_comes_from_the_environment(monkeypatch):
    mod = load_fresh_module(monkeypatch, NARRATIVE_TEXT="one two three")
    assert mod.FULL_TEXT == "one two three"
    assert mod.WORDS == ["one", "two", "three"]
    assert mod.USING_PLACEHOLDER is False


def test_narrative_whitespace_is_normalised(monkeypatch):
    mod = load_fresh_module(monkeypatch, NARRATIVE_TEXT="  one\n\ttwo   three  ")
    assert mod.FULL_TEXT == "one two three"
    assert len(mod.WORDS) == 3


def test_fragments_index_the_narrative(monkeypatch):
    mod = load_fresh_module(monkeypatch, NARRATIVE_TEXT="alpha beta gamma")
    assert mod.FRAGMENTS == [
        {"word": "alpha", "position": 0},
        {"word": "beta", "position": 1},
        {"word": "gamma", "position": 2},
    ]


def test_placeholder_is_used_when_unset(monkeypatch):
    """The app must still boot without NARRATIVE_TEXT, but say so loudly."""
    mod = load_fresh_module(monkeypatch, NARRATIVE_TEXT=None)
    assert mod.USING_PLACEHOLDER is True
    assert mod.FULL_TEXT == mod.PLACEHOLDER_TEXT

    with TestClient(mod.app) as client:
        assert client.get("/health").json()["narrative_configured"] is False


def test_blank_narrative_falls_back_to_placeholder(monkeypatch):
    mod = load_fresh_module(monkeypatch, NARRATIVE_TEXT="   \n  ")
    assert mod.USING_PLACEHOLDER is True


def test_configured_narrative_reports_healthy(monkeypatch):
    mod = load_fresh_module(monkeypatch, NARRATIVE_TEXT="a real narrative")
    with TestClient(mod.app) as client:
        assert client.get("/health").json()["narrative_configured"] is True


def test_fingerprint_tracks_the_narrative(monkeypatch):
    a = load_fresh_module(monkeypatch, NARRATIVE_TEXT="one two")
    b = load_fresh_module(monkeypatch, NARRATIVE_TEXT="one two")
    c = load_fresh_module(monkeypatch, NARRATIVE_TEXT="one three")

    assert a.TEXT_FINGERPRINT == b.TEXT_FINGERPRINT
    assert a.TEXT_FINGERPRINT != c.TEXT_FINGERPRINT
    assert len(a.TEXT_FINGERPRINT) == 12


def test_numeric_knobs_are_read_from_the_environment(monkeypatch):
    mod = load_fresh_module(
        monkeypatch,
        NARRATIVE_TEXT="one two",
        TOKEN_LIMIT="7",
        IDLE_TIMEOUT="60",
        ACTIVE_LIMIT="9",
        CHAOS_RATE="0.25",
    )
    assert mod.TOKEN_LIMIT == 7
    assert mod.IDLE_TIMEOUT == 60
    assert mod.ACTIVE_LIMIT == 9
    assert mod.CHAOS_RATE == 0.25


def test_unparseable_knobs_fall_back_to_defaults(monkeypatch):
    """A typo in a deploy variable should not take the challenge down."""
    mod = load_fresh_module(
        monkeypatch,
        NARRATIVE_TEXT="one two",
        TOKEN_LIMIT="twenty",
        CHAOS_RATE="lots",
    )
    assert mod.TOKEN_LIMIT == 20
    assert mod.CHAOS_RATE == 0.55


def test_defaults(monkeypatch):
    mod = load_fresh_module(
        monkeypatch,
        NARRATIVE_TEXT="one two",
        TOKEN_LIMIT=None,
        IDLE_TIMEOUT=None,
        ACTIVE_LIMIT=None,
        CHAOS_RATE=None,
        STATE_FILE=None,
    )
    assert mod.TOKEN_LIMIT == 20
    assert mod.IDLE_TIMEOUT == 1800
    assert mod.ACTIVE_LIMIT == 150
    assert mod.CHAOS_RATE == 0.55
    assert mod.STATE_FILE is None
