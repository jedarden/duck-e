# Rate Limiting and Cost Protection Implementation

## Overview

Production-ready rate limiting and cost protection has been implemented for DUCK-E. This protects against abuse, controls API costs, and ensures fair resource allocation for public deployments.

## 📁 Files Created

### Middleware Components
- **`/workspaces/duck-e/ducke/app/middleware/rate_limiting.py`** - Slowapi rate limiting with Redis support
- **`/workspaces/duck-e/ducke/app/middleware/cost_protection.py`** - Budget enforcement and session tracking
- **`/workspaces/duck-e/ducke/app/middleware/__init__.py`** - Middleware exports (updated)

### Configuration
- **`/workspaces/duck-e/ducke/requirements.txt`** - Updated with dependencies (slowapi, redis, prometheus-client, pydantic)
- **`/workspaces/duck-e/ducke/.env.example`** - Environment variable template
- **`/workspaces/duck-e/ducke/docker-compose.rate-limited.yml`** - Docker Compose with Redis, Prometheus, Grafana

### Documentation
- **`/workspaces/duck-e/ducke/docs/security/rate-limiting-guide.md`** - Complete configuration and deployment guide
- **`/workspaces/duck-e/ducke/prometheus.yml`** - Prometheus scraping configuration

### Tests
- **`/workspaces/duck-e/ducke/tests/test_rate_limiting.py`** - Unit tests for rate limiting
- **`/workspaces/duck-e/ducke/tests/test_cost_protection.py`** - Unit tests for cost protection
- **`/workspaces/duck-e/ducke/tests/conftest.py`** - Pytest configuration

### Integration Example
- **`/workspaces/duck-e/ducke/app/main_with_rate_limiting.py`** - Integrated main.py with all middleware

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd /workspaces/duck-e/ducke
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
# Copy example configuration
cp .env.example .env

# Edit .env with your settings
nano .env
```

### 3. Single Instance Deployment (In-Memory)

```bash
# No Redis required
docker-compose up -d duck-e
```

### 4. Multi-Instance Deployment (Redis-Backed)

```bash
# With Redis, Prometheus, and Grafana
docker-compose -f docker-compose.rate-limited.yml up -d
```

## 📊 Rate Limits

| Endpoint | Default Limit | Purpose |
|----------|---------------|---------|
| `/status` | 60/minute | Health check monitoring |
| `/` | 30/minute | Prevent page scraping |
| `/session` (WebSocket) | 5/minute | WebSocket connection limit |
| Weather API | 10/hour | External API quota protection |
| Web Search | 5/hour | Expensive operation protection |

## 💰 Cost Protection

### Budget Caps
- **Per-session**: $5.00 max (configurable)
- **Session duration**: 30 minutes max
- **Hourly total**: $50.00 across all sessions
- **Circuit breaker**: $100.00 system-wide threshold

### Token Pricing (per 1M tokens)

| Model | Input | Output |
|-------|-------|--------|
| gpt-5 | $10.00 | $30.00 |
| gpt-5-mini | $3.00 | $15.00 |
| gpt-realtime | $100.00 | $200.00 |

## 📈 Monitoring

### Prometheus Metrics (at `/metrics`)

**Rate Limiting:**
- `rate_limit_exceeded_total{endpoint, client_ip}`
- `rate_limit_check_duration_seconds{endpoint}`

**Cost Protection:**
- `api_cost_total_usd{model, session_id}`
- `active_sessions_total`
- `session_duration_seconds{status}`
- `token_usage_total{model, type}`
- `budget_exceeded_total{session_id}`

### Grafana Dashboards

Access at `http://localhost:3000` (admin/admin):
- Rate limit violations over time
- API cost trends
- Active sessions
- Token usage breakdown
- Budget utilization

## 🔧 Integration with main.py

To integrate the middleware into your existing `main.py`, add:

```python
# Import middleware
from app.middleware import (
    limiter,
    RateLimitMiddleware,
    CostProtectionMiddleware,
    custom_rate_limit_exceeded_handler,
    get_cost_tracker,
    get_rate_limit_for_endpoint
)
from slowapi.errors import RateLimitExceeded
from prometheus_client import make_asgi_app
import uuid

# Initialize cost tracker
cost_tracker = get_cost_tracker()

# Add middleware (order matters)
app.add_middleware(CostProtectionMiddleware)
app.add_middleware(RateLimitMiddleware)

# Register exception handler
app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)

# Mount metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
app.state.limiter = limiter

# Apply rate limits to endpoints
@app.get("/status")
@limiter.limit(get_rate_limit_for_endpoint("/status"))
async def index_page(request: Request):
    return {"message": "WebRTC DUCK-E Server is running!"}

# In WebSocket handler
@app.websocket("/session")
@limiter.limit(get_rate_limit_for_endpoint("/session"))
async def handle_media_stream(websocket: WebSocket, request: Request):
    session_id = str(uuid.uuid4())
    await cost_tracker.start_session(session_id)
    
    try:
        # Your WebSocket logic
        await realtime_agent.run()
    finally:
        await cost_tracker.end_session(session_id)
```

See **`app/main_with_rate_limiting.py`** for complete example.

## ✅ Testing

### Run Unit Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run all tests
pytest tests/ -v

# Run specific test files
pytest tests/test_rate_limiting.py -v
pytest tests/test_cost_protection.py -v
```

### Test Rate Limiting Manually

```bash
# Exceed rate limit
for i in {1..10}; do curl http://localhost:8000/session; done

# Expected: 429 after 5 requests/minute
```

### Test Cost Tracking

```python
from app.middleware import get_cost_tracker

tracker = get_cost_tracker()
await tracker.start_session("test-session")

usage = await tracker.track_usage(
    session_id="test-session",
    model="gpt-realtime",
    input_tokens=10000,
    output_tokens=20000
)

print(f"Cost: ${usage['session_cost']:.2f}")
print(f"Budget OK: {usage['budget_ok']}")
```

## 🛡️ Security Best Practices

1. **Redis Security** (for production):
   - Use authentication: `rediss://username:password@host:port/0`
   - Enable TLS encryption
   - Keep Redis in private network
   - Regular backups (AOF enabled in docker-compose)

2. **IP Detection**:
   - Middleware uses `X-Forwarded-For` when behind proxy
   - Ensure proxy properly sets this header

3. **Budget Monitoring**:
   - Set conservative limits initially
   - Monitor actual usage via Prometheus
   - Configure alerts for budget thresholds
   - Enable circuit breaker for production

## 📖 Full Documentation

See **`docs/security/rate-limiting-guide.md`** for:
- Detailed configuration options
- API integration examples
- Error handling
- Performance tuning
- Troubleshooting

## 🐛 Troubleshooting

### Rate limits not working?
1. Check `RATE_LIMIT_ENABLED=true` in `.env`
2. Verify middleware registered in `main.py`
3. Test Redis connection (if using distributed mode)

### Cost tracking inaccurate?
1. Ensure token usage reported in API calls
2. Verify model names match (case-sensitive)
3. Check Redis connection for distributed tracking

### Circuit breaker stuck?
```bash
# Reset via Redis
redis-cli DEL circuit_breaker:active

# Or restart application
docker-compose restart duck-e
```

## 📞 Support

- **Documentation**: See `/docs/security/rate-limiting-guide.md`
- **Tests**: Run `pytest tests/ -v`
- **Monitoring**: Prometheus at `http://localhost:9090`
- **Dashboards**: Grafana at `http://localhost:3000`
