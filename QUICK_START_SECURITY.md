# DUCK-E Security Quick Start Guide

**🚨 DO NOT DEPLOY PUBLICLY WITHOUT COMPLETING THESE STEPS 🚨**

This guide gets your DUCK-E application from current state to production-ready in the fastest way possible.

---

## ⏱️ Time Estimate: 8 Hours

This is the **absolute minimum** required before public deployment. Skipping any step puts you at severe risk of:
- **$10,000-$100,000** in API abuse costs
- Legal liability (no ToS/Privacy Policy)
- Data breaches and security incidents

---

## 🎯 Quick Start Checklist

### Prerequisites (5 minutes)

```bash
# Navigate to project
cd /workspaces/duck-e/ducke

# Verify dependencies installed
pip install -r requirements.txt

# Copy environment template
cp .env.example .env

# Edit .env with your API keys
nano .env
```

**Required in .env:**
- `OPENAI_API_KEY` - Your OpenAI API key
- `WEATHER_API_KEY` - Your WeatherAPI key
- `ALLOWED_ORIGINS` - Your domain (e.g., https://yourdomain.com)

---

## Step 1: Enable Rate Limiting (2 hours)

### 1.1 Update main.py (30 min)

Add these imports at the top of `app/main.py`:

```python
# Add after line 17
from app.middleware.rate_limiting import setup_rate_limiting
from app.middleware.cost_protection import CostProtectionMiddleware, get_cost_tracker
```

Add after creating the FastAPI app (after line 87):

```python
# After: app = FastAPI()

# Setup rate limiting
limiter = setup_rate_limiting(app)

# Add cost protection middleware
app.add_middleware(
    CostProtectionMiddleware,
    cost_tracker=get_cost_tracker()
)
```

Apply rate limits to endpoints:

```python
# Update status endpoint (line 89-91)
@app.get("/status", response_class=JSONResponse)
@limiter.limit("60/minute")  # Add this line
async def index_page(request: Request):  # Add request parameter
    return {"message": "WebRTC DUCK-E Server is running!"}

# Update main page endpoint (line 105-109)
@app.get("/", response_class=HTMLResponse)
@limiter.limit("30/minute")  # Add this line
async def start_chat(request: Request):
    """Endpoint to return the HTML page for audio chat."""
    port = request.url.port
    return templates.TemplateResponse("chat.html", {"request": request, "port": port})
```

### 1.2 Deploy Redis (30 min)

```bash
# Use the rate-limited docker compose
docker-compose -f docker-compose.rate-limited.yml up -d redis

# Verify Redis is running
docker logs ducke-redis

# Update .env
echo "REDIS_URL=redis://redis:6379/0" >> .env
```

### 1.3 Test Rate Limiting (30 min)

```bash
# Run tests
pytest tests/test_rate_limiting.py -v

# Test manually
curl http://localhost:8000/status
# Repeat 70 times - should get 429 error

# Check metrics
curl http://localhost:8000/metrics | grep rate_limit
```

### 1.4 Configure Monitoring (30 min)

```bash
# Start Prometheus and Grafana
docker-compose -f docker-compose.rate-limited.yml up -d prometheus grafana

# Access Grafana
# URL: http://localhost:3000
# Login: admin/admin

# Import dashboard
# Use the JSON from docs/security/rate-limiting-guide.md
```

---

## Step 2: Add Input Validation (1 hour)

### 2.1 Create Validation Models (30 min)

Create `app/models/validators.py`:

```python
from pydantic import BaseModel, Field, validator
import re

class LocationInput(BaseModel):
    location: str = Field(..., min_length=2, max_length=100)

    @validator('location')
    def validate_location(cls, v):
        # Only allow letters, spaces, commas, hyphens
        if not re.match(r'^[a-zA-Z\s,\-]+$', v):
            raise ValueError('Invalid location format')
        return v.strip()

class SearchQuery(BaseModel):
    query: str = Field(..., min_length=3, max_length=500)

    @validator('query')
    def validate_query(cls, v):
        # Remove potential injection attempts
        dangerous_patterns = ['<script', 'javascript:', 'onerror=']
        for pattern in dangerous_patterns:
            if pattern.lower() in v.lower():
                raise ValueError('Invalid search query')
        return v.strip()
```

### 2.2 Update Weather Functions (30 min)

In `app/main.py`, update the weather functions (lines 190-203):

```python
from app.models.validators import LocationInput

@realtime_agent.register_realtime_function(
    name="get_current_weather",
    description="Get the current weather in a given city."
)
def get_current_weather(location: Annotated[str, "city"]) -> str:
    # Validate input
    try:
        validated = LocationInput(location=location)
        safe_location = validated.location
    except Exception as e:
        logger.warning(f"Invalid location input: {location}")
        return "Invalid location format. Please provide a valid city name."

    # Use params instead of f-string for URL
    url = "https://api.weatherapi.com/v1/current.json"
    params = {
        'key': os.getenv('WEATHER_API_KEY'),
        'q': safe_location,
        'aqi': 'no'
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        logger.info(f"<-- Calling get_current_weather function for {safe_location} -->")
        return response.text
    except Exception as e:
        logger.error(f"Weather API error: {e}")
        return "Unable to fetch weather data at this time."
```

Do the same for `get_weather_forecast` function.

---

## Step 3: Enable HTTPS/TLS (2 hours)

### 3.1 Deploy Nginx Reverse Proxy (60 min)

```bash
# Copy hardened nginx config
cp docs/security/nginx.conf.hardened /etc/nginx/sites-available/ducke

# Get SSL certificate (Let's Encrypt)
sudo apt-get install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com

# Update nginx config with your domain
sudo nano /etc/nginx/sites-available/ducke
# Change: server_name yourdomain.com;

# Enable site
sudo ln -s /etc/nginx/sites-available/ducke /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 3.2 Update Environment (15 min)

Update `.env`:

```bash
# Add these lines
ENABLE_HSTS=true
ALLOWED_ORIGINS=https://yourdomain.com
```

### 3.3 Test HTTPS (15 min)

```bash
# Test SSL configuration
curl -I https://yourdomain.com

# Verify security headers
curl -I https://yourdomain.com | grep -i "strict-transport"

# Test WebSocket over WSS
# Update your frontend to use wss:// instead of ws://
```

### 3.4 Update Docker Compose (30 min)

Use the hardened docker-compose:

```bash
# Stop current containers
docker-compose down

# Use hardened configuration
cp docker-compose.rate-limited.yml docker-compose.yml

# Add nginx to docker-compose.yml
# See docs/security/docker-compose.hardened.yml for example

# Restart with new config
docker-compose up -d
```

---

## Step 4: Harden Container (1 hour)

### 4.1 Update Dockerfile (30 min)

Replace `dockerfile` with the hardened version:

```bash
cp docs/security/dockerfile.hardened dockerfile
```

Key improvements:
- Multi-stage build
- Non-root user
- Read-only root filesystem
- Minimal attack surface

### 4.2 Rebuild and Deploy (30 min)

```bash
# Rebuild image
docker-compose build

# Test locally
docker-compose up -d

# Verify non-root user
docker exec ducke-app whoami
# Should output: duckeuser (not root)

# Check security settings
docker inspect ducke-app | grep -A 10 SecurityOpt
```

---

## Step 5: Legal Compliance (1 hour)

### 5.1 Add Terms of Service (30 min)

Create `app/website_files/templates/tos.html`:

Use the template from `docs/security/public-facing-security.md` Section 6.1

Add route in `app/main.py`:

```python
@app.get("/terms", response_class=HTMLResponse)
async def terms_of_service(request: Request):
    return templates.TemplateResponse("tos.html", {"request": request})
```

### 5.2 Add Privacy Policy (30 min)

Create `app/website_files/templates/privacy.html`:

Use the template from `docs/security/public-facing-security.md` Section 6.2

Add route in `app/main.py`:

```python
@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})
```

Update main chat page to link to these pages.

---

## Step 6: Final Testing & Verification (1 hour)

### 6.1 Run All Tests (20 min)

```bash
# Unit tests
pytest tests/ -v

