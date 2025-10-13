# decrypt_api.py
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import random, time, uuid, os, json

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
)
WORDS = FULL_TEXT.split()
FRAGMENTS = [{"word": w, "position": i} for i, w in enumerate(WORDS)]

# --- State Stores ---
TEAM_TOKENS = {}       # {team: {"token": str, "remaining": int, "max": int, "timestamp": float}}
TEAM_DATA = {}         # {team: {"seen_count": int, "submissions": int, "tokens_issued": int, "start_time": float}}
CHAOS_EVENTS = {}      # {team: [chaos events]}
COMPLETED_TEAMS = set()

# -------------------------------------------------------------
# Utility
# -------------------------------------------------------------
def current_time() -> float:
    return time.time()

def chaos_roll(team: str):
    """Simulate chaos with small probability."""
    if random.random() < 0.05:  # 5% chance
        CHAOS_EVENTS.setdefault(team, []).append({"ts": current_time(), "type": "chaos"})
        raise HTTPException(status_code=random.choice([418, 429, 500, 504]), detail="Chaos event triggered")

# -------------------------------------------------------------
# Auth Endpoint
# -------------------------------------------------------------
@app.post("/auth")
def issue_token(request: Request, team: str = Header(None)):
    """Issue a token or return existing valid one."""
    if not team:
        raise HTTPException(status_code=400, detail="Missing team header")

    now = current_time()
    existing = TEAM_TOKENS.get(team)

    # reuse valid token
    if existing and existing["remaining"] > 0:
        existing["timestamp"] = now
        return {"token": existing["token"], "team": team, "remaining": existing["remaining"]}

    # otherwise issue new one
    token = str(uuid.uuid4())
    TEAM_TOKENS[team] = {"token": token, "remaining": TOKEN_LIMIT, "max": TOKEN_LIMIT, "timestamp": now}
    data = TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 0})
    data["tokens_issued"] += 1
    data.setdefault("start_time", now)
    return {"token": token, "team": team, "remaining": TOKEN_LIMIT}

# -------------------------------------------------------------
# Fragment Endpoint (auto refresh)
# -------------------------------------------------------------
@app.get("/fragment")
def get_fragment(request: Request, team: str = Header(None), token: str = Header(None)):
    """Return a random fragment, applying chaos and auto-refresh logic."""
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    now = current_time()

    # --- Token missing or expired from memory ---
    if not token_data:
        new_token = str(uuid.uuid4())
        TEAM_TOKENS[team] = {"token": new_token, "remaining": TOKEN_LIMIT, "max": TOKEN_LIMIT, "timestamp": now}
        TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 0, "start_time": now})
        TEAM_DATA[team]["tokens_issued"] += 1
        raise HTTPException(status_code=401, detail=f"Token expired or reset. New token issued: {new_token}")

    # --- Token mismatch ---
    if token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token for team")

    # --- Out of requests ---
    if token_data["remaining"] <= 0:
        raise HTTPException(status_code=403, detail="Token limit reached")

    # Apply chaos chance
    chaos_roll(team)

    # normal success
    token_data["remaining"] -= 1
    token_data["timestamp"] = now

    TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 1, "start_time": now})
    TEAM_DATA[team]["seen_count"] += 1

    return random.choice(FRAGMENTS)

# -------------------------------------------------------------
# Validate Endpoint
# -------------------------------------------------------------
@app.post("/validate")
def validate_submission(request: Request, team: str = Header(None), token: str = Header(None)):
    """Simulate validation of the reconstructed sentence."""
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    if not token_data or token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token")

    TEAM_DATA.setdefault(team, {}).setdefault("submissions", 0)
    TEAM_DATA[team]["submissions"] += 1

    # 15% chance of chaos here too
    if random.random() < 0.15:
        raise HTTPException(status_code=random.choice([418, 500, 504]), detail="Validation chaos event")

    COMPLETED_TEAMS.add(team)
    return {"team": team, "message": "Validation successful", "completed": True}

# -------------------------------------------------------------
# Status Endpoint (for dashboard)
# -------------------------------------------------------------
@app.get("/status")
def get_status():
    now = current_time()
    teams_out = []
    total_chaos = sum(len(v) for v in CHAOS_EVENTS.values())

    for team, token_data in TEAM_TOKENS.items():
        if now - token_data["timestamp"] > IDLE_TIMEOUT:
            continue

        team_data = TEAM_DATA.get(team, {})
        duration = now - team_data.get("start_time", now)
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
# Serve dashboard
# -------------------------------------------------------------
@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard():
    """Serve local dashboard file."""
    path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    with open(path) as f:
        return f.read()

# -------------------------------------------------------------
# Health endpoint
# -------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "uptime": time.time()}
