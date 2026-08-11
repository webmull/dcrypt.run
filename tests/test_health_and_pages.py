"""Health probe, the dashboard route, and static assets."""


def test_health(client, api_mod):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["uptime"] > 0
    assert body["teams_active"] == 0
    assert body["narrative_configured"] is True


def test_health_counts_active_teams(client, auth):
    auth("one")
    auth("two")
    assert client.get("/health").json()["teams_active"] == 2


def test_uptime_is_elapsed_time_not_a_clock(client, api_mod):
    """The old build reported `time.time()` here, which is not an uptime."""
    uptime = client.get("/health").json()["uptime"]
    assert uptime < 60 * 60 * 24 * 365  # sane elapsed value, not a unix timestamp
    assert abs(uptime - (api_mod.current_time() - api_mod.START_TIME)) < 5


def test_dashboard_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "Decrypt" in res.text


def test_dashboard_alias_redirects(client):
    res = client.get("/dashboard", follow_redirects=False)
    assert res.status_code == 307
    assert res.headers["location"] == "/"


def test_about_page_is_served(client):
    res = client.get("/about")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "chaos-engineering challenge" in res.text


def test_about_page_documents_every_chaos_event(client, api_mod):
    """The overview is the briefing material, so it must stay in step."""
    body = client.get("/about").text
    for kind in api_mod.CHAOS_TYPES:
        assert kind in body, f"{kind} is missing from the about page"


def test_about_page_states_the_real_quota_and_chaos_rate(client, api_mod):
    body = client.get("/about").text
    assert str(api_mod.TOKEN_LIMIT) in body
    assert f"{int(api_mod.CHAOS_RATE * 100)}%" in body


def test_about_and_scoreboard_link_to_each_other(client):
    assert '"/about"' in client.get("/").text
    assert 'href="/"' in client.get("/about").text


def test_static_assets_are_served(client):
    assert client.get("/static/favicon-32x32.png").status_code == 200
    assert client.get("/static/adam.png").status_code == 200


def test_solution_file_is_gone(client):
    """This used to be a public download of the answer."""
    assert client.get("/static/solution.txt").status_code == 404


def test_no_static_file_contains_the_narrative(api_mod, narrative):
    from pathlib import Path

    static = Path(api_mod.BASE_DIR) / "static"
    for path in static.rglob("*"):
        if path.is_file() and path.suffix in {".txt", ".json", ".webmanifest", ".md", ".html"}:
            assert narrative.lower() not in path.read_text(errors="ignore").lower(), path


def test_about_page_does_not_leak_the_narrative(client, api_mod, narrative):
    """It is the most-read page, and it explains the puzzle without giving it away."""
    body = client.get("/about").text.lower()
    assert narrative.lower() not in body
    for word in api_mod.WORDS:
        assert f'"{word.lower()}"' not in body


def test_about_page_has_no_external_scripts(api_mod):
    """Same rule as the scoreboard: nothing to fail at event time."""
    import re
    from pathlib import Path

    source = (Path(api_mod.BASE_DIR) / "about.html").read_text()
    assert re.findall(r"<script[^>]*\ssrc=", source) == []
    assert "<script" not in source
