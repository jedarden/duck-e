# Security Headers & CORS Configuration Guide

## Overview

This guide explains the security headers and CORS configuration implemented in DUCK-E to protect the public-facing application from common web vulnerabilities.

## Table of Contents

- [Security Headers](#security-headers)
- [CORS Configuration](#cors-configuration)
- [WebSocket Security](#websocket-security)
- [Environment Variables](#environment-variables)
- [Testing](#testing)
- [Production Deployment](#production-deployment)

## Security Headers

### Implemented Headers

#### 1. Strict-Transport-Security (HSTS)

Forces browsers to use HTTPS connections only.

**Configuration:**
```yaml
hsts:
  enabled: true
  max_age: 31536000  # 1 year
  include_subdomains: true
  preload: false
```

**Environment Variable:**
```bash
ENABLE_HSTS=true
HSTS_MAX_AGE=31536000
```

**Header Value:**
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
```

**Benefits:**
- Prevents man-in-the-middle attacks
- Prevents protocol downgrade attacks
- Protects against cookie hijacking

**IMPORTANT:** Only enable HSTS when your application is fully HTTPS. Once enabled, browsers will refuse HTTP connections for the specified duration.

#### 2. X-Content-Type-Options

Prevents MIME type sniffing.

**Header Value:**
```
X-Content-Type-Options: nosniff
```

**Benefits:**
- Prevents browsers from executing malicious content
- Reduces XSS attack surface

#### 3. X-Frame-Options

Prevents clickjacking attacks.

**Header Value:**
```
X-Frame-Options: DENY
```

**Benefits:**
- Prevents the application from being embedded in iframes
- Protects against UI redressing attacks

#### 4. X-XSS-Protection

Enables browser XSS filters (legacy support).

**Header Value:**
```
X-XSS-Protection: 1; mode=block
```

**Benefits:**
- Additional XSS protection for older browsers
- Works alongside CSP

#### 5. Content-Security-Policy (CSP)

Controls which resources can be loaded and executed.

**Default Policy:**
```
default-src 'self';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
img-src 'self' data: https:;
connect-src 'self' wss: https:;
font-src 'self' data:;
object-src 'none';
base-uri 'self';
form-action 'self';
frame-ancestors 'none';
upgrade-insecure-requests
```

**Configuration:**
```yaml
csp:
  use_default: true
  report_uri: https://example.com/csp-report  # Optional
```

**Environment Variable:**
```bash
CSP_REPORT_URI=https://example.com/csp-report
CUSTOM_CSP="default-src 'self'; script-src 'self'"
```

**Benefits:**
- Prevents XSS attacks
- Controls resource loading
- Prevents data exfiltration
- Provides violation reporting

**Note:** The policy allows `'unsafe-inline'` for scripts and styles due to WebRTC requirements. In production, consider using nonces or hashes for inline scripts.

#### 6. Permissions-Policy

Controls browser feature access.

**Default Policy:**
```
microphone=(self), camera=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()
```

**Benefits:**
- Restricts microphone to same origin only
- Disables camera, geolocation, and other sensors
- Reduces attack surface

#### 7. Referrer-Policy

Controls referrer information in requests.

**Header Value:**
```
Referrer-Policy: strict-origin-when-cross-origin
```

**Benefits:**
- Protects user privacy
- Prevents information leakage

## CORS Configuration

### Overview

Cross-Origin Resource Sharing (CORS) controls which domains can access your API.

### Configuration

**config/security.yaml:**
```yaml
cors:
  allowed_origins:
    - https://duck-e.example.com
    - https://*.duck-e.example.com
  allow_credentials: true
  allowed_methods:
    - GET
    - POST
    - OPTIONS
  allowed_headers:
    - "*"
  max_age: 600
```

**Environment Variable:**
```bash
ALLOWED_ORIGINS=https://duck-e.example.com,https://app.duck-e.example.com
ALLOW_CREDENTIALS=true
CORS_MAX_AGE=600
```

### Wildcard Support

The configuration supports wildcard subdomains:

```yaml
allowed_origins:
  - https://*.example.com  # Matches app.example.com, api.example.com, etc.
```

### Development vs Production

**Development:**
```bash
ENVIRONMENT=development
# Automatically allows localhost origins
```

**Production:**
```bash
ENVIRONMENT=production
ALLOWED_ORIGINS=https://duck-e.example.com
# Strict origin validation required
```

## WebSocket Security

### Origin Validation

WebSocket connections are validated against allowed origins.

**Implementation:**
```python
from app.middleware.websocket_validator import get_websocket_security_middleware

ws_security = get_websocket_security_middleware()

@app.websocket("/session")
async def handle_media_stream(websocket: WebSocket):
    if not await ws_security.validate_connection(websocket):
        return  # Connection rejected

    # Connection accepted, proceed with logic
```

**Configuration:**
```yaml
websocket:
  validate_origin: true
  require_origin: true
  connection_timeout: 300
```

**Environment Variables:**
```bash
WS_CONNECTION_TIMEOUT=300
```

### Benefits

- Prevents unauthorized WebSocket connections
- Audit trail of connection attempts
- Protection against CSRF attacks

## Environment Variables

### Complete List

```bash
# CORS Configuration
ALLOWED_ORIGINS=https://duck-e.example.com,https://*.duck-e.example.com
ALLOW_CREDENTIALS=true
CORS_MAX_AGE=600

# Security Headers
ENABLE_HSTS=true
HSTS_MAX_AGE=31536000
CSP_REPORT_URI=https://example.com/csp-report
CUSTOM_CSP=  # Optional: Override default CSP

# WebSocket Security
WS_CONNECTION_TIMEOUT=300

# Environment
ENVIRONMENT=production  # or development
```

### .env Example

```bash
# Development
ENVIRONMENT=development
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8000
ENABLE_HSTS=false

# Production
# ENVIRONMENT=production
# ALLOWED_ORIGINS=https://duck-e.example.com
# ENABLE_HSTS=true
# CSP_REPORT_URI=https://duck-e.example.com/csp-report
```

## Testing

### 1. Test Security Headers

```bash
curl -I https://duck-e.example.com/
```

**Expected Headers:**
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Content-Security-Policy: default-src 'self'; ...
Permissions-Policy: microphone=(self), camera=(), ...
Referrer-Policy: strict-origin-when-cross-origin
```

### 2. Test CORS

```bash
# Valid origin
curl -H "Origin: https://duck-e.example.com" \
     -H "Access-Control-Request-Method: POST" \
     -H "Access-Control-Request-Headers: Content-Type" \
     -X OPTIONS \
     https://duck-e.example.com/status

# Invalid origin (should be rejected)
curl -H "Origin: https://evil.com" \
     -H "Access-Control-Request-Method: POST" \
     -X OPTIONS \
     https://duck-e.example.com/status
```

### 3. Test WebSocket Origin Validation

```javascript
// Valid origin - should connect
const ws = new WebSocket('wss://duck-e.example.com/session');

// Invalid origin - should be rejected
// (Test from different domain)
```

### 4. Test CSP Violations

Open browser console and check for CSP violations when loading the application.

### 5. Integration Tests

```python
# tests/test_security_headers.py
import pytest
from fastapi.testclient import TestClient

def test_security_headers(client: TestClient):
    response = client.get("/")

    assert "strict-transport-security" in response.headers
    assert "x-content-type-options" in response.headers
    assert "x-frame-options" in response.headers
    assert "content-security-policy" in response.headers

def test_cors_valid_origin(client: TestClient):
    response = client.options(
        "/status",
        headers={"Origin": "https://duck-e.example.com"}
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers

def test_cors_invalid_origin(client: TestClient):
    response = client.options(
        "/status",
        headers={"Origin": "https://evil.com"}
    )

    # Should not include CORS headers
    assert "access-control-allow-origin" not in response.headers
```

## Production Deployment

### Pre-Deployment Checklist

1. **Configure Allowed Origins**
   ```bash
   ALLOWED_ORIGINS=https://duck-e.example.com,https://*.duck-e.example.com
   ```

2. **Enable HSTS**
   ```bash
   ENABLE_HSTS=true
   HSTS_MAX_AGE=31536000
   ```

3. **Set Environment**
   ```bash
   ENVIRONMENT=production
   ```

4. **Configure CSP Reporting** (Optional)
   ```bash
   CSP_REPORT_URI=https://duck-e.example.com/csp-report
   ```

5. **Test SSL/TLS Configuration**
   - Ensure HTTPS is properly configured
   - Test with SSL Labs: https://www.ssllabs.com/ssltest/

6. **Review CSP Policy**
   - Consider removing `'unsafe-inline'` if possible
   - Use nonces or hashes for inline scripts

### Monitoring

1. **CSP Violation Reports**
   - Set up endpoint to collect CSP violation reports
   - Monitor for unexpected violations

2. **Security Logs**
   - Monitor rejected WebSocket connections
   - Track CORS preflight failures
   - Review security event logs

3. **Security Scanning**
   - Use tools like OWASP ZAP or Burp Suite
   - Regular penetration testing
   - Automated security scans in CI/CD

### HSTS Preloading

Once you're confident in your HTTPS setup:

1. Enable preload in configuration:
   ```yaml
   hsts:
     preload: true
   ```

2. Submit your domain to HSTS preload list:
   https://hstspreload.org/

**WARNING:** HSTS preloading is permanent and affects all subdomains. Only enable when you're certain all services are HTTPS-ready.

## Troubleshooting

### CORS Issues

**Problem:** Requests from valid origin are blocked

**Solution:**
1. Check `ALLOWED_ORIGINS` environment variable
2. Verify origin format (include protocol and port)
3. Check browser console for specific error messages

### WebSocket Connection Rejected

**Problem:** WebSocket connections fail with origin validation error

**Solution:**
1. Ensure WebSocket origin matches allowed origins
2. Check that `Origin` header is being sent
3. Review logs for specific rejection reasons

### CSP Violations

**Problem:** Application features broken due to CSP

**Solution:**
1. Check browser console for CSP violation reports
2. Adjust CSP policy to allow necessary resources
3. Use CSP report URI to collect violations

## Security Best Practices

1. **Always use HTTPS in production**
2. **Set strict CORS policies**
3. **Monitor CSP violation reports**
4. **Keep security headers up to date**
5. **Regular security audits**
6. **Review and update allowed origins regularly**
7. **Use environment-specific configurations**
8. **Enable security logging**
9. **Test security headers before deployment**
10. **Keep dependencies updated**

## References

- [OWASP Secure Headers Project](https://owasp.org/www-project-secure-headers/)
- [MDN Web Security](https://developer.mozilla.org/en-US/docs/Web/Security)
- [Content Security Policy Reference](https://content-security-policy.com/)
- [HSTS Preload List](https://hstspreload.org/)
- [CORS Specification](https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS)

## Support

For security issues or questions:
- Review this documentation
- Check application logs
- Consult OWASP resources
- Report security vulnerabilities responsibly
