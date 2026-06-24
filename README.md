# DUCK-E: The Duck That Talks Back

**DUCK-E** (Digitally Unified Conversational Knowledge Engine) is an AI-powered voice assistant inspired by rubber duck debugging. Instead of explaining your problem to a silent rubber duck, DUCK-E listens and talks back — asking questions, offering suggestions, and searching the web in real time.

## How it works

```
Browser (WebRTC) → WebSocket → FastAPI → OpenAI Realtime API
                                              (gpt-4o-realtime-preview)
```

1. Your browser captures audio via the MediaDevices API and opens a WebRTC peer connection.
2. The FastAPI backend requests an ephemeral key from OpenAI — your real API key never touches the browser.
3. Speech is transcribed by OpenAI Whisper-1; the Realtime API handles both understanding and voice response natively.
4. When DUCK-E needs external data (weather, web search), it calls the appropriate tool on the server and folds the result into its reply.

## Features

### Voice I/O
- Low-latency, full-duplex voice conversation via the OpenAI Realtime API
- 11 built-in voices — changeable mid-session without reconnecting
- Interruption-friendly: DUCK-E handles natural conversation flow

### Tools
| Tool | What it does |
|------|-------------|
| `get_current_weather` | Current conditions via Open-Meteo (free, no key required) |
| `get_weather_forecast` | Multi-day forecast via Open-Meteo |
| `web_search` | Live web search via OpenAI's `gpt-4o-mini` + web_search_preview |
| `web_fetch` | Fetches and parses a URL (SSRF-protected; blocks private IPs) |
| `save_memory` / `recall_memories` | Stores and retrieves facts about the user |
| `change_voice` | Switches voice mid-session |

### Persistent memory
When deployed behind a reverse proxy that injects `x-forwarded-user` / `x-forwarded-email` headers (e.g. oauth2-proxy), DUCK-E stores per-user facts with categories, confidence scores, and time decay, and surfaces them at the start of each session. Memory is silently disabled in local dev when headers are absent.

### Cost protection
- Per-session spend cap (default: $5)
- Hourly spend cap (default: $50)
- Circuit-breaker threshold (default: $100 — disables new sessions for 30 minutes)
- Maximum session duration (default: 30 minutes)
- Client-side live cost display derived from token counts in `response.done` events

### Security
- Ephemeral key flow — real `OPENAI_API_KEY` never sent to the browser
- Per-IP rate limiting via slowapi
- SSRF protection on `web_fetch` (resolves hostnames, rejects private/loopback ranges)
- Input validation via Pydantic models (`LocationInput`, `SearchQuery`, `FetchUrl`)
- CORS origin whitelist
- Security headers middleware (HSTS, CSP, X-Frame-Options, etc.)

## Quick start

### Docker (fastest)

```bash
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-... \
  ghcr.io/jedarden/duck-e:latest
```

Open `http://localhost:8000` and start talking.

### Docker Compose

Create a `.env` file:

```
OPENAI_API_KEY=sk-...
```

Then run:

```bash
docker-compose up -d
```

### Local development

```bash
pip install -r requirements.txt
# create .env with OPENAI_API_KEY=sk-...
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | Must have Realtime API access |
| `REALTIME_MODEL` | No | `gpt-4o-realtime-preview` | Override the realtime model |
| `RATE_LIMIT_ENABLED` | No | `true` | Toggle per-IP rate limiting |
| `RATE_LIMIT_WEBSOCKET` | No | `5/minute` | Per-IP WebSocket connection rate |
| `COST_PROTECTION_ENABLED` | No | `true` | Toggle cost protection |
| `COST_PROTECTION_MAX_SESSION_COST_USD` | No | `5.0` | Per-session spend cap |
| `COST_PROTECTION_MAX_TOTAL_COST_PER_HOUR_USD` | No | `50.0` | Hourly spend cap |
| `COST_PROTECTION_CIRCUIT_BREAKER_THRESHOLD_USD` | No | `100.0` | Kill-switch threshold |
| `COST_PROTECTION_MAX_SESSION_DURATION_MINUTES` | No | `30` | Maximum session length |
| `ALLOWED_ORIGINS` | No | — | CORS origin whitelist (comma-separated) |
| `GRAFANA_PASSWORD` | No | — | For the hardened docker-compose stack |

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Main chat UI |
| `WS` | `/session` | Real-time audio WebSocket |
| `GET` | `/status` | Health check + version |
| `GET` | `/health/openai` | Tests ephemeral key creation |
| `GET` | `/metrics` | Prometheus metrics |

## Voices

DUCK-E ships with 11 voices from the OpenAI Realtime API. Switch any time via the voice selector in the UI or by asking DUCK-E to change its voice mid-conversation:

`alloy` · `ash` · `ballad` · `coral` · `echo` · `fable` · `nova` · `onyx` · `sage` · `shimmer` · `verse`

## License

MIT — see [LICENSE](LICENSE).
