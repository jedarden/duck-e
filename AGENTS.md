# AGENTS.md — DUCK-E

Guidance for AI coding agents (and humans) working in this repository.

## Project overview

DUCK-E (Digitally Unified Conversational Knowledge Engine) is a browser-based
voice assistant for rubber-duck debugging. A FastAPI service brokers a WebRTC
session between the browser and the OpenAI Realtime API, with server-side
tools: weather (Open-Meteo), web search, SSRF-protected URL fetch, per-user
memory, and mid-session voice switching. The backend is a custom
`RealtimeSession` class; an earlier AG2/AutoGen dependency was removed and
only the `ag2.init` client message name remains for compatibility.

```
Browser (WebRTC) → WebSocket → FastAPI → OpenAI Realtime API
```

Key components:

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, WebSocket endpoint `/session`, tool handlers |
| `app/realtime_session.py` | Custom `RealtimeSession` |
| `app/memory.py` | `UserMemoryStore` — per-user facts (JSON file store) |
| `app/config.py` | OpenAI config generation (`get_realtime_config()`) |
| `app/website_files/templates/chat.html` | Chat UI |
| `app/website_files/static/main.js` | WebRTC, cost tracking, transcript rendering |
| `app/middleware/` | Rate limiting, cost protection, security headers |

## Running locally

Requires Python 3.12 and an `OPENAI_API_KEY` with Realtime API access in `.env`
(copy `.env.example`).

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add OPENAI_API_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --no-proxy-headers --env-file .env
```

Docker alternative (publishes `${DUCKE_PORT:-8000}`):

```bash
cp .env.example .env          # add OPENAI_API_KEY
docker compose up --build -d
```

Quick checks: `curl localhost:8000/status` (server up) and
`curl localhost:8000/health/openai` (key accepted by OpenAI → `status: ok`).

## Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest -q tests/test_cost_enforcement.py tests/test_ptt_integration.py   # definition of done
python3 -m pytest -q                                                                # full suite
python3 -m pytest -q tests/security/                                                # security tests
```

`cargo`-style remote CI does not apply here; CI is Argo Workflows (see below).

## House rules

- **Never create `.github/workflows/*`.** GitHub Actions are disabled org-wide
  and must stay disabled. All CI runs on Argo Workflows in the `iad-ci`
  cluster (template: `duck-e-build`), defined in
  `declarative-config/k8s/iad-ci/argo-workflows/`.
- **Work on `main`.** No feature branches or PR-per-change flow; commit to
  `main` and push to Forgejo (`git.ardenone.com`). GitHub is a read-only
  mirror. Never force-push.
- **Stage exact paths.** No `git add -A`, `git add .`, or `git commit -a`.
- **No `:latest` tags** for `ronaldraygun/*` images — pin the semver from
  `VERSION`. Local build tags for testing are fine.
- **Secrets stay out of the repo.** `.env` is gitignored; never commit an API
  key, and never pass one on a command line in examples or tests.
- `docs/` holds historical implementation notes and `notes/` holds per-task
  working notes — check whether a change belongs there before adding to them.
