"""Decrypt the Narrative — a chaos-engineering API challenge.

Teams authenticate, pull word fragments from a deliberately unreliable
endpoint, and reassemble the hidden narrative. The narrative itself is read
from the NARRATIVE_TEXT environment variable so that it never lives in
source control.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent

# -------------------------------------------------------------
# Configuration — everything here is overridable at deploy time
# -------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


TOKEN_LIMIT = _env_int("TOKEN_LIMIT", 20)
IDLE_TIMEOUT = _env_int("IDLE_TIMEOUT", 1800)  # 30 minutes
ACTIVE_LIMIT = _env_int("ACTIVE_LIMIT", 150)
CHAOS_RATE = _env_float("CHAOS_RATE", 0.55)
CLEANUP_INTERVAL = _env_int("CLEANUP_INTERVAL", 60)
SNAPSHOT_INTERVAL = _env_int("SNAPSHOT_INTERVAL", 15)
STATE_FILE = os.getenv("STATE_FILE")  # unset = no persistence

PLACEHOLDER_TEXT = (
    "Set the NARRATIVE TEXT environment variable to the narrative you want teams "
    "to reassemble, because this placeholder is all they can see until you do."
)

_raw_text = " ".join(os.getenv("NARRATIVE_TEXT", "").split())
USING_PLACEHOLDER = not _raw_text
FULL_TEXT = _raw_text or PLACEHOLDER_TEXT

WORDS = FULL_TEXT.split()
FRAGMENTS = [{"word": w, "position": i} for i, w in enumerate(WORDS)]
TEXT_FINGERPRINT = hashlib.sha256(FULL_TEXT.encode()).hexdigest()[:12]

# 1–20 chars, must start with a letter or digit and must not end in a space, so
# a name can never render as a blank row on the scoreboard.
TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _-]{0,18}[A-Za-z0-9_-])?$")
START_TIME = time.time()

CHAOS_TYPES = [
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

# -------------------------------------------------------------
# State stores
# -------------------------------------------------------------
TEAM_TOKENS: dict[str, dict] = {}
TEAM_DATA: dict[str, dict] = {}
CHAOS_EVENTS: dict[str, list] = {}
COMPLETED_TEAMS: set[str] = set()
RECENT_CHAOS: deque = deque(maxlen=30)  # feeds the dashboard ticker


def current_time() -> float:
    return time.time()


def new_team_record(now: float) -> dict:
    return {
        "seen": set(),          # distinct word positions delivered intact
        "requests_ok": 0,       # fragment calls that returned a clean fragment
        "submissions": 0,
        "tokens_issued": 0,
        "start_time": now,
    }


def team_record(team: str) -> dict:
    return TEAM_DATA.setdefault(team, new_team_record(current_time()))


def mark_seen(team: str, positions) -> None:
    """Record word positions that reached the team with their true value."""
    record = team_record(team)
    record["seen"].update(positions)
    record["requests_ok"] += 1


# -------------------------------------------------------------
# Persistence (opt-in via STATE_FILE)
# -------------------------------------------------------------
def save_state() -> None:
    if not STATE_FILE:
        return
    payload = {
        "fingerprint": TEXT_FINGERPRINT,
        "saved_at": current_time(),
        "tokens": TEAM_TOKENS,
        "teams": {
            team: {**record, "seen": sorted(record["seen"])}
            for team, record in TEAM_DATA.items()
        },
        "chaos": CHAOS_EVENTS,
        "completed": sorted(COMPLETED_TEAMS),
    }
    tmp = Path(f"{STATE_FILE}.tmp")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload))
        tmp.replace(STATE_FILE)
    except OSError as exc:
        print(f"[state] snapshot failed: {exc}")


def load_state() -> None:
    if not STATE_FILE or not Path(STATE_FILE).exists():
        return
    try:
        payload = json.loads(Path(STATE_FILE).read_text())
    except (OSError, ValueError) as exc:
        print(f"[state] snapshot unreadable, starting fresh: {exc}")
        return

    # A snapshot taken against a different narrative would carry meaningless
    # word positions, so refuse it rather than corrupt the scoreboard.
    if payload.get("fingerprint") != TEXT_FINGERPRINT:
        print("[state] snapshot belongs to a different narrative, ignoring")
        return

    TEAM_TOKENS.update(payload.get("tokens", {}))
    for team, record in payload.get("teams", {}).items():
        TEAM_DATA[team] = {**record, "seen": set(record.get("seen", []))}
    CHAOS_EVENTS.update(payload.get("chaos", {}))
    COMPLETED_TEAMS.update(payload.get("completed", []))
    print(f"[state] restored {len(TEAM_TOKENS)} team(s) from {STATE_FILE}")


def evict_idle_teams() -> int:
    cutoff = current_time() - IDLE_TIMEOUT
    stale = [team for team, tok in TEAM_TOKENS.items() if tok["timestamp"] < cutoff]
    for team in stale:
        TEAM_TOKENS.pop(team, None)
        TEAM_DATA.pop(team, None)
        CHAOS_EVENTS.pop(team, None)
        COMPLETED_TEAMS.discard(team)
    return len(stale)


# -------------------------------------------------------------
# Background housekeeping
# -------------------------------------------------------------
async def housekeeping() -> None:
    """Evict idle teams and snapshot state without waiting on a dashboard hit."""
    while True:
        await asyncio.sleep(min(CLEANUP_INTERVAL, SNAPSHOT_INTERVAL))
        try:
            evicted = evict_idle_teams()
            if evicted:
                print(f"[cleanup] evicted {evicted} idle team(s)")
            save_state()
        except Exception as exc:  # never let the loop die mid-event
            print(f"[housekeeping] {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if USING_PLACEHOLDER:
        print("[config] NARRATIVE_TEXT is unset — serving the placeholder narrative")
    print(f"[config] narrative={len(WORDS)} words fingerprint={TEXT_FINGERPRINT}")
    load_state()
    task = asyncio.create_task(housekeeping())
    try:
        yield
    finally:
        task.cancel()
        save_state()


app = FastAPI(title="Decrypt the Narrative API", lifespan=lifespan)

with open(BASE_DIR / "openapi.yaml") as f:
    custom_spec = yaml.safe_load(f)

app.openapi = lambda: custom_spec
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Teams call this from their own scripts, so any origin may read it. No cookies
# are involved, so credentials stay off — "*" plus credentials is a combination
# browsers reject anyway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# -------------------------------------------------------------
# Token validation
# -------------------------------------------------------------
def validate_token(team: str, token: str, require_remaining: bool = True) -> dict:
    """Shared validator for token presence, expiry, and remaining count."""
    if not team or not token:
        raise HTTPException(status_code=400, detail="Missing team or token header")

    token_data = TEAM_TOKENS.get(team)
    now = current_time()

    if not token_data:
        raise HTTPException(status_code=401, detail="No active token found for team")

    if now - token_data["timestamp"] > IDLE_TIMEOUT:
        raise HTTPException(status_code=401, detail="Token expired. Please re-authenticate.")

    if token_data["token"] != token:
        raise HTTPException(status_code=403, detail="Invalid token for team")

    if require_remaining and token_data["remaining"] <= 0:
        raise HTTPException(status_code=403, detail="Token limit reached")

    token_data["timestamp"] = now
    return token_data


# -------------------------------------------------------------
# Chaos
# -------------------------------------------------------------
async def chaos_roll(team: str):
    """Inject controlled chaos into /fragment requests.

    Returns a response body to send back, or None to fall through to a normal
    fragment (which is what the purely time- and quota-based events do).
    """
    if random.random() >= CHAOS_RATE:
        return None

    chaos_type = random.choice(CHAOS_TYPES)
    event = {"ts": current_time(), "type": chaos_type, "team": team}
    CHAOS_EVENTS.setdefault(team, []).append({"ts": event["ts"], "type": chaos_type})
    RECENT_CHAOS.append(event)

    elapsed_minutes = (current_time() - team_record(team)["start_time"]) / 60
    scale = min(1 + (elapsed_minutes * 0.1), 2.0)

    # --- Delay ---
    if chaos_type == "delay":
        await asyncio.sleep(random.uniform(0.5, 2.5) * scale)
        return None

    # --- Malformed JSON ---
    if chaos_type == "malformed_json":
        return PlainTextResponse('{"word": "spl1t", "pos":', media_type="application/json")

    # --- Broken JSON ---
    if chaos_type == "broken_json":
        return PlainTextResponse("{not valid json at all", media_type="application/json")

    # --- Error Code ---
    if chaos_type == "error_code":
        raise HTTPException(
            status_code=random.choice([418, 429, 500, 504]),
            detail="Chaos error event triggered",
        )

    # --- Duplicate Fragment (the word itself is genuine, so it counts) ---
    if chaos_type == "duplicate_fragment":
        frag = random.choice(FRAGMENTS)
        mark_seen(team, [frag["position"]])
        return {"fragments": [frag, frag]}

    # --- Empty Response ---
    if chaos_type == "empty_response":
        return {}

    # --- HTML Injection ---
    if chaos_type == "html_injection":
        return PlainTextResponse(
            "<html><body><h1>Error</h1></body></html>", media_type="application/json"
        )

    # --- Slow Burst ---
    if chaos_type == "slow_burst":
        for _ in range(random.randint(3, 6)):
            await asyncio.sleep(random.uniform(0.3, 1.0))
        return None

    # --- Token Drain ---
    if chaos_type == "token_drain":
        token_data = TEAM_TOKENS.get(team)
        if token_data:
            token_data["remaining"] = max(0, token_data["remaining"] - random.randint(1, 3))
        return None

    # --- Reverse Text (corrupted, so it does not count as seen) ---
    if chaos_type == "reverse_text":
        frag = dict(random.choice(FRAGMENTS))
        frag["word"] = frag["word"][::-1]
        return frag

    # --- Unicode Garble (corrupted, so it does not count as seen) ---
    if chaos_type == "unicode_garble":
        frag = dict(random.choice(FRAGMENTS))
        frag["word"] = frag["word"] + random.choice(["μΩλ", "Ʃ∂∆", "☠️"])
        return frag

    # --- Out of Order (genuine word from the tail of the text) ---
    if chaos_type == "out_of_order":
        frag = random.choice(FRAGMENTS[-10:])
        mark_seen(team, [frag["position"]])
        return frag

    return None


# -------------------------------------------------------------
# Auth
# -------------------------------------------------------------
@app.post("/auth")
async def issue_token(team: str = Header(None)):
    """Issue a token, or hand back the team's existing one if it still has quota."""
    if not team:
        raise HTTPException(status_code=400, detail="Missing team header")

    if not TEAM_NAME_RE.fullmatch(team):
        raise HTTPException(
            status_code=400,
            detail="Invalid team name: 1-20 characters, letters, digits, spaces, "
                   "underscores and hyphens only, starting with a letter or digit",
        )

    if team not in TEAM_TOKENS and len(TEAM_TOKENS) >= ACTIVE_LIMIT:
        raise HTTPException(status_code=403, detail="Team limit reached. Please try again later.")

    now = current_time()
    existing = TEAM_TOKENS.get(team)

    if existing and existing["remaining"] > 0:
        existing["timestamp"] = now
        return {"token": existing["token"], "team": team, "remaining": existing["remaining"]}

    token = str(uuid.uuid4())
    TEAM_TOKENS[team] = {
        "token": token,
        "remaining": TOKEN_LIMIT,
        "max": TOKEN_LIMIT,
        "timestamp": now,
    }
    record = team_record(team)
    record["tokens_issued"] += 1
    return {"token": token, "team": team, "remaining": TOKEN_LIMIT}


