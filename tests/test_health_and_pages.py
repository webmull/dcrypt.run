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


def test_static_assets_are_served(client):
    assert client.get("/static/favicon-32x32.png").status_code == 200


def test_solution_file_is_gone(client):
    """This used to be a public download of the answer."""
    assert client.get("/static/solution.txt").status_code == 404


def test_no_static_file_contains_the_narrative(api_mod, narrative):
    from pathlib import Path

    static = Path(api_mod.BASE_DIR) / "static"
    for path in static.rglob("*"):
        if path.is_file() and path.suffix in {".txt", ".json", ".webmanifest", ".md", ".html"}:
            assert narrative.lower() not in path.read_text(errors="ignore").lower(), path
