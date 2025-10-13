from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import random, time, uuid, os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

TOKEN_LIMIT = 20
IDLE_TIMEOUT = 1800  # 30 minutes

TEAM_TOKENS = {}   # team -> token dict: {token, remaining, max, timestamp}
TEAM_DATA = {}     # team -> seen_count, submissions, etc.
CHAOS_EVENTS = {}  # team -> list of chaos events
COMPLETED_TEAMS = set()

FULL_TEXT = (
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
)
WORDS = FULL_TEXT.split()
FRAGMENTS = [{"word": word, "position": idx} for idx, word in enumerate(WORDS)]

@app.post("/auth")
def issue_token(request: Request, team: str = Header(None)):
    if not team:
        raise HTTPException(status_code=400, detail="Missing team header")

    now = time.time()
    existing = TEAM_TOKENS.get(team)

    # Return existing active token if still valid
    if existing and now - existing["timestamp"] < IDLE_TIMEOUT and existing["remaining"] > 0:
        return {"token": existing["token"], "team": team, "remaining": existing["remaining"]}

    # Generate new token
    token = str(uuid.uuid4())
    TEAM_TOKENS[team] = {
        "token": token,
        "remaining": TOKEN_LIMIT,
        "max": TOKEN_LIMIT,
        "timestamp": now
    }

    team_data = TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 0, "start_time": now, "duration": 0})
    team_data["tokens_issued"] += 1
    return {"token": token, "team": team, "remaining": TOKEN_LIMIT}

@app.get("/status")
def get_status():
    now = time.time()
    teams = []

    for team, token_data in TEAM_TOKENS.items():
        if now - token_data["timestamp"] > IDLE_TIMEOUT:
            continue

        data = TEAM_DATA.get(team, {})
        duration = now - data.get("start_time", now)
        chaos_count = len(CHAOS_EVENTS.get(team, []))

        teams.append({
            "team": team,
            "seen_count": data.get("seen_count", 0),
            "submissions": data.get("submissions", 0),
            "remaining": token_data["remaining"],
            "tokens_issued": data.get("tokens_issued", 0),
            "completed": team in COMPLETED_TEAMS,
            "chaos": chaos_count,
            "duration": duration,
        })

    total_chaos = sum(len(v) for v in CHAOS_EVENTS.values())

    return {
        "teams": teams,
        "total_chaos": total_chaos,
        "total_words": len(WORDS),
    }

@app.get("/fragment")
def get_fragment(request: Request, team: str = Header(None), token: str = Header(None)):
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    if not token_data or token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token")

    if token_data["remaining"] <= 0:
        raise HTTPException(status_code=403, detail="Token expired")

    token_data["remaining"] -= 1
    token_data["timestamp"] = time.time()

    TEAM_DATA.setdefault(team, {}).setdefault("seen_count", 0)
    TEAM_DATA[team]["seen_count"] += 1

    # Chaos simulation
    chaos_chance = random.random()
    if chaos_chance < 0.05:
        CHAOS_EVENTS.setdefault(team, []).append({"ts": time.time(), "type": "418"})
        raise HTTPException(status_code=418, detail="I'm a teapot — chaos mode engaged!")
    elif chaos_chance < 0.10:
        CHAOS_EVENTS.setdefault(team, []).append({"ts": time.time(), "type": "500"})
        raise HTTPException(status_code=500, detail="Internal Server Error – chaos simulation.")
    elif chaos_chance < 0.15:
        CHAOS_EVENTS.setdefault(team, []).append({"ts": time.time(), "type": "timeout"})
        time.sleep(random.uniform(1.5, 3.0))

    return random.choice(FRAGMENTS)

@app.post("/validate")
def validate_submission(request: Request, team: str = Header(None)):
    """Validate full sentence submission."""
    if not team:
        raise HTTPException(status_code=400, detail="Missing team header")

    data = TEAM_DATA.get(team)
    if not data:
        raise HTTPException(status_code=404, detail="Unknown team")

    data["submissions"] += 1

    if data.get("seen_count", 0) >= len(WORDS):
        COMPLETED_TEAMS.add(team)
        data["duration"] = time.time() - data["start_time"]
        return {"status": "completed", "team": team, "duration": data["duration"]}
    return {"status": "incomplete", "team": team}

# 🧭 Serve the dashboard directly
@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard():
    if not os.path.exists("dashboard.html"):
        raise HTTPException(status_code=404, detail="Dashboard file not found")
    with open("dashboard.html", "r", encoding="utf-8") as f:
        return f.read()
