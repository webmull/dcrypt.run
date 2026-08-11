"""The /fragment endpoint: token enforcement, the happy path, and every
chaos event in the catalogue.

Chaos is pinned with `force_chaos`, which fixes the RNG and collapses all
sleep durations to zero, so these run fast and deterministically.
"""

import json

import pytest

from conftest import force_chaos, no_chaos

ALL_CHAOS = [
    "delay",
    "malformed_json",
    "broken_json",
    "error_code",
    "duplicate_fragment",
    "empty_response",
    "html_injection",
    "slow_burst",
    "token_drain",
    "reverse_text",
    "unicode_garble",
    "out_of_order",
]

# Events that hand over a genuine, uncorrupted word.
COUNTS_AS_SEEN = {"delay", "slow_burst", "token_drain", "duplicate_fragment", "out_of_order"}


def test_catalogue_matches_implementation(api_mod):
    """If a chaos type is added, this test is the reminder to document it."""
    assert api_mod.CHAOS_TYPES == ALL_CHAOS


# -------------------------------------------------------------
# Token enforcement
# -------------------------------------------------------------
def test_missing_headers(client):
    res = client.get("/fragment")
    assert res.status_code == 400
    assert res.json()["detail"] == "Missing team or token header"


def test_unknown_team(client):
    res = client.get("/fragment", headers={"team": "ghost", "token": "x"})
    assert res.status_code == 401
    assert res.json()["detail"] == "No active token found for team"


def test_wrong_token(client, auth):
    headers, _ = auth()
    res = client.get("/fragment", headers={**headers, "token": "not-the-token"})
    assert res.status_code == 403
    assert res.json()["detail"] == "Invalid token for team"


def test_expired_token(client, auth, api_mod):
    headers, _ = auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= api_mod.IDLE_TIMEOUT + 1
    res = client.get("/fragment", headers=headers)
    assert res.status_code == 401
    assert "Token expired" in res.json()["detail"]


def test_exhausted_quota(client, auth, api_mod):
    headers, _ = auth()
    api_mod.TEAM_TOKENS["team-alpha"]["remaining"] = 0
    res = client.get("/fragment", headers=headers)
    assert res.status_code == 403
    assert res.json()["detail"] == "Token limit reached"


def test_successful_call_refreshes_idle_timer(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    headers, _ = auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= 100
    before = api_mod.TEAM_TOKENS["team-alpha"]["timestamp"]
    client.get("/fragment", headers=headers)
    assert api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] > before


