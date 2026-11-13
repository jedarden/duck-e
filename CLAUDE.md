# DUCK-E - Coding Environment

Agent guidance now lives in [AGENTS.md](AGENTS.md) — project overview, run and
test commands, and house rules (no GitHub Actions, work on `main`, stage exact
paths, no `:latest` image tags). This file keeps the deeper gotchas and
deployment context.

## Project Overview

DUCK-E is a real-time voice assistant built on FastAPI + OpenAI Realtime API. It provides voice-first interaction with tools for weather, web search, web fetch, memory, and voice changing.

## Architecture

```
Browser (WebRTC) → WebSocket → FastAPI → OpenAI Realtime API
                                      ↓
                                 Tools (weather/search/memory/fetch)
```

### Key Components

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, WebSocket endpoint `/session`, tool handlers |
| `app/realtime_session.py` | Custom RealtimeSession (replaced AG2 RealtimeAgent) |
| `app/memory.py` | UserMemoryStore for per-user facts (JSON file store) |
| `app/config.py` | OpenAI config generation (`get_realtime_config()`) |
| `app/website_files/static/main.js` | Frontend: WebRTC, cost tracking, transcript rendering |
| `app/website_files/static/ag2client.js` | WebRTC+WebSocket bridge (originally AG2, now compatible) |
| `app/middleware/` | Rate limiting, cost protection, security headers |

## Development

### Running locally

```bash
# Install deps
pip install -r requirements.txt

# Set required env vars in .env:
# OPENAI_API_KEY=sk-...

# Run dev server (--no-proxy-headers keeps proxy headers from being
# consumed by uvicorn before the app's trusted-proxy validation)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --no-proxy-headers
```

### Docker

```bash
docker build -t duck-e:dev .
docker run --env-file .env -p 8000:8000 duck-e:dev
```

## Important Patterns

### Tool Registration

Tools are registered in `main.py` via `session.register_tool()`:

```python
session.register_tool(
    name="get_current_weather",
    description="Get current weather for a location",
    handler=get_current_weather,
    parameters={...}  # OpenAI JSON schema
)
```

### WebSocket Message Flow

1. Client connects → `RealtimeSession.run()` accepts WebSocket
2. Server fetches ephemeral key from `/v1/realtime/sessions`
3. Server sends `ag2.init` message with config (ephemeral key only, never real API key)
4. Client establishes WebRTC data channel
5. Tool calls flow: `response.function_call_arguments.done` → execute → `conversation.item.create`

### Cost Tracking

- Server-side: `CostProtectionMiddleware` limits per-session spend ($5 default)
- Client-side: `main.js` accumulates usage from `response.done` events, displays hourly estimate
- Backend API calls (web_search via OpenAI Responses API) tracked separately

### Memory System

- User identity: a validated JWT takes precedence; otherwise a configured trusted socket peer can supply `X-authentik-email`/`X-authentik-username`, with `x-forwarded-email`/`x-forwarded-user` as fallbacks. Conflicting values between the header families reject the entire proxy identity. Email is the memory key when present, then username.
- JSON file store: `/data/memory/{user_hash}.json`
- Tools: `save_memory(fact)`, `recall_memories()`
- Memories injected into system message at session start

### Security

- **SSRF protection**: `FetchUrl` validator resolves hostnames, blocks private IPs
- **Rate limiting**: Per-IP limits via `slowapi`
- **Input validation**: `LocationInput`, `SearchQuery`, `FetchUrl` validators
- **CORS**: Origin whitelist in `app.middleware.configure_cors()`
- **Cost protection**: $5/session hard limit, $100 circuit breaker

### Push-to-Talk (PTT) Mode

**Overview**: PTT mode allows users to control when the microphone is active by holding a button or pressing spacebar. This prevents the assistant from listening when not intended.

**UI Elements** (in `chat.html`):
- `.ptt-controls` - Container for PTT UI (toggle + button + hint)
- `.ptt-toggle` - Checkbox to enable/disable PTT mode
- `.ptt-btn` - Large button labeled "🎤 Hold to Talk"
- `.ptt-hint` - "Or press and hold Spacebar" (hidden on mobile)

**JavaScript Implementation** (in `main.js`):
- `isPushToTalk` - Global state variable (default: false)
- `STORAGE_KEYS.PTT = 'duck-e-ptt'` - localStorage key for persistence
- `loadPushToTalkState()` / `savePushToTalkState()` - Persist across sessions
- `updatePTTUI(enabled)` - Show/hide PTT button and hint
- `togglePushToTalk()` - Switch between continuous and PTT modes
- `startTalking()` / `stopTalking()` - Microphone control during hold
- Keyboard listeners - Spacebar when PTT mode enabled