# Security verification
bash docs/security/verify-security.sh

# Load testing
# Install locust: pip install locust
# Run: locust -f tests/load_test.py
```

### 6.2 Manual Security Checks (20 min)

```bash
# 1. Verify rate limiting
for i in {1..70}; do curl http://localhost:8000/status; done
# Should see 429 errors after 60 requests

# 2. Verify security headers
curl -I https://yourdomain.com | grep -E "(Strict-Transport|X-Content-Type|X-Frame)"

# 3. Verify CORS
curl -H "Origin: https://malicious.com" https://yourdomain.com
# Should be blocked

# 4. Verify cost protection
# Open chat, have long conversation
# Check metrics: curl http://localhost:8000/metrics | grep cost

# 5. Test circuit breaker
# Trigger high costs (multiple concurrent sessions)
# Verify automatic shutdown in logs
```

### 6.3 Monitoring Setup (20 min)

```bash
# Configure cost alerts in Grafana
# 1. Open http://localhost:3000
# 2. Create alert for: ducke_cost_total_usd > 40
# 3. Configure notification channel (email/Slack)

# Set up log monitoring
docker logs -f ducke-app | grep -E "(ERROR|CIRCUIT_BREAKER|RATE_LIMIT)"

# Create monitoring checklist
# - [ ] Daily cost review
# - [ ] Weekly security log audit
# - [ ] Monthly dependency updates
# - [ ] Quarterly penetration testing
```

---

## 🎉 Deployment Checklist

Before going live, verify ALL of these:

### Security Controls
- [ ] Rate limiting active on all endpoints
- [ ] Cost protection circuit breaker tested
- [ ] Input validation on all user inputs
- [ ] HTTPS/TLS enabled (no HTTP)
- [ ] Security headers present (run verify script)
- [ ] CORS configured for your domain only
- [ ] Container running as non-root
- [ ] Redis deployed and connected

### Infrastructure
- [ ] Using hardened Dockerfile
- [ ] Using hardened docker-compose
- [ ] Nginx reverse proxy with TLS
- [ ] Resource limits configured
- [ ] Health checks enabled
- [ ] Automatic restarts enabled

### Monitoring
- [ ] Prometheus collecting metrics
- [ ] Grafana dashboards configured
- [ ] Cost alerts configured ($40/hour threshold)
- [ ] Rate limit violation alerts
- [ ] Error log monitoring
- [ ] Uptime monitoring

### Legal & Compliance
- [ ] Terms of Service published
- [ ] Privacy Policy published
- [ ] Rate limits disclosed
- [ ] Contact information available
- [ ] GDPR compliance reviewed (if EU users)

### Testing
- [ ] All unit tests passing
- [ ] Security verification script passes
- [ ] Load testing completed
- [ ] Circuit breaker tested
- [ ] Emergency shutdown tested
- [ ] Backup/restore tested

### Documentation
- [ ] All security reports reviewed
- [ ] Team trained on monitoring
- [ ] Incident response plan documented
- [ ] Escalation contacts defined
- [ ] On-call schedule established

---

## 🚀 Go-Live Procedure

### Pre-Launch (1 hour before)

```bash
# 1. Final backup
docker-compose down
tar -czf ducke-backup-$(date +%Y%m%d).tar.gz /workspaces/duck-e/ducke

