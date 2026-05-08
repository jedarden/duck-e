# DUCK-E - Coding Environment

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
# WEATHER_API_KEY=...

# Run dev server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t duck-e:latest .
docker run -p 8000:8000 -e OPENAI_API_KEY=... -e WEATHER_API_KEY=... duck-e:latest
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

- User identity from headers: `x-forwarded-user`, `x-forwarded-email`
- JSON file store: `/data/memory/{user_hash}.json`
- Tools: `save_memory(fact)`, `recall_memories()`
- Memories injected into system message at session start

### Security

- **SSRF protection**: `FetchUrl` validator resolves hostnames, blocks private IPs
- **Rate limiting**: Per-IP limits via `slowapi`
- **Input validation**: `LocationInput`, `SearchQuery`, `FetchUrl` validators
- **CORS**: Origin whitelist in `app.middleware.configure_cors()`
- **Cost protection**: $5/session hard limit, $100 circuit breaker

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

- **CI/CD**: ArgoCD WorkflowTemplate `duck-e-build` in iad-ci cluster → Docker image → `ghcr.io/jedarden/duck-e`
- **Production cluster**: apexalgo-iad (accessed via kubectl-proxy over Tailscale)
- **Auth**: Behind oauth2-proxy or Tailscale auth for header injection
- **Metrics**: Prometheus metrics at `/metrics`

## Common Gotchas

1. **Ephemeral key flow**: Never send real `OPENAI_API_KEY` to client. Only send `client_secret.value` from `/v1/realtime/sessions` response.
2. **Voice change**: Requires session reinit — `change_voice` tool sends new config, client reconnects WebRTC.
3. **Memory persistence**: Requires user headers from reverse proxy — won't work in local dev without mocking headers.
4. **AG2 references**: Code still uses `ag2.init` message type for client compatibility, but backend is custom `RealtimeSession`.
5. **Cost display resets**: On reconnect, `resetCostState()` called in `main.js` → `connectAudio()`.
