# Google OAuth Implementation Documentation

## Overview

DUCK-E implements a complete Google OAuth 2.0 authentication flow for user identity and memory keying. This allows users to sign in with their Google account, enabling persistent memory that's keyed to their email address.

## Architecture

```
┌─────────────────┐      OAuth Flow      ┌──────────────────┐
│   Browser UI    │ ──────────────────> │  Google OAuth     │
│  (main.js)      │ <──────────────────  │   Authorization   │
└─────────────────┘      JWT Token       └──────────────────┘
         │                                    │
         │ JWT Token                          │
         ↓                                    │
┌─────────────────┐                          │
| FastAPI Backend │ <─────────────────────────┘
│  (main.py)      │
│                 │
│  • /auth/login    - Initiate OAuth flow
│  • /auth/callback - Handle OAuth callback  
│  • /auth/me       - Validate JWT & get user info
│  • /auth/config   - Check OAuth configuration
└─────────────────┘
         │
         │ User Identity (email)
         ↓
┌─────────────────┐
│  Memory System  │
│  (memory.py)    │
│                 │
│  • UserMemoryStore(user_email)
│  • Per-user facts keyed by email
│  • JSON file storage: /data/memory/{email_hash}.json
└─────────────────┘
```

## Components

### 1. Backend OAuth Module (`app/middleware/google_oauth.py`)

**Key Functions:**

- `is_oauth_configured()` - Checks if `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set
- `initiate_login()` - Starts the OAuth flow by redirecting to Google
- `handle_callback()` - Processes OAuth callback, exchanges code for tokens, creates JWT
- `get_user_info_from_token()` - Extracts user info from JWT token
- `cleanup_expired_states()` - Removes expired OAuth states (runs every 5 minutes)
- `cleanup_expired_sessions()` - Removes expired OAuth sessions (7-day TTL)

**OAuth Flow:**

1. User clicks "Sign in with Google" → `/auth/login`
2. Redirect to Google OAuth authorization page
3. User grants permissions → redirect to `/auth/callback?code=...&state=...`
4. Backend exchanges code for Google access token
5. Backend fetches user info from Google
6. Backend creates JWT token with user identity
7. JWT token returned to frontend (via URL params or JSON)
8. Frontend stores JWT in `localStorage`

**JWT Token Payload:**

```json
{
  "sub": "user@example.com",
  "tier": "premium",
  "name": "John Doe",
  "email": "user@example.com",
  "picture": "https://...",
  "google_id": "123456789",
  "auth_method": "google_oauth",
  "exp": 1234567890,
  "iat": 1234567890,
  "jti": "unique-token-id",
  "token_type": "access"
}
```

### 2. Frontend Integration (`app/website_files/static/main.js`)

**OAuth State Management:**

```javascript
// Stored in localStorage
oauthToken = null;           // JWT access token
userInfo = null;             // { email, name, picture }

// Storage keys
STORAGE_KEYS = {
  TOKEN: 'ducke_oauth_token',
  USER_INFO: 'ducke_user_info'
}
```

**Key Functions:**

- `loadOAuthState()` - Loads OAuth state from localStorage on page load
- `saveOAuthState(token, user)` - Saves OAuth state to localStorage
- `clearOAuthState()` - Clears OAuth state on logout
- `updateLoginUI()` - Updates UI based on authentication state
- `handleLogin()` - Initiates OAuth flow by redirecting to `/auth/login`
- `handleLogout()` - Clears state and disconnects WebSocket

**WebSocket Authentication:**

```javascript
// Token passed as query parameter (WebSocket can't send custom headers)
const url = new URL(socketUrl);
if (oauthToken) {
  url.searchParams.set('token', oauthToken);
}
```

### 3. Main App Integration (`app/main.py`)

**OAuth Endpoints (conditionally registered):**

```python
if _oauth_available:
    @app.get("/auth/login")
    @app.get("/auth/callback") 
    @app.get("/auth/config")
    @app.get("/auth/me")
```

**WebSocket Authentication (lines 382-441):**

1. Extract JWT token from `Authorization` header or query parameter
2. Validate token and extract user info using `get_user_info_from_token()`
3. Fallback to proxy headers (`x-forwarded-email`, `x-forwarded-user`)
4. Determine `user_identity` (JWT email > forwarded email > forwarded user)

**Memory System Integration (lines 444-447):**

```python
if user_identity:
    memory_store = UserMemoryStore(user_identity)  # Keyed by email
    memory_store.load()
```

### 4. Memory System (`app/memory.py`)

**UserMemoryStore:**

```python
class UserMemoryStore:
    def __init__(self, user_id: str):
        self.user_id = user_id  # Email from OAuth
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()
        self.file_path = Path(f"/data/memory/{user_hash}.json")
```

**Memory Operations:**

- `add_fact()` - Add structured fact about the user
- `get_facts()` - Retrieve all facts
- `extract_and_save()` - Auto-extract facts from conversation
- `get_or_generate_summary()` - Get user summary for context

## Configuration

### Environment Variables

```bash
# Required for Google OAuth
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback

# JWT Configuration (required for OAuth)
JWT_SECRET_KEY=your_jwt_secret_key_here_change_in_production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=120
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

### Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing
3. Enable Google+ API
4. Create OAuth 2.0 credentials:
   - Application type: Web application
   - Authorized redirect URIs: `http://localhost:8000/auth/callback` (or your production URL)
5. Copy Client ID and Client Secret to environment variables

## Usage Flow

