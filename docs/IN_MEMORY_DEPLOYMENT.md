# In-Memory Deployment Guide

## Overview

DUCK-E is configured for **single instance deployment** using **in-memory storage** for all security controls. This simplifies deployment and eliminates external dependencies like Redis.

## Architecture Decision

**Storage Strategy:** In-memory only
**Deployment Model:** Single instance (1 container, 1 process)
**Session Persistence:** Sessions lost on restart (acceptable)
**Cost Tracking:** Resets on restart (acceptable)

---

## What Happens on Server Restart?

### ✅ **Acceptable State Loss**

When the server restarts, the following state is lost (and this is **acceptable**):

1. **Rate Limit Counters**
   - All IP-based rate limit counters reset to zero
   - Users get fresh rate limits immediately
   - **Impact:** Users who hit rate limits can access again
   - **Acceptable:** Fresh start on restart is fine

2. **Cost Tracking**
   - All session budgets reset
   - Circuit breaker resets
   - Total hourly cost tracking resets
   - **Impact:** Users get fresh $5 session budgets
   - **Acceptable:** Cost protection still active for new sessions

3. **Active WebSocket Sessions**
   - All active sessions are terminated
   - Users must reconnect
   - **Impact:** Users experience brief interruption
   - **Acceptable:** WebSockets handle reconnection automatically

4. **JWT Session State** (if using authentication)
   - Token revocation list clears
   - Session bindings reset
   - **Impact:** Revoked tokens become valid again until expiration
   - **Acceptable:** Tokens expire naturally within 2-8 hours

### ❌ **What Is NOT Lost**

These remain intact after restart:

1. **Application Code** - All security controls remain active
2. **Environment Configuration** - Settings persist
3. **API Keys** - Loaded from .env file
4. **Security Middleware** - All protections active immediately
5. **Docker Image** - Container state preserved

---

## Deployment Configuration

### docker-compose.yml

```yaml
services:
  duck-e:
    build:
      context: .
      dockerfile: dockerfile
    container_name: duck-e
    ports:
      - "8000:8000"
    env_file:
      - .env
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

**No Redis required!** Just one simple container.

### .env Configuration

```bash
# Required
OPENAI_API_KEY=your_openai_api_key_here
WEATHER_API_KEY=your_weather_api_key_here

# Rate Limiting (in-memory)
RATE_LIMIT_ENABLED=true
RATE_LIMIT_STATUS=60/minute
RATE_LIMIT_MAIN_PAGE=30/minute
RATE_LIMIT_WEBSOCKET=5/minute

# Cost Protection (in-memory)
COST_PROTECTION_ENABLED=true
COST_PROTECTION_MAX_SESSION_COST_USD=5.0
COST_PROTECTION_MAX_SESSION_DURATION_MINUTES=30
COST_PROTECTION_MAX_TOTAL_COST_PER_HOUR_USD=50.0
COST_PROTECTION_CIRCUIT_BREAKER_THRESHOLD_USD=100.0

# Security
ALLOWED_ORIGINS=https://yourdomain.com
ENABLE_HSTS=true

# Monitoring
ENABLE_METRICS=true
```

---

## Deployment Steps

### 1. Quick Start (5 minutes)

```bash
# Navigate to project
cd /workspaces/duck-e/ducke

# Configure environment
cp .env.example .env
nano .env  # Add your API keys

# Build and run
docker-compose up -d

# Verify
curl http://localhost:8000/status
```

### 2. Production Deployment (2 hours)

```bash
# 1. Setup HTTPS (recommended)
sudo apt-get install nginx certbot
sudo certbot --nginx -d yourdomain.com

# 2. Build production image
docker-compose build

# 3. Deploy
docker-compose up -d

# 4. Verify security headers
curl -I https://yourdomain.com/status

# 5. Monitor logs
docker logs -f duck-e
```

---

## Performance Characteristics

### Memory Usage

| Component | Memory |
|-----------|--------|
| Base application | ~200MB |
| Rate limiting (in-memory) | ~50MB |
| Cost tracking (in-memory) | ~20MB |
| Active WebSocket sessions (100) | ~100MB |
| **Total** | **~370MB** |

**Recommended:** 512MB minimum, 2GB for headroom

### CPU Usage

| Operation | CPU |
|-----------|-----|
| Rate limit check | < 0.1ms |
| Input validation | < 0.5ms |
| Cost tracking | < 0.5ms |
| WebSocket handling | ~5% per 100 connections |

**Recommended:** 2 CPUs for production

### Disk Usage

| Component | Disk |
|-----------|------|
| Application code | ~50MB |
| Dependencies | ~200MB |
| Logs (daily) | ~10MB |
| **Total** | **~260MB** |

---

## Scaling Considerations

### When to Use In-Memory

✅ **Good for:**
- Single server deployment
- < 1,000 concurrent users
- Development and staging
- Simple deployment requirements
- No need for session persistence across restarts

### When to Add Redis

⚠️ **Consider Redis when:**
- Multiple server instances (horizontal scaling)
- > 1,000 concurrent users
- Load balancer in front
- Session persistence across restarts is critical
- Kubernetes/Docker Swarm deployment

### Migration Path

If you need to scale later:

1. **Add Redis to docker-compose:**
```yaml
services:
  duck-e:
    # existing config
    environment:
      - REDIS_URL=redis://redis:6379/0

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
```

2. **No code changes required!** The middleware automatically detects Redis and uses it.

3. **Gradual migration:** Run both in-memory and Redis, then switch

---

## Monitoring

### Health Checks

```bash
# Application health
curl http://localhost:8000/status