# -------------------------------------------------------------
# Fragment (chaos lives here)
# -------------------------------------------------------------
@app.get("/fragment")
async def get_fragment(team: str = Header(None), token: str = Header(None)):
    """Return a random fragment, or whatever chaos decides to return instead."""
    token_data = validate_token(team, token)
    token_data["remaining"] -= 1

    chaos_result = await chaos_roll(team)
    if chaos_result is not None:
        return chaos_result

    fragment = random.choice(FRAGMENTS)
    mark_seen(team, [fragment["position"]])
    return fragment


# -------------------------------------------------------------
# Validate (no chaos)
# -------------------------------------------------------------
@app.post("/validate")
async def validate_submission(payload: dict, team: str = Header(None), token: str = Header(None)):
    """Check a team's reconstructed narrative against the canonical text.

    The coverage gate is free; a genuine attempt costs a token.
    """
    token_data = validate_token(team, token)
    record = team_record(team)

    if len(record["seen"]) < len(WORDS):
        raise HTTPException(
            status_code=403,
            detail=f"You've seen {len(record['seen'])} of {len(WORDS)} words. "
                   "Keep exploring fragments before submitting.",
        )

    submitted_text = payload.get("submission")
    if not submitted_text or not isinstance(submitted_text, str):
        raise HTTPException(status_code=400, detail="Missing submission")

    token_data["remaining"] -= 1
    record["submissions"] += 1

    def normalize(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    if normalize(submitted_text) != normalize(FULL_TEXT):
        raise HTTPException(status_code=400, detail="Incorrect submission")

    completed_at = current_time()
    record["completed_time"] = completed_at
    COMPLETED_TEAMS.add(team)
    save_state()

    return {
        "team": team,
        "status": "success",
        "message": "Correct submission! Challenge complete.",
        "completed_at": completed_at,
        "duration": completed_at - record["start_time"],
    }


# -------------------------------------------------------------
# Status (read-only; the dashboard polls this)
# -------------------------------------------------------------
@app.get("/status")
async def get_status():
    now = current_time()
    total_words = len(WORDS)
    teams_out = []

    for team, token_data in TEAM_TOKENS.items():
        record = TEAM_DATA.get(team, {})
        start = record.get("start_time", now)
        completed_time = record.get("completed_time")
        teams_out.append({
            "team": team,
            "unique_seen": len(record.get("seen", ())),
            "requests_ok": record.get("requests_ok", 0),
            "submissions": record.get("submissions", 0),
            "remaining": token_data["remaining"],
            "max": token_data.get("max", TOKEN_LIMIT),
            "completed": team in COMPLETED_TEAMS,
            "chaos": len(CHAOS_EVENTS.get(team, ())),
            "tokens_issued": record.get("tokens_issued", 1),
            "duration": (completed_time or now) - start,
        })

    return {
        "teams": teams_out,
        "total_chaos": sum(len(v) for v in CHAOS_EVENTS.values()),
        "total_words": total_words,
        "recent_chaos": list(RECENT_CHAOS)[::-1],
        "uptime": now - START_TIME,
    }


# -------------------------------------------------------------
# Dashboard + health
# -------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def serve_dashboard():
    path = BASE_DIR / "dashboard.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="dashboard.html not found")
    return FileResponse(path, media_type="text/html")


@app.get("/dashboard", include_in_schema=False)
async def redirect_dashboard():
    return RedirectResponse(url="/")


@app.get("/health")
async def health():
    return {
        "ok": True,
        "uptime": current_time() - START_TIME,
        "teams_active": len(TEAM_TOKENS),
        "narrative_configured": not USING_PLACEHOLDER,
    }
