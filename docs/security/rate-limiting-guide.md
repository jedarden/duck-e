# DUCK-E Rate Limiting and Cost Protection Guide

## Overview

This guide explains the rate limiting and cost protection features implemented in DUCK-E to ensure responsible API usage and cost control for public deployments.

## Features

### 1. Rate Limiting

**Slowapi-based rate limiting** prevents abuse and ensures fair resource allocation across users.

#### Endpoint-Specific Limits

| Endpoint | Limit | Purpose |
|----------|-------|---------|
| `/status` | 60/minute | Health check monitoring |
| `/` | 30/minute | Prevent page scraping |
| `/session` (WebSocket) | 5/minute | WebSocket connection limit |
| Weather API | 10/hour | External API quota protection |
| Web Search | 5/hour | Expensive operation protection |

#### Configuration

Set limits via environment variables:

```bash
# Enable/disable rate limiting
RATE_LIMIT_ENABLED=true

# Per-endpoint limits
RATE_LIMIT_STATUS=60/minute
RATE_LIMIT_MAIN_PAGE=30/minute
RATE_LIMIT_WEBSOCKET=5/minute
RATE_LIMIT_WEATHER_API=10/hour
RATE_LIMIT_WEB_SEARCH=5/hour

# Redis for distributed rate limiting (multi-instance)
REDIS_URL=redis://localhost:6379/0
```

### 2. Cost Protection

**Budget enforcement** prevents runaway API costs with per-session and global limits.

#### Budget Caps

- **Per-session limit**: $5.00 (configurable)
- **Session duration**: 30 minutes max
- **Hourly total**: $50.00 across all sessions
- **Circuit breaker**: Activates at $100.00 total cost

#### Token Cost Estimation

| Model | Input Cost (per 1M tokens) | Output Cost (per 1M tokens) |
|-------|---------------------------|----------------------------|
| gpt-5 | $10.00 | $30.00 |
| gpt-5-mini | $3.00 | $15.00 |
| gpt-realtime | $100.00 | $200.00 |

#### Configuration

```bash
# Enable/disable cost protection
COST_PROTECTION_ENABLED=true

# Budget limits
COST_PROTECTION_MAX_SESSION_COST_USD=5.0
COST_PROTECTION_MAX_SESSION_DURATION_MINUTES=30
COST_PROTECTION_MAX_TOTAL_COST_PER_HOUR_USD=50.0
COST_PROTECTION_CIRCUIT_BREAKER_THRESHOLD_USD=100.0

# Redis for distributed cost tracking
REDIS_URL=redis://localhost:6379/0
```

### 3. Redis-Based Distributed Tracking

For **multi-instance deployments**, Redis provides:

- **Distributed rate limiting** across all instances
- **Centralized cost tracking** for accurate budget enforcement
- **Session persistence** across restarts
- **Circuit breaker coordination**

## Deployment

### Single Instance (In-Memory)

```bash
# No Redis required
docker-compose up -d duck-e
```

**Note**: Rate limits and costs are tracked in-memory. Not suitable for multi-instance deployments.

### Multi-Instance with Redis

```bash
# Use rate-limited compose file
docker-compose -f docker-compose.rate-limited.yml up -d
```

This starts:
- **Redis** for distributed state
- **DUCK-E** application with rate limiting
- **Prometheus** for metrics (optional)
- **Grafana** for dashboards (optional)

### Kubernetes/Cloud Deployment

For production deployments:

1. **Deploy Redis cluster** (e.g., AWS ElastiCache, Redis Cloud)
2. **Set REDIS_URL** environment variable
3. **Enable cost protection** and rate limiting
4. **Configure Prometheus** scraping

Example:
```yaml
env:
  - name: REDIS_URL
    value: "redis://redis-cluster:6379/0"
  - name: RATE_LIMIT_ENABLED
    value: "true"
  - name: COST_PROTECTION_ENABLED
    value: "true"
```

## Monitoring

### Prometheus Metrics

The middleware exposes these metrics at `/metrics`:

**Rate Limiting:**
- `rate_limit_exceeded_total{endpoint, client_ip}` - Rate limit violations
- `rate_limit_check_duration_seconds{endpoint}` - Rate limit check latency

**Cost Protection:**
- `api_cost_total_usd{model, session_id}` - Total API costs
- `active_sessions_total` - Number of active sessions
- `session_duration_seconds{status}` - Session duration histogram
- `token_usage_total{model, type}` - Token usage counters
- `budget_exceeded_total{session_id}` - Budget violations

### Grafana Dashboards

Access Grafana at `http://localhost:3000` (default credentials: admin/admin).

**Pre-configured panels:**
- Rate limit violations over time
- API cost trends
- Active sessions
- Token usage breakdown
- Budget utilization

### Health Checks

**Redis health:**
```bash
curl http://localhost:8000/health/redis
```

