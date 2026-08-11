"""The /validate endpoint: the coverage gate, normalisation, and scoring."""

import pytest


def test_missing_headers(client):
    res = client.post("/validate", json={"submission": "anything"})
    assert res.status_code == 400
    assert res.json()["detail"] == "Missing team or token header"


def test_expired_token(client, auth, api_mod):
    headers, _ = auth()
    api_mod.TEAM_TOKENS["team-alpha"]["timestamp"] -= api_mod.IDLE_TIMEOUT + 1
    res = client.post("/validate", headers=headers, json={"submission": "x"})
    assert res.status_code == 401


def test_body_must_be_an_object(client, auth):
    headers, _ = auth()
    res = client.post("/validate", headers=headers, json=["not", "an", "object"])
    assert res.status_code == 422


# -------------------------------------------------------------
# The coverage gate
# -------------------------------------------------------------
def test_gate_blocks_before_full_coverage(client, auth, narrative):
    headers, _ = auth()
    res = client.post("/validate", headers=headers, json={"submission": narrative})
    assert res.status_code == 403
    assert "0 of 10 words" in res.json()["detail"]


def test_gate_reports_actual_progress(client, auth, api_mod, narrative):
    headers, _ = auth()
    api_mod.team_record("team-alpha")["seen"] = {0, 1, 2, 3}

    res = client.post("/validate", headers=headers, json={"submission": narrative})
    assert res.status_code == 403
    assert f"4 of {len(api_mod.WORDS)} words" in res.json()["detail"]


def test_gate_is_free(client, auth, api_mod, narrative):
    """Being told you are not ready yet should not cost quota."""
    headers, _ = auth()
    start = api_mod.TEAM_TOKENS["team-alpha"]["remaining"]

    client.post("/validate", headers=headers, json={"submission": narrative})

    assert api_mod.TEAM_TOKENS["team-alpha"]["remaining"] == start
    assert api_mod.TEAM_DATA["team-alpha"]["submissions"] == 0


def test_gate_needs_every_single_word(client, auth, api_mod, fully_covered, narrative):
    headers, _ = auth()
    record = fully_covered("team-alpha")
    record["seen"].discard(len(api_mod.WORDS) - 1)  # one short

    res = client.post("/validate", headers=headers, json={"submission": narrative})
    assert res.status_code == 403


# -------------------------------------------------------------
# Submissions
# -------------------------------------------------------------
def test_missing_submission_field(client, auth, fully_covered):
    headers, _ = auth()
    fully_covered("team-alpha")
    res = client.post("/validate", headers=headers, json={})
    assert res.status_code == 400
    assert res.json()["detail"] == "Missing submission"


@pytest.mark.parametrize("value", [None, "", 42, [], {}, True])
def test_non_string_submissions_are_rejected(client, auth, fully_covered, value):
    headers, _ = auth()
    fully_covered("team-alpha")
    res = client.post("/validate", headers=headers, json={"submission": value})
    assert res.status_code == 400


def test_wrong_submission_costs_quota_and_is_counted(client, auth, api_mod, fully_covered):
    headers, _ = auth()
    fully_covered("team-alpha")
    start = api_mod.TEAM_TOKENS["team-alpha"]["remaining"]

    res = client.post("/validate", headers=headers, json={"submission": "completely wrong"})

    assert res.status_code == 400
    assert res.json()["detail"] == "Incorrect submission"
    assert api_mod.TEAM_TOKENS["team-alpha"]["remaining"] == start - 1
    assert api_mod.TEAM_DATA["team-alpha"]["submissions"] == 1
    assert "team-alpha" not in api_mod.COMPLETED_TEAMS


def test_correct_submission_completes_the_challenge(client, auth, api_mod, fully_covered, narrative):
    headers, _ = auth()
    fully_covered("team-alpha")

    res = client.post("/validate", headers=headers, json={"submission": narrative})

    assert res.status_code == 200
    body = res.json()
    assert body["team"] == "team-alpha"
    assert body["status"] == "success"
    assert body["message"] == "Correct submission! Challenge complete."
    assert body["completed_at"] > 0
    assert body["duration"] >= 0
    assert "team-alpha" in api_mod.COMPLETED_TEAMS


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.upper(),
        lambda s: s.lower(),
        lambda s: s + ".",
        lambda s: "  " + s + "  ",
        lambda s: s.replace(" ", "   "),
        lambda s: s.replace(" ", ", "),
        lambda s: s + "!!!",
        lambda s: s.replace(" ", "\n"),
    ],
    ids=["upper", "lower", "trailing-dot", "padded", "extra-spaces",
         "commas", "bangs", "newlines"],
)
def test_normalisation_forgives_case_and_punctuation(
    client, auth, fully_covered, narrative, mutate
):
    headers, _ = auth()
    fully_covered("team-alpha")
    res = client.post("/validate", headers=headers, json={"submission": mutate(narrative)})
    assert res.status_code == 200, res.text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda words: " ".join(reversed(words)),
        lambda words: " ".join(words[:-1]),
        lambda words: " ".join(words + ["extra"]),
        lambda words: " ".join(words[1:]),
    ],
    ids=["reversed", "missing-last", "extra-word", "missing-first"],
)
def test_wrong_word_order_or_count_fails(client, auth, fully_covered, narrative, mutate):
    headers, _ = auth()
    fully_covered("team-alpha")
    res = client.post("/validate", headers=headers, json={"submission": mutate(narrative.split())})
    assert res.status_code == 400


def test_duration_is_measured_from_first_auth(client, auth, api_mod, fully_covered, narrative):
    headers, _ = auth()
    fully_covered("team-alpha")
    api_mod.TEAM_DATA["team-alpha"]["start_time"] -= 120

    body = client.post("/validate", headers=headers, json={"submission": narrative}).json()
    assert body["duration"] >= 120


def test_completion_freezes_duration_in_status(client, auth, api_mod, fully_covered, narrative):
    headers, _ = auth()
    fully_covered("team-alpha")
    client.post("/validate", headers=headers, json={"submission": narrative})

    first = client.get("/status").json()["teams"][0]["duration"]
    second = client.get("/status").json()["teams"][0]["duration"]
    assert first == second


def test_exhausted_quota_blocks_submission(client, auth, api_mod, fully_covered, narrative):
    headers, _ = auth()
    fully_covered("team-alpha")
    api_mod.TEAM_TOKENS["team-alpha"]["remaining"] = 0

    res = client.post("/validate", headers=headers, json={"submission": narrative})
    assert res.status_code == 403
    assert res.json()["detail"] == "Token limit reached"