# Docker health
docker ps
# Should show: healthy

# Logs
docker logs duck-e --tail 100 -f
```

### Metrics (Prometheus)

Access metrics at: `http://localhost:8000/metrics`

**Key metrics to monitor:**
- `rate_limit_exceeded_total` - Rate limit violations
- `ducke_cost_total_usd` - Total API costs
- `ducke_circuit_breaker_activations_total` - Emergency stops

### Restart Impact

**What to expect after restart:**
- Service downtime: < 5 seconds
- Active WebSocket sessions: disconnected (reconnect automatically)
- Rate limits: reset (users get fresh limits)
- Costs: tracking resets (protection still active)
- Security: all controls active immediately

**Monitor for 5 minutes after restart:**
```bash
# Watch logs
docker logs -f duck-e | grep -E "(ERROR|WARN|RATE_LIMIT|COST)"

# Check metrics
curl http://localhost:8000/metrics | grep -E "(rate_limit|cost|circuit)"

# Test rate limiting
for i in {1..70}; do curl http://localhost:8000/status; sleep 1; done
# Should see 429 after 60 requests
```

---

## Backup & Recovery

### What to Backup

Since state is in-memory, you only need to backup:

1. **Configuration:**
```bash
tar -czf backup-$(date +%Y%m%d).tar.gz \
  .env \
  docker-compose.yml \
  dockerfile
```

2. **Application Code:**
```bash
git commit -am "Backup before deployment"
git push origin main
```

3. **Logs (optional):**
```bash
docker logs duck-e > logs-$(date +%Y%m%d).txt
```

### Recovery

If something goes wrong:

```bash
# Stop container
docker-compose down

# Restore configuration
tar -xzf backup-YYYYMMDD.tar.gz

# Rebuild and restart
docker-compose build
docker-compose up -d

# Verify
curl http://localhost:8000/status
```

**Total recovery time:** < 2 minutes

---

## Security Best Practices

### 1. Restart Strategy

**Recommended restart frequency:**
- Normal operation: None (let it run)
- After code changes: Immediately
- Security patches: Weekly
- Routine maintenance: Monthly

**Graceful restart procedure:**
```bash
# 1. Notify users (if possible)
# 2. Wait for active sessions to complete (~30 min)
# 3. Restart
docker-compose restart duck-e

# 4. Monitor
docker logs -f duck-e
```

### 2. Cost Protection on Restart

Since cost tracking resets on restart:

- Circuit breaker protection still active
- Fresh $5 budgets per session
- Hourly $50 limit still enforced
- **Worst case:** Users get extra session after restart (acceptable)

### 3. Rate Limiting on Restart

Since rate limits reset on restart:

- Fresh rate limit counters
- Users who hit limits can access again
- **Worst case:** Brief spike after restart (acceptable)
- Protection active within milliseconds

---

## Troubleshooting

### Issue: High Memory Usage

```bash
# Check memory
docker stats duck-e

# If > 1GB:
# 1. Check for memory leaks in logs
# 2. Restart container
docker-compose restart duck-e

# 3. Consider adding memory limits
# See docker-compose.yml deploy section
```

### Issue: Rate Limits Not Working

```bash
# 1. Check configuration
docker exec duck-e env | grep RATE_LIMIT

# 2. Check logs for initialization
docker logs duck-e | grep "rate limiting"
# Should see: "Initializing in-memory rate limiting"

# 3. Test manually
for i in {1..70}; do curl http://localhost:8000/status; done
# Should get 429 after 60 requests
```

### Issue: Cost Protection Not Working

```bash
# 1. Check configuration
docker exec duck-e env | grep COST_PROTECTION

# 2. Check logs for tracking
docker logs duck-e | grep "cost tracking"

# 3. Check metrics
curl http://localhost:8000/metrics | grep ducke_cost
```

---

## FAQ

**Q: What happens to active users when server restarts?**
A: WebSocket connections drop and users need to reconnect (automatically handled by client). Session state is lost but users get fresh budgets.

**Q: Will I lose rate limit protection on restart?**
A: No. Rate limits are active within milliseconds of restart. Counters reset but protection is immediate.

**Q: Can someone abuse the system by forcing restarts?**
A: No. They don't have access to restart the container. Even if they did, rate limits activate immediately.

**Q: How long does a restart take?**
A: < 5 seconds downtime. Application is available almost immediately.

**Q: Should I restart regularly?**
A: No need for routine restarts. Only restart for code updates or security patches.

**Q: What if I need session persistence later?**
A: Add Redis to docker-compose.yml and set REDIS_URL. No code changes needed.

**Q: Is in-memory storage production-ready?**
A: Yes, for single instance deployments < 1,000 concurrent users. Many production services use this approach.

---

## Production Checklist

Before deploying to production:

- [ ] Environment variables configured (.env)
- [ ] HTTPS/TLS enabled with valid certificate
- [ ] Security headers verified
- [ ] Rate limiting tested
- [ ] Cost protection tested
- [ ] Monitoring configured (Prometheus/Grafana)
- [ ] Backup procedure documented
- [ ] Restart procedure tested
- [ ] Health checks passing
- [ ] All tests passing (`pytest tests/ -v`)

**Once complete, you're ready for production!** 🚀

---

*In-memory deployment optimized for simplicity and single instance use case*
*Session state loss on restart is acceptable and expected*