# -------------------------------------------------------------
# Happy path
# -------------------------------------------------------------
def test_clean_fragment_shape(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()

    assert set(body) == {"word", "position"}
    assert 0 <= body["position"] < len(api_mod.WORDS)
    assert body["word"] == api_mod.WORDS[body["position"]]


def test_clean_fragment_costs_one_and_counts(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    headers, _ = auth()
    start = api_mod.TEAM_TOKENS["team-alpha"]["remaining"]

    body = client.get("/fragment", headers=headers).json()

    assert api_mod.TEAM_TOKENS["team-alpha"]["remaining"] == start - 1
    record = api_mod.TEAM_DATA["team-alpha"]
    assert record["seen"] == {body["position"]}
    assert record["requests_ok"] == 1


def test_repeated_words_do_not_inflate_coverage(client, auth, api_mod, monkeypatch):
    """Coverage counts distinct words, so the same word twice is still one."""
    no_chaos(monkeypatch)
    monkeypatch.setattr(api_mod.random, "choice", lambda seq: api_mod.FRAGMENTS[3])
    headers, _ = auth()

    for _ in range(4):
        client.get("/fragment", headers=headers)

    record = api_mod.TEAM_DATA["team-alpha"]
    assert record["seen"] == {3}
    assert record["requests_ok"] == 4


def test_full_grind_reaches_total_coverage(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    monkeypatch.setattr(api_mod, "TOKEN_LIMIT", 500)
    headers, _ = auth()

    counter = {"i": 0}

    def cycle(seq):
        frag = seq[counter["i"] % len(seq)]
        counter["i"] += 1
        return frag

    monkeypatch.setattr(api_mod.random, "choice", cycle)
    for _ in range(len(api_mod.WORDS)):
        assert client.get("/fragment", headers=headers).status_code == 200

    assert len(api_mod.TEAM_DATA["team-alpha"]["seen"]) == len(api_mod.WORDS)


# -------------------------------------------------------------
# Chaos: bookkeeping common to every event
# -------------------------------------------------------------
@pytest.mark.parametrize("kind", ALL_CHAOS)
def test_every_chaos_event_is_recorded(client, auth, api_mod, monkeypatch, kind):
    force_chaos(monkeypatch, kind)
    headers, _ = auth()

    client.get("/fragment", headers=headers)

    events = api_mod.CHAOS_EVENTS["team-alpha"]
    assert [e["type"] for e in events] == [kind]
    assert events[0]["ts"] > 0

    recent = list(api_mod.RECENT_CHAOS)
    assert recent[-1]["type"] == kind
    assert recent[-1]["team"] == "team-alpha"


@pytest.mark.parametrize("kind", ALL_CHAOS)
def test_every_chaos_event_still_costs_quota(client, auth, api_mod, monkeypatch, kind):
    """Chaos is not a free retry — that is the whole economy of the challenge."""
    force_chaos(monkeypatch, kind)
    headers, _ = auth()
    start = api_mod.TEAM_TOKENS["team-alpha"]["remaining"]

    client.get("/fragment", headers=headers)

    spent = start - api_mod.TEAM_TOKENS["team-alpha"]["remaining"]
    assert spent >= 1


@pytest.mark.parametrize("kind", ALL_CHAOS)
def test_coverage_only_credits_intact_words(client, auth, api_mod, monkeypatch, kind):
    force_chaos(monkeypatch, kind)
    headers, _ = auth()

    client.get("/fragment", headers=headers)

    seen = api_mod.TEAM_DATA["team-alpha"]["seen"]
    if kind in COUNTS_AS_SEEN:
        assert len(seen) == 1, f"{kind} delivers a real word and should count"
    else:
        assert seen == set(), f"{kind} delivers nothing usable and must not count"


# -------------------------------------------------------------
# Chaos: per-event behaviour
# -------------------------------------------------------------
def test_delay_still_returns_a_real_fragment(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "delay", fragment_index=2)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()
    assert body == {"word": api_mod.WORDS[2], "position": 2}


def test_slow_burst_still_returns_a_real_fragment(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "slow_burst", fragment_index=1)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()
    assert body == {"word": api_mod.WORDS[1], "position": 1}


def test_malformed_json_is_unparseable_but_claims_json(client, auth, monkeypatch):
    force_chaos(monkeypatch, "malformed_json")
    headers, _ = auth()
    res = client.get("/fragment", headers=headers)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    with pytest.raises(json.JSONDecodeError):
        json.loads(res.text)


def test_broken_json_is_unparseable(client, auth, monkeypatch):
    force_chaos(monkeypatch, "broken_json")
    headers, _ = auth()
    res = client.get("/fragment", headers=headers)

    assert res.status_code == 200
    with pytest.raises(json.JSONDecodeError):
        json.loads(res.text)


def test_html_injection_serves_html_as_json(client, auth, monkeypatch):
    force_chaos(monkeypatch, "html_injection")
    headers, _ = auth()
    res = client.get("/fragment", headers=headers)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    assert res.text.lstrip().startswith("<html")


def test_empty_response(client, auth, monkeypatch):
    force_chaos(monkeypatch, "empty_response")
    headers, _ = auth()
    res = client.get("/fragment", headers=headers)

    assert res.status_code == 200
    assert res.json() == {}


def test_error_code_is_from_the_documented_set(client, auth, monkeypatch):
    force_chaos(monkeypatch, "error_code")
    headers, _ = auth()
    res = client.get("/fragment", headers=headers)

    # force_chaos pins list choices to the first element.
    assert res.status_code == 418
    assert res.json()["detail"] == "Chaos error event triggered"


def test_error_code_still_charges_quota(client, auth, api_mod, monkeypatch):
    """The decrement happens before chaos rolls, so a raised error still bills."""
    force_chaos(monkeypatch, "error_code")
    headers, _ = auth()
    start = api_mod.TEAM_TOKENS["team-alpha"]["remaining"]

    client.get("/fragment", headers=headers)

    assert api_mod.TEAM_TOKENS["team-alpha"]["remaining"] == start - 1


def test_duplicate_fragment_shape(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "duplicate_fragment", fragment_index=4)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()

    assert list(body) == ["fragments"]
    assert len(body["fragments"]) == 2
    assert body["fragments"][0] == body["fragments"][1]
    assert body["fragments"][0]["position"] == 4


def test_token_drain_takes_extra_quota(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "token_drain")  # randint pinned to its low bound, 1
    headers, _ = auth()
    start = api_mod.TEAM_TOKENS["team-alpha"]["remaining"]

    client.get("/fragment", headers=headers)

    # One for the request, at least one more for the drain.
    assert api_mod.TEAM_TOKENS["team-alpha"]["remaining"] == start - 2


def test_token_drain_never_goes_negative(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "token_drain")
    headers, _ = auth()
    api_mod.TEAM_TOKENS["team-alpha"]["remaining"] = 1

    client.get("/fragment", headers=headers)

    assert api_mod.TEAM_TOKENS["team-alpha"]["remaining"] == 0


def test_reverse_text_corrupts_the_word(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "reverse_text", fragment_index=0)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()

    assert body["position"] == 0
    assert body["word"] == api_mod.WORDS[0][::-1]
    # The canonical fragment list must not be mutated by chaos.
    assert api_mod.FRAGMENTS[0]["word"] == api_mod.WORDS[0]


def test_unicode_garble_appends_junk(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "unicode_garble", fragment_index=0)
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()

    assert body["word"].startswith(api_mod.WORDS[0])
    assert body["word"] != api_mod.WORDS[0]
    assert api_mod.FRAGMENTS[0]["word"] == api_mod.WORDS[0]


def test_out_of_order_returns_a_genuine_tail_word(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "out_of_order")
    headers, _ = auth()
    body = client.get("/fragment", headers=headers).json()

    assert body["word"] == api_mod.WORDS[body["position"]]
    assert body["position"] >= max(0, len(api_mod.WORDS) - 10)


def test_chaos_rate_zero_disables_chaos(client, auth, api_mod, monkeypatch):
    no_chaos(monkeypatch)
    headers, _ = auth()
    for _ in range(15):
        assert client.get("/fragment", headers=headers).status_code == 200
    assert "team-alpha" not in api_mod.CHAOS_EVENTS


def test_chaos_rate_one_forces_chaos_every_call(client, auth, api_mod, monkeypatch):
    monkeypatch.setattr(api_mod, "CHAOS_RATE", 1.0)
    monkeypatch.setattr(api_mod, "TOKEN_LIMIT", 100)
    monkeypatch.setattr(api_mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(api_mod.random, "randint", lambda a, b: a)
    headers, _ = auth()

    for _ in range(10):
        client.get("/fragment", headers=headers)

    assert len(api_mod.CHAOS_EVENTS["team-alpha"]) == 10


def test_recent_chaos_is_capped(client, auth, api_mod, monkeypatch):
    force_chaos(monkeypatch, "empty_response")
    monkeypatch.setattr(api_mod, "TOKEN_LIMIT", 100)
    headers, _ = auth()

    for _ in range(40):
        client.get("/fragment", headers=headers)

    assert len(api_mod.RECENT_CHAOS) == api_mod.RECENT_CHAOS.maxlen == 30