**Application status:**
```bash
curl http://localhost:8000/status
```

## API Integration

### Cost Tracking in WebSocket

```python
from app.middleware import get_cost_tracker

tracker = get_cost_tracker()

# Start session
await tracker.start_session(session_id)

# Track usage
usage = await tracker.track_usage(
    session_id=session_id,
    model="gpt-realtime",
    input_tokens=150,
    output_tokens=300
)

if not usage["budget_ok"]:
    # Budget exceeded - terminate session
    await websocket.close(
        code=1008,
        reason="Budget limit exceeded"
    )

# End session
await tracker.end_session(session_id)
```

### Custom Rate Limits

```python
from app.middleware import limiter

@app.get("/custom-endpoint")
@limiter.limit("10/minute")
async def custom_endpoint(request: Request):
    return {"message": "Custom rate limited endpoint"}
```

## Error Handling

### Rate Limit Exceeded (429)

```json
{
  "error": "Rate limit exceeded",
  "message": "Too many requests. Please try again later.",
  "retry_after_seconds": 60,
  "endpoint": "/session",
  "limit": "5 per minute"
}
```

**Response headers:**
- `Retry-After: 60` - Seconds until retry is allowed

### Budget Exceeded (402)

```json
{
  "error": "Budget limit exceeded",
  "session_cost_usd": 5.02,
  "limit_usd": 5.00,
  "remaining_budget_usd": 0.00
}
```

### Circuit Breaker Active (503)

```json
{
  "error": "Service temporarily unavailable",
  "message": "System is under high load. Please try again later.",
  "circuit_breaker_active": true,
  "reset_time": "2025-10-10T18:30:00Z"
}
```

## Security Considerations

### IP Detection

The middleware uses `X-Forwarded-For` header when behind a proxy:

```python
def get_client_identifier(request: Request) -> str:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return get_remote_address(request)
```

**Important**: Ensure your reverse proxy (nginx, Cloudflare, etc.) properly sets `X-Forwarded-For`.

### Redis Security

For production Redis:

1. **Use authentication**: Set `requirepass` in Redis config
2. **Enable TLS**: Use `rediss://` URLs
3. **Network isolation**: Keep Redis in private network
4. **Regular backups**: Enable AOF persistence

Example secure Redis URL:
```bash
REDIS_URL=rediss://username:password@redis.example.com:6380/0
```

### Cost Protection Best Practices

1. **Set conservative limits** initially
2. **Monitor actual usage** via Prometheus
3. **Adjust limits** based on real traffic
4. **Enable circuit breaker** for production
5. **Configure alerts** for budget threshold warnings

## Testing

### Test Rate Limiting

```bash
# Exceed rate limit
for i in {1..10}; do
  curl http://localhost:8000/session
done

# Expected: 429 after 5 requests/minute
```

### Test Cost Protection

```python
# tests/test_cost_protection.py
import pytest
from app.middleware import get_cost_tracker

@pytest.mark.asyncio
async def test_session_budget_enforcement():
    tracker = get_cost_tracker()
    session_id = "test-session"

    await tracker.start_session(session_id)

    # Simulate expensive usage
    usage = await tracker.track_usage(
        session_id=session_id,
        model="gpt-realtime",
        input_tokens=10000,
        output_tokens=20000
    )

    # Should exceed $5 budget
    assert not usage["budget_ok"]
    assert usage["warnings"]

    await tracker.end_session(session_id)
```

### Run Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio

# Run tests
pytest tests/test_rate_limiting.py
pytest tests/test_cost_protection.py
```

## Performance Tuning

### Redis Optimization

```bash
# In redis.conf or docker-compose.yml
maxmemory 256mb
maxmemory-policy allkeys-lru
appendonly yes
appendfsync everysec
```

### Rate Limit Storage Strategy

The middleware uses **fixed-window-elastic-expiry** strategy:
- **Fixed windows**: Predictable reset times
- **Elastic expiry**: Keys auto-expire to free memory
- **Best for**: Public APIs with burst traffic

## Troubleshooting

### Issue: Rate limits not working

**Check:**
1. `RATE_LIMIT_ENABLED=true` in environment
2. Middleware registered in `main.py`
3. Redis connection (if using distributed mode)

### Issue: Cost tracking inaccurate

**Check:**
1. Token usage reporting in API calls
2. Model name matching (case-sensitive)
3. Redis connection for distributed tracking

### Issue: Circuit breaker stuck

**Solution:**
```bash
# Reset circuit breaker via Redis
redis-cli DEL circuit_breaker:active
```

Or restart the application to reset in-memory state.

## Support

For issues or questions:
- **GitHub Issues**: [duck-e/issues](https://github.com/your-org/duck-e/issues)
- **Documentation**: [Full API Docs](./API.md)
- **Monitoring**: Prometheus at `http://localhost:9090`
- **Metrics Dashboard**: Grafana at `http://localhost:3000`
