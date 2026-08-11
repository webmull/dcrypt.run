# Decrypt the Narrative

A chaos-engineering challenge, built for a university hackathon and run live at
**[dcrypt.run](https://dcrypt.run)**.

A hidden narrative is shattered into single-word fragments. Teams pull the
fragments back one request at a time from an API that misbehaves on purpose —
truncated payloads, HTML pretending to be JSON, bogus status codes, silently
drained quotas — and reassemble the original text. A live scoreboard tracks
every team's progress on the big screen.

The challenge is not "can you call an HTTP endpoint". It is: **can you write a
client that keeps working when the server is actively lying to you?**

---

## Contents

- [How the challenge works](#how-the-challenge-works)
- [The chaos catalogue](#the-chaos-catalogue)
- [What counts as progress](#what-counts-as-progress)
- [API reference](#api-reference)
- [The scoreboard](#the-scoreboard)
- [Running it locally](#running-it-locally)
- [Configuration](#configuration)
- [Deploying](#deploying)
- [Running it as an event](#running-it-as-an-event)
- [Tests](#tests)
- [Project layout](#project-layout)
- [Design notes](#design-notes)
- [Licence](#licence)

---

## How the challenge works

Three endpoints, one loop:

```
POST /auth       →  register a team, get a token and a request quota
GET  /fragment   →  spend one unit of quota, maybe receive one word
POST /validate   →  submit the reassembled narrative
```

```bash
# 1. Register
curl -X POST https://dcrypt.run/auth -H "team: bit-shifters"
# {"token":"3f2b9c14-...","team":"bit-shifters","remaining":20}

# 2. Collect (this is the part that fights back)
curl https://dcrypt.run/fragment \
  -H "team: bit-shifters" -H "token: 3f2b9c14-..."
# {"word":"microservices","position":4}

# 3. Submit, once you have every word
curl -X POST https://dcrypt.run/validate \
  -H "team: bit-shifters" -H "token: 3f2b9c14-..." \
  -H "Content-Type: application/json" \
  -d '{"submission":"In a world built on microservices ..."}'
```

Each fragment carries its `position`, so reassembly is a sorting problem, not a
guessing game. The difficulty is entirely in **surviving the transport**.

### The quota economy

A token starts with 20 requests. Every `/fragment` call spends one **whether or
not it returns anything useful** — a 500, an empty body, a garbled word all
cost the same as a clean fragment. When the quota runs out, call `/auth` again
for a fresh token; progress is preserved across re-authentication. Re-auths are
counted and displayed, so grinding is visible.

Words are served at random, so this is a coupon-collector problem: expect to
need several times more requests than there are words. Measured against the
live deployment with a 76-word narrative at the default 55% chaos rate, a
working client took **880 requests and 46 re-authentications** — roughly 12
requests per word. Tune `TOKEN_LIMIT` if that is more grinding than you want
for your session length.

Coverage alone is not the whole cost. Because `reverse_text` and
`unicode_garble` corrupt a word without announcing it, a client generally needs
*several* samples of each position and a majority vote to be confident — the
run above reached full coverage at request 480 but did not submit correctly
until request 880.

---

## The chaos catalogue

Roughly 55% of `/fragment` calls misbehave. Twelve events, all of them things
that happen to real distributed systems:

| Event | What the client sees |
|---|---|
| `delay` | The real fragment, 0.5–5s late |
| `slow_burst` | The real fragment after several stacked pauses |
| `token_drain` | The real fragment, and 1–3 extra quota silently gone |
| `malformed_json` | `200 OK`, `Content-Type: application/json`, truncated body |
| `broken_json` | `200 OK`, body that was never JSON |
| `html_injection` | `200 OK`, an HTML error page labelled as JSON |
| `empty_response` | `200 OK` with `{}` |
| `error_code` | `418`, `429`, `500` or `504` |
| `duplicate_fragment` | `{"fragments": [x, x]}` — a completely different shape |
| `out_of_order` | A genuine fragment, but always from the tail of the text |
| `reverse_text` | A fragment whose `word` is reversed |
| `unicode_garble` | A fragment whose `word` has junk appended |

Delays scale with how long a team has been running, up to 2×, so the system
gets *worse* under sustained load — which is the point.

**The lesson each event teaches:** don't trust the status code, don't trust the
content type, don't assume the response shape is stable, and don't assume a
successful HTTP call means you got data.

---

## What counts as progress

A team's score is **distinct words received intact** — not requests made.

- Counts: `delay`, `slow_burst`, `token_drain`, `duplicate_fragment`,
  `out_of_order`. These deliver a genuine word with a correct position.
- Does not count: `reverse_text`, `unicode_garble` (corrupted), and every
  event that carries no word at all.

`/validate` refuses to score a submission until coverage is complete, and the
`403` tells the team exactly where they are (`"You've seen 41 of 50 words"`).
This matters: the gate is a real check against server-side state, so it cannot
be satisfied by guessing, and a team is never left staring at a bare
"incorrect" with no idea whether the problem is their parser or their
reassembly.

Comparison ignores case, punctuation and extra whitespace, so teams are marked
on recovering the words and their order — not on reproducing the punctuation.

---

## API reference

Interactive docs, generated from [`openapi.yaml`](openapi.yaml), are served at
[`/docs`](https://dcrypt.run/docs). The spec is hand-written and served
verbatim, and [`tests/test_openapi.py`](tests/test_openapi.py) asserts it
matches the implementation — route by route, field by field — so the published
documentation cannot quietly drift from the code.

| Endpoint | Purpose |
|---|---|
| `POST /auth` | Register a team; issue or reuse a token |
| `GET /fragment` | Pull one word. Chaos lives here |
| `POST /validate` | Submit the reassembled narrative |
| `GET /status` | Live scoreboard data (public, read-only) |
| `GET /health` | Liveness probe |
| `GET /` | The scoreboard |

### Team names

`1–20` characters, letters, digits, spaces, underscores and hyphens, and must
start with a letter or digit. Validated server-side against a single regex.

The rule is deliberately strict because team names are rendered on a projected
scoreboard: `<svg onload=alert()>` is exactly 20 characters, and a name that is
nothing but a space would render as an invisible row. HTTP headers are ASCII,
so no emoji.

---

## The scoreboard

[`dashboard.html`](dashboard.html) — a single self-contained file, no build
step, no external scripts. It polls `/status` every 1.5s and shows each team's
word coverage, remaining quota, chaos absorbed and elapsed time. Completed
teams sort to the top by finishing time.

Each team carries a state icon, so the room can read the board at a glance
without a legend:

| Icon | State | Meaning |
|---|---|---|
| Pulsing green dot | Decoding | A request within the last 60s |
| Hollow pause mark | Idle | Nothing for 60s+ — stuck, debugging, or gone |
| Gold circled tick | Decrypted | Submitted correctly |

Idle is driven by `idle_seconds` on `/status`, which is the age of the team's
last request. It is genuinely useful while running an event: a board full of
idle teams means the room is stuck and the briefing needs revisiting, not that
the challenge is too hard.

It is written to be projected in a room, and to survive being projected in a
room:

- Team names are written with `textContent`, never interpolated into markup.
- Cards are cached per team and mutated in place, so bar transitions actually
  animate instead of restarting 40 times a minute.
- A failed poll shows a "connection lost" line with the age of the last good
  update, rather than freezing on stale numbers or going blank. A resilience
  challenge whose own scoreboard falls over is a bad look.
- No CDN dependencies. Nothing to fail at the worst possible moment.
- `prefers-reduced-motion` is respected.

---

## Running it locally

Requires Python 3.11+.

```bash
git clone https://github.com/webmull/dcrypt.run.git
cd dcrypt.run

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export NARRATIVE_TEXT="Whatever sentence you want teams to reassemble."
uvicorn decrypt_api:app --reload
```

Then open <http://127.0.0.1:8000> for the scoreboard and
<http://127.0.0.1:8000/docs> for the API.

Without `NARRATIVE_TEXT` the server still starts, but serves a placeholder
narrative and reports `"narrative_configured": false` on `/health`.

### Developing against it

Turn the chaos off to get a clean loop working first, then turn it up:

```bash
CHAOS_RATE=0 NARRATIVE_TEXT="one two three" uvicorn decrypt_api:app --reload
```

---

## Configuration

Everything is environment-driven, so the same image can run a practice round
and a live final.

| Variable | Default | Purpose |
|---|---|---|
| `NARRATIVE_TEXT` | placeholder | **The narrative teams must reassemble.** Whitespace is normalised |
| `TOKEN_LIMIT` | `20` | Requests per token |
| `CHAOS_RATE` | `0.55` | Probability a `/fragment` call misbehaves (`0`–`1`) |
| `IDLE_TIMEOUT` | `1800` | Seconds before an inactive team is evicted |
| `ACTIVE_LIMIT` | `150` | Maximum concurrent teams |
| `STATE_FILE` | unset | Path for state snapshots. Unset disables persistence |
| `SNAPSHOT_INTERVAL` | `15` | Seconds between snapshots |
| `CLEANUP_INTERVAL` | `60` | Seconds between idle sweeps |

An unparseable numeric value falls back to its default rather than refusing to
boot — a typo in a deploy variable should not take the challenge offline
mid-event.

### The narrative is never committed

`NARRATIVE_TEXT` exists because this repo is public and the answer must not be.
Nothing in the tree contains the live narrative: not the source, not the
OpenAPI examples, not the static assets, not the scoreboard. Three tests
([`test_status.py`](tests/test_status.py),
[`test_openapi.py`](tests/test_openapi.py),
[`test_dashboard_safety.py`](tests/test_dashboard_safety.py)) assert that it
never leaks into a response, the published spec, or the page.

> An earlier version of this project shipped the answer as
> `static/solution.txt`, which was publicly downloadable for the duration of
> the event. Hence the tests.

---

## Deploying

The [`Procfile`](Procfile) works on any platform that reads one:

```
web: uvicorn decrypt_api:app --host 0.0.0.0 --port $PORT
```

### DigitalOcean App Platform

Build command is the default Python buildpack; run command is
`uvicorn decrypt_api:app --host 0.0.0.0 --port $PORT`. Set at minimum:

| Key | Value | Scope |
|---|---|---|
| `NARRATIVE_TEXT` | your narrative, as one line | Run time, **encrypted** |

Set it as an *encrypted* variable so it is not visible in the app spec or the
dashboard. Everything else has a sensible default.

**Run a single instance.** State lives in memory, so two instances would each
hold half the teams and the scoreboard would flicker between them. Scale up,
not out.

### Surviving a restart

Set `STATE_FILE=/data/state.json` to snapshot the scoreboard every 15s and
restore it on boot. Snapshots are fingerprinted against the narrative, so a
snapshot from a different text is refused rather than silently corrupting
scores.

This needs a **persistent volume** to be worth anything. On App Platform the
container filesystem is ephemeral, so without an attached volume a restart
still starts from empty — leave `STATE_FILE` unset there and accept it, or
attach storage.

---

## Running it as an event

Some things learned from running this live:

- **Set `NARRATIVE_TEXT` before you announce the URL.** Check
  `/health` reports `"narrative_configured": true`.
- **Pick the narrative for length.** Words drive difficulty far more than
  chaos rate does; the coupon-collector effect means doubling the word count
  roughly doubles the grind. 40–80 words suits a few hours.
- **`CHAOS_RATE` is the difficulty dial**, and it is safe to change mid-event —
  it is read per request, so no restart is needed. Drop it if the room is
  stuck.
- **Put `/docs` on the screen during the briefing.** The chaos catalogue is
  documented there in full; teams that read it do dramatically better, which is
  the lesson.
- **`IDLE_TIMEOUT` evicts after 30 minutes**, which clears the board of teams
  that wandered off. Raise it if you are running a long session with breaks.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

204 tests, roughly a second to run. They cover:

- **Auth** — name validation (including the XSS and blank-name payloads that
  the rules exist to block), token reuse, re-auth preserving progress, the
  active-team limit.
- **Chaos** — every one of the twelve events, individually pinned with a
  seeded RNG: what it returns, that it costs quota, and whether it credits
  coverage. `test_catalogue_matches_implementation` fails if a thirteenth is
  added without documenting it.
- **Scoring** — the coverage gate, that being told "not yet" is free but a real
  attempt costs, normalisation across case and punctuation, and that word
  order still matters.
- **Telemetry** — `/status` shape, that it never mutates state, that idle
  eviction works, and that no response leaks the narrative.
- **Config** — every environment variable, including the placeholder fallback
  and bad-value handling.
- **Persistence** — snapshot round-trip, fingerprint mismatch, corrupt file,
  unwritable path.
- **Spec drift** — `openapi.yaml` against the live app, both directions.
- **Scoreboard** — source-level guards against the escaping and CDN mistakes.

---

## Project layout

```
decrypt_api.py      The whole API — endpoints, chaos, state, persistence
dashboard.html      Self-contained scoreboard
openapi.yaml        Hand-written spec, served verbatim at /docs
Procfile            Deployment entrypoint
requirements.txt    Runtime dependencies
tests/              pytest suite
static/             Favicons and manifest
```

One module, because the whole thing is about 500 lines and splitting it would
cost more in navigation than it saved in tidiness.

---

## Design notes

A few decisions worth explaining, since the obvious alternatives are wrong in
interesting ways.

**Quota is charged before chaos rolls.** Failure has to cost something or
"retry forever" beats writing a real client.

**Coverage tracks distinct words, server-side.** An earlier version counted
requests, which meant the completion gate could be satisfied while a third of
the words were still missing — teams then hit a bare "incorrect submission"
with no idea why. Counting distinct words makes both the gate and the
scoreboard bar mean what they say.

**Handlers are `async`, and chaos delays use `asyncio.sleep`.** With
synchronous handlers, `time.sleep` occupies a threadpool worker; a `slow_burst`
holds one for up to six seconds. At 150 teams that starves the pool and stalls
the entire app, scoreboard included — the failure mode is the app going down
precisely when it is busiest.

**Idle eviction runs on a background task, not inside `GET /status`.** It used
to live in the status handler, which meant cleanup only happened while somebody
was watching the scoreboard, and a read endpoint quietly mutated state.

**Chaos events that deliver real words still credit coverage.**
`out_of_order` and `duplicate_fragment` hand over genuine, correctly-positioned
data — awkwardly shaped, but true. Refusing to count them would punish teams
for parsing them correctly.

**There is no per-team secret.** A team could authenticate as another team and
drain their quota. This is a deliberate non-goal: the threat model is a room of
students at a hackathon, and a claim secret adds friction to the first thirty
seconds of every team's experience — which is exactly where you least want it.
Don't reuse this shape for anything adversarial.

---

## Licence

MIT — see [LICENSE](LICENSE).

Built by [Adam Davis](https://adamdavis.co.uk).