### 1. User Authentication

```
User clicks "Sign in with Google" 
→ Redirect to Google OAuth
→ User grants permissions
→ Redirect back to /auth/callback
→ Frontend receives JWT token
→ Token stored in localStorage
→ UI updated with user info
```

### 2. WebSocket Connection

```
User clicks "Connect"
→ WebSocket connection opened
→ JWT token passed as query parameter
→ Backend validates token
→ User identity extracted from token
→ Memory store initialized with user email
→ Session proceeds with authenticated context
```

### 3. Memory Persistence

```
During conversation
→ Facts extracted from dialogue
→ Stored in /data/memory/{email_hash}.json
→ On next session, memory loaded automatically
→ User summary injected into system message
```

## Security Features

1. **JWT Token Validation:** Tokens signed with `JWT_SECRET_KEY`, verified on every request
2. **Token Expiration:** Access tokens expire after 2 hours (configurable)
3. **State Parameter:** OAuth state parameter prevents CSRF attacks
4. **Token Refresh:** Refresh tokens support (7-day expiration)
5. **HTTPS Required:** Production uses HTTPS for token security
6. **Email Hashing:** User emails hashed for filenames (privacy)
7. **Memory Isolation:** Each user's memory stored in separate file

## Fallback Behavior

If OAuth is not configured, the system gracefully falls back:

1. **Frontend:** Login button hidden
2. **Backend:** Checks for proxy headers (`x-forwarded-email`, `x-forwarded-user`)
3. **Memory:** Works with any user identity from headers
4. **Local Dev:** Memory disabled if no auth available

## API Endpoints

### `/auth/login`
**Method:** GET  
**Query Params:** `redirect_uri` (optional)  
**Response:** Redirect to Google OAuth

### `/auth/callback`
**Method:** GET  
**Query Params:** `code`, `state`, `redirect_to_frontend` (optional)  
**Response:** JSON with JWT tokens or redirect to frontend

```json
{
  "access_token": "jwt_token_here",
  "refresh_token": "refresh_token_here", 
  "token_type": "Bearer",
  "user_info": {
    "email": "user@example.com",
    "name": "User Name",
    "picture": "https://...",
    "verified_email": true
  },
  "tier": "premium"
}
```

### `/auth/config`
**Method:** GET  
**Response:** OAuth configuration status

```json
{
  "configured": true,
  "login_url": "https://accounts.google.com/o/oauth2/v2/auth?...",
  "message": "Google OAuth is configured"
}
```

### `/auth/me`
**Method:** GET  
**Headers:** `Authorization: Bearer <token>`  
**Response:** User info from valid JWT

```json
{
  "authenticated": true,
  "user_info": {
    "email": "user@example.com",
    "name": "User Name",
    "picture": "https://...",
    "tier": "premium",
    "auth_method": "google_oauth",
    "google_id": "123456789"
  }
}
```

## Testing

### Manual Testing

1. **Check OAuth Configuration:**
   ```bash
   curl http://localhost:8000/auth/config
   ```

2. **Initiate OAuth Flow:**
   ```bash
   curl http://localhost:8000/auth/login
   ```

3. **Validate Token:**
   ```bash
   curl -H "Authorization: Bearer <token>" http://localhost:8000/auth/me
   ```

### Automated Testing

```python
# Test OAuth is properly configured
def test_oauth_configured():
    from app.middleware.google_oauth import is_oauth_configured
    assert is_oauth_configured() == True

# Test token creation and validation
def test_jwt_flow():
    from app.middleware.auth import create_access_token, validate_token
    payload = {"sub": "test@example.com", "tier": "premium"}
    token = create_access_token(payload)
    decoded = validate_token(token)
    assert decoded["sub"] == "test@example.com"
```

## Troubleshooting

**Issue:** "Google OAuth is not configured" error  
**Solution:** Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` environment variables

**Issue:** Token validation fails  
**Solution:** Ensure `JWT_SECRET_KEY` is consistent across all requests

**Issue:** Memory not persisting  
**Solution:** Check `/data` directory permissions and user identity extraction

**Issue:** OAuth redirect loop  
**Solution:** Verify `GOOGLE_REDIRECT_URI` matches Google Cloud Console configuration

## Production Deployment

1. **Environment Variables:** Set all required env vars in your production environment
2. **HTTPS:** Use HTTPS for all OAuth redirects (update `GOOGLE_REDIRECT_URI`)
3. **Domain:** Add production domain to Google OAuth authorized redirect URIs
4. **JWT Secret:** Use a strong, unique `JWT_SECRET_KEY` in production
5. **CORS:** Update `ALLOWED_ORIGINS` to include your production domain

## Current Implementation Status

✅ **COMPLETE** - Google OAuth implementation is fully functional:

- ✅ Backend OAuth module (`app/middleware/google_oauth.py`)
- ✅ JWT token creation and validation (`app/middleware/auth.py`)
- ✅ OAuth endpoints in main app (`/auth/login`, `/auth/callback`, `/auth/me`, `/auth/config`)
- ✅ Frontend integration with localStorage (`app/website_files/static/main.js`)
- ✅ UI components (login button, user info display)
- ✅ WebSocket authentication via JWT tokens
- ✅ Memory system keying by user email
- ✅ Fallback to proxy headers when OAuth unavailable
- ✅ Complete documentation in README.md
- ✅ Environment variables documented in `.env.example`

The implementation provides secure, persistent user identity for memory keying with graceful fallback for unauthenticated users.
