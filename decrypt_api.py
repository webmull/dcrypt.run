from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import random, time, uuid

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

TEAM_TOKENS = {}
TEAM_DATA = {}
CHAOS_EVENTS = {}
COMPLETED_TEAMS = set()

FULL_TEXT = (
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
    "[narrative redacted from history]"
)

WORDS = FULL_TEXT.split()
FRAGMENTS = [{"word": w, "position": i} for i, w in enumerate(WORDS)]


@app.post("/auth")
def issue_token(request: Request, team: str = Header(None)):
    if not team:
        raise HTTPException(status_code=400, detail="Missing team header")

    now = time.time()
    existing = TEAM_TOKENS.get(team)
    if existing and now - existing["timestamp"] < IDLE_TIMEOUT and existing["remaining"] > 0:
        return {"token": existing["token"], "team": team, "remaining": existing["remaining"]}

    token = str(uuid.uuid4())
    TEAM_TOKENS[team] = {
        "token": token,
        "remaining": TOKEN_LIMIT,
        "max": TOKEN_LIMIT,
        "timestamp": now
    }

    data = TEAM_DATA.setdefault(team, {
        "seen_count": 0,
        "submissions": 0,
        "tokens_issued": 0,
        "start_time": now,
        "duration": 0
    })
    data["tokens_issued"] += 1
    return {"token": token, "team": team, "remaining": TOKEN_LIMIT}


@app.get("/status")
def get_status():
    now = time.time()
    teams = []

    all_team_names = set(TEAM_TOKENS.keys()) | set(COMPLETED_TEAMS) | set(TEAM_DATA.keys())
    for team in all_team_names:
        token_data = TEAM_TOKENS.get(team)
        data = TEAM_DATA.get(team, {})

        seen_count = len(FRAGMENTS) if team in COMPLETED_TEAMS else data.get("seen_count", 0)
        duration = data.get("duration", 0)
        if team not in COMPLETED_TEAMS:
            duration = now - data.get("start_time", now)

        remaining = token_data["remaining"] if token_data else 0

        teams.append({
            "team": team,
            "seen_count": seen_count,
            "submissions": data.get("submissions", 0),
            "remaining": remaining,
            "completed": team in COMPLETED_TEAMS,
            "duration": duration,
            "tokens_issued": data.get("tokens_issued", 0),
            "chaos": len(CHAOS_EVENTS.get(team, []))
        })

    total_chaos = sum(len(v) for v in CHAOS_EVENTS.values())
    return {"teams": teams, "events": CHAOS_EVENTS, "total_words": len(FRAGMENTS), "total_chaos": total_chaos}


@app.get("/fragment")
def get_fragment(request: Request, team: str = Header(None), token: str = Header(None)):
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    if not token_data or token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token")
    if token_data["remaining"] <= 0:
        raise HTTPException(status_code=403, detail="Token expired")

    # ⚡ Enhanced Chaos
    chaos_type = random.choices(
        ["none", "delay", "error", "teapot", "timeout", "throttle", "garbage"],
        weights=[45, 15, 10, 5, 10, 10, 5],
        k=1
    )[0]

    if chaos_type != "none":
        CHAOS_EVENTS.setdefault(team, []).append({"ts": time.time(), "type": chaos_type})
        if chaos_type == "delay":
            time.sleep(random.uniform(0.5, 2.0))
        elif chaos_type == "error":
            raise HTTPException(status_code=500, detail="Chaos event: internal server error")
        elif chaos_type == "teapot":
            raise HTTPException(status_code=418, detail="Chaos event: I'm a teapot")
        elif chaos_type == "timeout":
            time.sleep(random.uniform(3.0, 6.0))
            raise HTTPException(status_code=504, detail="Chaos event: gateway timeout")
        elif chaos_type == "throttle":
            raise HTTPException(status_code=429, detail="Chaos event: too many requests")
        elif chaos_type == "garbage":
            return {"word": "###" * random.randint(1, 3), "position": random.randint(-3, 999)}

    token_data["remaining"] -= 1
    token_data["timestamp"] = time.time()

    data = TEAM_DATA.setdefault(team, {"seen_count": 0, "submissions": 0, "tokens_issued": 1, "start_time": time.time(), "duration": 0})
    if team not in COMPLETED_TEAMS:
        data["seen_count"] = min(len(FRAGMENTS), data.get("seen_count", 0) + 1)
        data["duration"] = time.time() - data.get("start_time", time.time())

    return random.choice(FRAGMENTS)


@app.post("/validate")
def validate_submission(request: Request, team: str = Header(None), token: str = Header(None)):
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    if not token_data or token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token")

    TEAM_DATA.setdefault(team, {"submissions": 0})
    TEAM_DATA[team]["submissions"] += 1

    COMPLETED_TEAMS.add(team)
    TEAM_DATA[team]["seen_count"] = len(FRAGMENTS)
    TEAM_DATA[team]["duration"] = time.time() - TEAM_DATA[team].get("start_time", time.time())

    return {"team": team, "success": True, "message": "Challenge completed successfully!"}