**PTT Behavior**:
1. When PTT is **disabled** (default): Microphone is always on when connected (unless muted)
2. When PTT is **enabled**: Microphone is off by default
3. While holding button/spacebar: Microphone temporarily enabled
4. On release: Microphone disabled again
5. State persists in localStorage across browser sessions

**Voice Change Interaction**:
- Voice changes use OpenAI's `session.update` event (in-place update, no reinit)
- PTT state (`isPushToTalk`) is preserved during voice changes
- Microphone state is correctly restored: if in PTT mode, mic stays disabled by default
- No reconnection needed — the WebRTC session persists across voice changes

## Model Configuration

Uses `gpt-realtime-2` for voice sessions. Config generated in `app/config.py`:

```python
{
    "model": "gpt-realtime-2",
    "api_key": os.getenv("OPENAI_API_KEY"),
    "voice": "alloy",  # changeable via tool
}
```

## Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Security tests (92% coverage)
pytest tests/security/
```

## Deployment

- **CI/CD**: Argo Workflows `duck-e-build` in `iad-ci` cluster → Docker image → `ronaldraygun/duck-e`, then auto-bumps the tag in every declarative-config manifest referencing it. **Check that the bump landed**: its `update-declarative-config` step hit its pod deadline on the 0.2.162 and 0.2.163 runs (2026-11-12), so both tags were bumped by hand.
- **Production cluster**: **ardenone-cluster** (`k8s/ardenone-cluster/ducke/` in `jedarden/declarative-config`), two Deployments behind a Cloudflare Tunnel: `duck-e` at `ducke.ardenone.com` (main) and `duck-e-demo` at `ducke-demo.ardenone.com` (public, cost-capped via `ducke-demo-config`). There is no apexalgo-iad or ghcr.io deployment — verified 2026-08-13.
- **Auth** (since 2026-11-12, ducke-0556aec3): `ducke.ardenone.com` is gated by Authentik forward-auth (`authentik-forward-auth` middleware, embedded outpost, `forward_single`, `platform-admins`) on **every** route including the `/session` WebSocket — the outpost cookie is set on this host, so the same-origin upgrade carries it. Only `/outpost.goauthentik.io/` is unauthenticated. Authentik side: `ducke.yaml` + the embedded outpost's providers list in `declarative-config/k8s/ardenone-cluster/authentik/authentik-blueprints-configmap.yml`. The tailnet-only `duck-e-ducke-ardenone-cluster-ts.ardenone.com:8444` route stays unauthenticated by design; the demo host is public.
- **Secrets** (no SealedSecrets since 2026-11-12, ducke-f2e5baa4): ExternalSecrets from OpenBao v2 — `ducke-openai` ← `secret/ardenone-cluster/ducke/openai` (OPENAI_API_KEY, both instances) and `ducke-valkey` ← `secret/ardenone-cluster/ducke/valkey-acl` (REDIS_URL for Valkey ACL user `ducke`, db 7, main instance only). Rotate by writing a new OpenBao version; Reloader restarts the pods.
- **Storage**: `/data/memory` is an NFS-backed PVC (`ducke-memory`, `nfs-synology-apps` StorageClass). The app can read Authentik identity headers from `TRUSTED_PROXY_SOURCES` peers, but that setting remains unset in production, so the PVC is still unused for per-user memory. See Memory System gotcha below.
- **Metrics**: Prometheus metrics at `/metrics`

## Common Gotchas

1. **Ephemeral key flow**: Never send real `OPENAI_API_KEY` to client. Only send `client_secret.value` from `/v1/realtime/sessions` response.
2. **Voice change**: Uses OpenAI's `session.update` event (in-place update, no reinit needed). The WebRTC connection persists, PTT state is preserved, and the new voice applies to subsequent responses.
3. **Memory persistence**: Requires a JWT identity or identity headers from a configured trusted reverse proxy; local development needs a JWT or mocked trusted peer and headers. The app accepts `X-authentik-email`/`X-authentik-username` from trusted peers, and Traefik overwrites these through `authResponseHeaders` on the Authentik-protected production route. `TRUSTED_PROXY_SOURCES` is still unset in the Deployment, so those headers are ignored and nothing has yet been persisted to `/data/memory` in the live deployment. The deployment follow-up must configure trusted proxy sources and strip identity headers on unauthenticated routes.
4. **AG2 references**: Code still uses `ag2.init` message type for client compatibility, but backend is custom `RealtimeSession`.
5. **Cost display resets**: On reconnect, `resetCostState()` called in `main.js` → `connectAudio()`.
6. **PTT mode persistence**: PTT state is stored in localStorage and restored on page load, but microphone state must be reapplied after connection establishes. See `toggleConnection()` in main.js lines 1124-1131 where saved PTT state determines whether mic starts enabled or disabled.
