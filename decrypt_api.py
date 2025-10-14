from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
import random, time, uuid, os, re

app = FastAPI(title="Decrypt the Narrative API")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Configuration ---
TOKEN_LIMIT = 20
IDLE_TIMEOUT = 1800  # 30 minutes

FULL_TEXT = (
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
)
WORDS = FULL_TEXT.split()
FRAGMENTS = [{"word": w, "position": i} for i, w in enumerate(WORDS)]

# --- State Stores ---
TEAM_TOKENS = {}
TEAM_DATA = {}
CHAOS_EVENTS = {}
COMPLETED_TEAMS = set()


# -------------------------------------------------------------
# Utility
# -------------------------------------------------------------
def current_time() -> float:
    return time.time()


def chaos_roll(team: str):
    """Inject controlled chaos into /fragment requests."""
    if random.random() < 0.10:  # ~10% chance overall
        chaos_type = random.choice(["delay", "malformed_json", "broken_json", "error_code"])
        CHAOS_EVENTS.setdefault(team, []).append({"ts": current_time(), "type": chaos_type})

        # 💤 Artificial delay chaos (scales with activity)
        if chaos_type == "delay":
            team_info = TEAM_DATA.get(team, {})
            elapsed_minutes = (current_time() - team_info.get("start_time", current_time())) / 60
            scale = min(1 + (elapsed_minutes * 0.1), 2.0)
            sleep_time = random.uniform(0.5, 2.5) * scale
            time.sleep(sleep_time)
            return None

        # 🪓 Malformed JSON chaos (truncated)
        if chaos_type == "malformed_json":
            broken_body = '{"word": "spl1t", "pos":'
            return PlainTextResponse(broken_body, media_type="application/json", status_code=200)

        # 💣 Broken JSON chaos (garbled)
        if chaos_type == "broken_json":
            broken_body = "{not valid json at all"
            return PlainTextResponse(broken_body, media_type="application/json", status_code=200)

        # ☕ Error code chaos
        if chaos_type == "error_code":
            raise HTTPException(
                status_code=random.choice([418, 429, 500, 504]),
                detail="Chaos error event triggered"
            )

    return None


# -------------------------------------------------------------
# Auth Endpoint
# -------------------------------------------------------------
@app.post("/auth")
def issue_token(request: Request, team: str = Header(None)):
    """Issue a token or return existing valid one."""
    if not team:
        raise HTTPException(status_code=400, detail="Missing team header")

    if len(team_name) > 20:
        raise HTTPException(status_code=400, detail="Team name too long (max 20 characters)")

    now = current_time()
    existing = TEAM_TOKENS.get(team)

    # If still valid, reuse
    if existing and existing["remaining"] > 0:
        existing["timestamp"] = now
        return {"token": existing["token"], "team": team, "remaining": existing["remaining"]}

    # Otherwise issue new token
    token = str(uuid.uuid4())
    TEAM_TOKENS[team] = {
        "token": token,
        "remaining": TOKEN_LIMIT,
        "max": TOKEN_LIMIT,
        "timestamp": now
    }
    data = TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 0})
    data["tokens_issued"] += 1
    data.setdefault("start_time", now)
    return {"token": token, "team": team, "remaining": TOKEN_LIMIT}


# -------------------------------------------------------------
# Fragment Endpoint (Chaos lives here)
# -------------------------------------------------------------
@app.get("/fragment")
def get_fragment(request: Request, team: str = Header(None), token: str = Header(None)):
    """Return a random fragment or chaos response."""
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    # 💥 Chaos can strike before validation
    chaos_result = chaos_roll(team)
    if chaos_result is not None:
        return chaos_result

    token_data = TEAM_TOKENS.get(team)
    now = current_time()

    if not token_data or now - token_data["timestamp"] > IDLE_TIMEOUT:
        raise HTTPException(status_code=401, detail="Token expired. Please re-authenticate.")

    if token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token for team")

    if token_data["remaining"] <= 0:
        raise HTTPException(status_code=403, detail="Token limit reached")

    token_data["remaining"] -= 1
    token_data["timestamp"] = now

    TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 1, "start_time": now})
    TEAM_DATA[team]["seen_count"] += 1

    return random.choice(FRAGMENTS)


# -------------------------------------------------------------
# Validate Endpoint (no chaos)
# -------------------------------------------------------------
@app.post("/validate")
async def validate_submission(request: Request, team: str = Header(None), token: str = Header(None)):
    """Validate a team's submitted sentence against the canonical full text."""
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    if not token_data or token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid or expired token")

    body = await request.json()
    submitted_text = body.get("submission")
    if not submitted_text:
        raise HTTPException(status_code=400, detail="Missing submission")

    # Normalize both sides: remove punctuation, lowercase, collapse spaces
    normalize = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    canonical = normalize(FULL_TEXT)
    submission_clean = normalize(submitted_text)

    if submission_clean != canonical:
        CHAOS_EVENTS.setdefault(team, []).append({"ts": time.time(), "type": "invalid_submission"})
        raise HTTPException(status_code=400, detail="Incorrect submission")

    # ✅ Mark completion and record timing
    TEAM_DATA.setdefault(team, {}).setdefault("completed_time", time.time())
    COMPLETED_TEAMS.add(team)

    return {
        "team": team,
        "status": "success",
        "message": "Correct submission! Challenge complete.",
        "completed_at": TEAM_DATA[team]["completed_time"],
        "duration": TEAM_DATA[team]["completed_time"] - TEAM_DATA[team]["start_time"]
    }

# -------------------------------------------------------------
# Status Endpoint with Cleanup
# -------------------------------------------------------------
@app.get("/status")
def get_status():
    now = current_time()
    teams_out = []

    # 🧹 Clean up idle teams (30+ min inactivity)
    idle_cutoff = now - IDLE_TIMEOUT
    to_remove = [team for team, tok in TEAM_TOKENS.items() if tok["timestamp"] < idle_cutoff]
    for team in to_remove:
        TEAM_TOKENS.pop(team, None)
        TEAM_DATA.pop(team, None)
        CHAOS_EVENTS.pop(team, None)
        COMPLETED_TEAMS.discard(team)

    total_chaos = sum(len(v) for v in CHAOS_EVENTS.values())

    for team, token_data in TEAM_TOKENS.items():
        team_data = TEAM_DATA.get(team, {})
        start = team_data.get("start_time", now)
        completed_time = team_data.get("completed_time")
        duration = (completed_time - start) if completed_time else (now - start)
        chaos_count = len(CHAOS_EVENTS.get(team, []))

        teams_out.append({
            "team": team,
            "seen_count": team_data.get("seen_count", 0),
            "submissions": team_data.get("submissions", 0),
            "remaining": token_data["remaining"],
            "completed": team in COMPLETED_TEAMS,
            "chaos": chaos_count,
            "tokens_issued": team_data.get("tokens_issued", 1),
            "duration": duration,
        })

    return {
        "teams": teams_out,
        "total_chaos": total_chaos,
        "total_words": len(WORDS),
    }


# -------------------------------------------------------------
# Dashboard HTML
# -------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard():
    path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    with open(path) as f:
        return f.read()


# -------------------------------------------------------------
# Health Check
# -------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "uptime": time.time()}
