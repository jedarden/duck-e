# DUCK-E API Authentication Guide

## Overview

DUCK-E implements **optional JWT-based authentication** with tiered access levels. Anonymous users can access the API with free-tier limits, while authenticated users receive higher rate limits and session budgets based on their subscription tier.

## Authentication Design Principles

1. **Optional Authentication**: API works without authentication (free tier)
2. **Anonymous Access**: Requests without a credential use free-tier access; supplied invalid credentials are rejected
3. **No Breaking Changes**: Existing anonymous users continue working
4. **Tier-Based Access**: Premium/Enterprise tiers unlock higher limits

## Access Tiers

### Free Tier (Anonymous/Default)

**No authentication required**

```http
GET /status
# No Authorization header needed
```

**Limits:**
- **Rate Limit**: 5 WebSocket connections per minute
- **Session Budget**: $5.00
- **Session Timeout**: 30 minutes
- **Concurrent Connections**: 1

### Premium Tier (JWT Required)

**Requires valid JWT token**

```http
GET /auth/me
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Limits:**
- **Rate Limit**: 20 WebSocket connections per minute
- **Session Budget**: $20.00
- **Session Timeout**: 2 hours
- **Concurrent Connections**: 5

### Enterprise Tier (JWT with Custom Claim)

**Requires JWT with `tier: "enterprise"` claim**

```http
GET /auth/me
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Limits:**
- **Rate Limit**: 100 WebSocket connections per minute
- **Session Budget**: $100.00
- **Session Timeout**: 8 hours
- **Concurrent Connections**: 20

## JWT Token Contract

An access JWT must be signed with the configured algorithm and contain a
non-empty `sub` and a numeric `exp` (Unix NumericDate). The token is invalid
once `exp` is reached; there is no expiry grace period. If present,
`token_type` must be `access` (refresh tokens are never API credentials).

`tier` must be exactly `free`, `premium`, or `enterprise`. A signed legacy
token with no `tier` claim is treated as `premium`; an explicit unknown or
malformed tier is rejected with 401. The signature issuer is responsible for
assigning tiers correctly.

### Access Token (2 hours)

```json
{
  "sub": "user_12345",
  "tier": "premium",
  "exp": 1699999999,
  "iat": 1699996399,
  "jti": "unique-token-id-abc123",
  "token_type": "access"
}
```

### Refresh Token (7 days)

```json
{
  "sub": "user_12345",
  "tier": "premium",
  "exp": 1700604399,
  "iat": 1699996399,
  "jti": "unique-refresh-id-xyz789",
  "token_type": "refresh"
}
```

## API Endpoints

### 1. Token Generation (External)

> **Note**: Token generation happens outside DUCK-E API (e.g., your auth service)

Example token creation with Python:

```python
from datetime import datetime, timedelta
from jose import jwt

# Token payload
payload = {
    "sub": "user_12345",
    "tier": "premium",  # "free", "premium", or "enterprise"
    "exp": datetime.utcnow() + timedelta(hours=2),
    "iat": datetime.utcnow()
}

# Sign token
secret_key = "your-jwt-secret-key"
token = jwt.encode(payload, secret_key, algorithm="HS256")
```

### 2. Using Tokens with DUCK-E

#### HTTP Requests

```bash
# Anonymous (free tier)
curl https://api.ducke.io/status

# Validate an authenticated access token and read its tier
curl https://api.ducke.io/auth/me \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

#### WebSocket Connections

The browser WebSocket API cannot set an `Authorization` header. Browser clients
pass the access token as the `token` query parameter during the handshake.
Because query strings can be captured by proxy/access logs, use `wss://`, keep
access tokens short-lived, and configure every proxy and access log to redact
the `token` query value. DUCK-E application logs record auth header names only
and do not log the token query value. Do not send a JWT in a WebSocket message
after connecting.

```javascript
// JavaScript/Browser
const url = new URL('wss://api.ducke.io/session');
url.searchParams.set('token', accessToken);
const ws = new WebSocket(url);
```

Non-browser WebSocket clients may instead send the standard
`Authorization: Bearer <access JWT>` handshake header. A connection must use
one transport only; sending both is rejected. A missing credential is an
anonymous free-tier session. A supplied invalid or expired JWT closes the
WebSocket with policy-violation code `1008` before a session starts.

### 3. Token Refresh

Refresh tokens allow getting new access tokens without re-authentication.

**Endpoint**: `POST /auth/refresh` (implement in your auth service)

