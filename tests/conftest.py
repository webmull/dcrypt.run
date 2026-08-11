"""Shared fixtures.

The narrative is read from the environment at import time, so it has to be set
before `decrypt_api` is imported. pytest loads this conftest before collecting
any test module, which makes this the right place to do it.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "decrypt_api.py"

# A short, known narrative keeps coverage assertions cheap.
NARRATIVE = "Alpha bravo charlie delta echo foxtrot golf hotel india juliet"

os.environ["NARRATIVE_TEXT"] = NARRATIVE
os.environ.pop("STATE_FILE", None)

import decrypt_api as api  # noqa: E402


@pytest.fixture(scope="session")
def narrative() -> str:
    return NARRATIVE


@pytest.fixture
def api_mod():
    return api


@pytest.fixture(autouse=True)
def clean_state():
    """Every test starts from an empty scoreboard."""
    def wipe():
        api.TEAM_TOKENS.clear()
        api.TEAM_DATA.clear()
        api.CHAOS_EVENTS.clear()
        api.COMPLETED_TEAMS.clear()
        api.RECENT_CHAOS.clear()

    wipe()
    yield
    wipe()


@pytest.fixture
def client():
    # The context manager runs the lifespan, which is what starts housekeeping
    # and restores any snapshot.
    with TestClient(api.app) as c:
        yield c


@pytest.fixture
def auth(client):
    """Register a team and return (headers, payload)."""
    def _auth(team="team-alpha"):
        res = client.post("/auth", headers={"team": team})
        assert res.status_code == 200, res.text
        body = res.json()
        return {"team": team, "token": body["token"]}, body

    return _auth


@pytest.fixture
def fully_covered(api_mod):
    """Give a team complete word coverage without grinding the endpoint."""
    def _cover(team="team-alpha"):
        record = api_mod.team_record(team)
        record["seen"] = set(range(len(api_mod.WORDS)))
        return record

    return _cover


def force_chaos(monkeypatch, kind, *, fragment_index=0):
    """Pin the RNG so a specific chaos event fires with no real sleeping."""
    monkeypatch.setattr(api.random, "random", lambda: 0.0)
    monkeypatch.setattr(api.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(api.random, "randint", lambda a, b: a)

    def choice(seq):
        if seq is api.CHAOS_TYPES:
            return kind
        if seq is api.FRAGMENTS:
            return api.FRAGMENTS[fragment_index]
        return seq[0]

    monkeypatch.setattr(api.random, "choice", choice)


def no_chaos(monkeypatch):
    """Disable chaos so the happy path is deterministic."""
    monkeypatch.setattr(api, "CHAOS_RATE", 0.0)


def load_fresh_module(monkeypatch, **env):
    """Import a second, independent copy of the app under a given environment.

    Used for the config tests, since configuration is resolved at import time.
    Deliberately does not touch sys.modules, so the shared `api` module the
    other tests rely on is unaffected.
    """
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    spec = importlib.util.spec_from_file_location("decrypt_api_fresh", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