# 2. Deploy production configuration
docker-compose -f docker-compose.rate-limited.yml up -d

# 3. Warm up services
curl https://yourdomain.com/status
# Verify: {"message": "WebRTC DUCK-E Server is running!"}

# 4. Start monitoring
# Open Grafana dashboard
# Start log tail: docker logs -f ducke-app
```

### Launch

```bash
# 1. Enable public DNS
# Point yourdomain.com to your server IP

# 2. Monitor closely for first hour
# Watch metrics dashboard
# Monitor cost metrics every 5 minutes
# Check for unusual patterns

# 3. Test from external network
# Open https://yourdomain.com
# Test voice interaction
# Verify rate limits apply
```

### Post-Launch (First 24 Hours)

```bash
# Hourly checks:
# - Cost metrics (should be < $5/hour)
# - Rate limit violations (expected initially)
# - Error rates (should be < 1%)
# - Response times (should be < 2s)

# Daily reports:
curl http://localhost:8000/metrics > daily-metrics-$(date +%Y%m%d).txt
docker logs ducke-app > daily-logs-$(date +%Y%m%d).txt
```

---

## 📞 Emergency Contacts

### If Costs Spike

```bash
# IMMEDIATE SHUTDOWN
docker-compose down

# ANALYZE
docker logs ducke-app | grep COST | tail -100

# Review Grafana cost dashboard
# Identify abuse source (IP addresses)

# ADD TO BLOCKLIST
# Update nginx config or firewall rules

# RESTART with stricter limits
# Update .env: COST_PROTECTION_MAX_TOTAL_COST_PER_HOUR_USD=20.0
docker-compose up -d
```

### If Security Incident

1. **Isolate** - `docker-compose down`
2. **Preserve** - `docker logs ducke-app > incident-$(date +%Y%m%d-%H%M%S).log`
3. **Analyze** - Review logs for attack vectors
4. **Patch** - Deploy security updates
5. **Resume** - Restart with enhanced monitoring

---

## 📊 Expected Costs (Post-Implementation)

### Normal Operations
- **Small traffic** (100 users/day): **$5-10/day**
- **Medium traffic** (1,000 users/day): **$20-50/day**
- **Large traffic** (10,000 users/day): **$100-200/day**

All within budget caps and rate limits.

### Emergency Scenarios
- **Bot attack**: Stops at $100 (circuit breaker)
- **Viral spike**: Capped at $50/hour by rate limits
- **Malicious user**: $5 max per session, then blocked

---

## ✅ Success Metrics

After 24 hours, you should see:

- **Zero circuit breaker activations** (unless attacked)
- **Rate limit violations < 5%** of requests
- **Cost per user < $0.50**
- **No security warnings in logs**
- **99.9%+ uptime**
- **Response time < 2 seconds**

If any metric is off, review the security guides and adjust configurations.

---

## 🎓 Training Resources

For your team:
1. Read `docs/security/SECURITY_OVERVIEW.md` (this overview)
2. Review `docs/security/rate-limiting-guide.md` (operations)
3. Practice emergency procedures (shutdown, restore)
4. Set up monitoring dashboards
5. Schedule weekly security reviews

---

## 📚 Additional Resources

- **Complete Security Analysis**: `/docs/security/SECURITY_OVERVIEW.md`
- **API Security**: `/docs/security/api-security-report.md`
- **Authentication**: `/docs/security/auth-hardening-report.md`
- **Infrastructure**: `/docs/security/infrastructure-security-report.md`
- **Public Facing**: `/docs/security/public-facing-security.md`

---

**🎯 You're now ready for production deployment!**

Remember:
- Monitor daily for the first week
- Review costs weekly
- Update dependencies monthly
- Re-audit security quarterly

Good luck! 🚀