```bash
curl -X POST https://your-auth-service.com/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "YOUR_REFRESH_TOKEN"}'
```

**Response**:

```json
{
  "access_token": "new_access_token_here",
  "token_type": "bearer",
  "expires_in": 7200
}
```

## Error Handling

### 401 Unauthorized

**Invalid or expired token** - Rejected. Anonymous free-tier access applies only when no credential is supplied.

```json
{
  "detail": "Token has expired"
}
```

The HTTP response also includes `WWW-Authenticate: Bearer`. Public endpoints
without a token remain available anonymously. If a token is supplied to an
optional-auth endpoint, malformed, expired, revoked, non-access, or
unsupported-tier tokens return 401 instead of silently downgrading.

### 429 Rate Limit Exceeded

**Too many requests for current tier**

```json
{
  "error": "Rate limit exceeded",
  "message": "Too many requests. Please try again later.",
  "retry_after_seconds": 60,
  "endpoint": "/session",
  "limit": "5/minute",
  "current_tier": "free"
}
```

**Headers**:
- `Retry-After`: Seconds until rate limit resets
- `X-RateLimit-Limit`: Total requests allowed
- `X-RateLimit-Remaining`: Requests remaining
- `X-RateLimit-Reset`: Unix timestamp for reset

## Implementation Examples

### Python Client

```python
import requests
from datetime import datetime, timedelta
from jose import jwt

class DuckEClient:
    def __init__(self, token: str = None):
        self.token = token
        self.base_url = "https://api.ducke.io"

    def get_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def check_status(self):
        response = requests.get(
            f"{self.base_url}/status",
            headers=self.get_headers()
        )
        return response.json()

# Anonymous usage (free tier)
client = DuckEClient()
print(client.check_status())  # 5 conn/min limit

# Authenticated usage (premium tier)
premium_client = DuckEClient(token="your_jwt_token")
print(premium_client.check_status())  # 20 conn/min limit
```

### JavaScript Client

```javascript
class DuckEClient {
  constructor(token = null) {
    this.token = token;
    this.baseUrl = 'https://api.ducke.io';
  }

  getHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    if (this.token) {
      headers['Authorization'] = `Bearer ${this.token}`;
    }
    return headers;
  }

  async checkStatus() {
    const response = await fetch(`${this.baseUrl}/status`, {
      headers: this.getHeaders()
    });
    return response.json();
  }

  connectWebSocket() {
    const headers = this.getHeaders();
    return new WebSocket(`wss://${this.baseUrl}/session`, {
      headers
    });
  }
}

// Usage
const freeClient = new DuckEClient();  // 5 conn/min
const premiumClient = new DuckEClient('your_jwt_token');  // 20 conn/min
```

## Security Best Practices

### 1. Token Storage

**Browser/Frontend:**
```javascript
// ✅ GOOD: Use httpOnly cookies (server-side)
// ❌ BAD: localStorage (XSS vulnerable)
// ⚠️ OK: sessionStorage (better than localStorage)
```

**Mobile Apps:**
```swift
// ✅ GOOD: iOS Keychain
// ✅ GOOD: Android KeyStore
// ❌ BAD: SharedPreferences, UserDefaults
```

### 2. Token Transmission

```bash
# ✅ ALWAYS use HTTPS
wss://api.ducke.io/session

# ❌ NEVER use HTTP/WS
ws://api.ducke.io/session  # Tokens sent in plain text!
```

### 3. Token Validation

**Server-side validation** (DUCK-E handles this):
- Signature verification
- Expiration checking
- Revocation status
- Required claims validation

### 4. Token Revocation

DUCK-E supports token revocation via Redis:

```python
from app.middleware.auth import revoke_token

# Revoke token by JWT ID
revoke_token(jti="unique-token-id-abc123")
```

**Use Cases:**
- User logout
- Suspicious activity detected
- Password changed
- Account compromised

## Configuration

### Environment Variables

```bash
# .env file

# JWT Configuration
JWT_SECRET_KEY=your-256-bit-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=120
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# Redis (optional, for token revocation)
REDIS_URL=redis://localhost:6379/0
```

### Generating Secret Keys

**Secure random secret**:

```bash
# Option 1: OpenSSL
openssl rand -hex 32

# Option 2: Python
python -c "import secrets; print(secrets.token_hex(32))"

# Option 3: Node.js
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

## Rate Limit Headers

DUCK-E includes rate limit information in response headers:

```http
HTTP/1.1 200 OK
X-RateLimit-Limit: 20
X-RateLimit-Remaining: 15
X-RateLimit-Reset: 1699999999
X-User-Tier: premium
Content-Type: application/json
```

**Header Meanings:**
- `X-RateLimit-Limit`: Requests allowed per window (based on tier)
- `X-RateLimit-Remaining`: Requests remaining in current window
- `X-RateLimit-Reset`: Unix timestamp when limit resets
- `X-User-Tier`: Current user tier (free/premium/enterprise)

## Monitoring & Metrics

### Prometheus Metrics

DUCK-E exposes authentication metrics:

```
# Failed authentication attempts
auth_failures_total{tier="free",reason="expired"} 15
auth_failures_total{tier="premium",reason="invalid_signature"} 3

# Successful authentications by tier
auth_success_total{tier="premium"} 1245
auth_success_total{tier="enterprise"} 89

# Rate limit hits by tier
rate_limit_exceeded_total{tier="free",endpoint="/session"} 234
rate_limit_exceeded_total{tier="premium",endpoint="/session"} 12
```

### Logging

**Authentication events are logged:**

```
[INFO] Token validated successfully: user=user_123 tier=premium
[WARN] Expired token attempted: user=user_456 tier=premium
[WARN] Invalid signature detected: ip=192.168.1.1
[ERROR] Token revocation check failed: redis_error
```

## Testing

### Manual Testing

```bash
# 1. Generate test token
export TEST_TOKEN=$(python -c "
from jose import jwt
from datetime import datetime, timedelta
payload = {
    'sub': 'test_user',
    'tier': 'premium',
    'exp': datetime.utcnow() + timedelta(hours=2)
}
print(jwt.encode(payload, 'test-secret', algorithm='HS256'))
")

# 2. Test anonymous access (free tier)
curl https://api.ducke.io/status

# 3. Test authenticated access (premium tier)
curl https://api.ducke.io/auth/me \
  -H "Authorization: Bearer $TEST_TOKEN"

# 4. Test expired token (should be rejected with HTTP 401)
export EXPIRED_TOKEN=$(python -c "
from jose import jwt
from datetime import datetime, timedelta
payload = {
    'sub': 'test_user',
    'tier': 'premium',
    'exp': datetime.utcnow() - timedelta(hours=1)
}
print(jwt.encode(payload, 'test-secret', algorithm='HS256'))
")

curl https://api.ducke.io/auth/me \
  -H "Authorization: Bearer $EXPIRED_TOKEN"
```

For a route that requires authentication (such as `/auth/me`), the expired
token returns 401. Public routes without a token continue to use free-tier
access.

### Automated Testing

See `/tests/security/test_authentication.py` for comprehensive test suite covering:
- Token generation and validation
- Tier detection
- Rate limiting by tier
- Expired/malformed token handling
- Token refresh mechanism
- Security features (revocation, binding)

## FAQ

### Q: What happens if I don't provide a token?

**A**: You get free tier access automatically. No errors, service works normally with free tier limits (5 conn/min, $5 budget).

### Q: What happens if my token expires?

**A**: A request with the expired token receives HTTP 401; a WebSocket
connection with it closes using code 1008. Refresh the access token through
your auth service, then retry. A request with no token still uses free-tier
access.

### Q: Can I use API keys instead of JWT?

**A**: Currently only JWT Bearer tokens are supported. API key support may be added in future versions.

### Q: How do I upgrade from free to premium?

**A**: Obtain a JWT token from your authentication service with `"tier": "premium"` in the payload. No changes needed in DUCK-E.

### Q: Is token revocation required?

**A**: No, it's optional. Revocation requires Redis. Without Redis, tokens remain valid until expiration.

### Q: How secure is this?

**A**:
- ✅ Industry-standard JWT (RFC 7519)
- ✅ HMAC-SHA256 signatures
- ✅ Expiration enforcement
- ✅ HTTPS/WSS only
- ✅ Optional token revocation
- ✅ Session binding (user-agent, IP)
- ⚠️ Rotate JWT_SECRET_KEY regularly
- ⚠️ Use strong secrets (256+ bits)

## Support

For issues or questions:
- **GitHub Issues**: https://github.com/your-org/duck-e/issues
- **Documentation**: https://docs.ducke.io
- **Security**: security@ducke.io

---

**Last Updated**: 2026-10-11
**API Version**: 1.0.0
**Authentication**: Optional JWT Bearer
