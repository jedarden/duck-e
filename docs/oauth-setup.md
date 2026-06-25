# Google OAuth Setup Guide

This guide explains how to configure and use Google OAuth for user authentication in DUCK-E, enabling persistent memory keyed to user identity.

## Overview

DUCK-E supports Google OAuth 2.0 authentication, allowing users to sign in with their Google account. When authenticated, user memory is keyed to their email address, enabling persistent preferences and conversation history across sessions.

## How It Works

1. **User clicks "Sign in with Google"** → Redirected to Google OAuth consent screen
2. **User grants permission** → Google redirects back with authorization code
3. **Backend exchanges code for tokens** → Creates JWT with user identity
4. **Frontend stores JWT** → Used for WebSocket authentication
5. **WebSocket connection authenticated** → Memory keyed to user email
6. **Preferences persist** → Next session remembers user-specific data

## Setup Instructions

### Step 1: Create Google OAuth 2.0 Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Navigate to: **APIs & Services** → **Credentials**
4. Click **Create Credentials** → **OAuth client ID**
5. Application type: **Web application**
6. Name: `DUCK-E` or your preferred name
7. Authorized redirect URIs:
   - Production: `https://your-domain.com/auth/callback`
   - Development: `http://localhost:8000/auth/callback`
8. Click **Create**
9. Copy the **Client ID** and **Client Secret**

### Step 2: Configure Environment Variables

Add the following to your `.env` file or deployment environment:

```bash
# Required for Google OAuth
GOOGLE_CLIENT_ID=your-google-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-google-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback

# Required for JWT token creation
JWT_SECRET_KEY=your-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=120
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
```

### Step 3: Update Redirect URI for Production

For production deployments, update `GOOGLE_REDIRECT_URI` to match your domain:

```bash
GOOGLE_REDIRECT_URI=https://your-domain.com/auth/callback
```

Make sure to add this URL to your Google OAuth client's authorized redirect URIs.

## Architecture

### Components

1. **`app/middleware/google_oauth.py`**: Google OAuth flow implementation
   - OAuth state management
   - Token exchange with Google
   - User info retrieval
   - JWT token creation

2. **`app/middleware/auth.py`**: JWT authentication
   - Token validation
   - User tier management
   - Security features (token revocation, IP binding)

3. **`app/memory.py`**: User memory store
   - Keyed by user email
   - Persistent facts and preferences
   - Automatic memory extraction

4. **Frontend (`main.js`, `chat.html`)**: OAuth UI and token handling
   - Login/logout buttons
   - Token storage (localStorage)
   - WebSocket authentication

### Flow Diagram

```
┌─────────┐         ┌──────────┐         ┌──────────┐
│ Browser │ ──────>│  DUCK-E  │ ──────>│ Google   │
│ (User)  │<────────│  Backend  │<────────│ OAuth    │
└─────────┘         └──────────┘         └──────────┘
      │                   │                     
      │ 1. Login button   │                     
      │<──────────────────┘                     
      │                                        
      │ 2. Redirect to Google OAuth           
      │───────────────────┐                   
      │                   │                   
      │                   ├──>Google OAuth     
      │                   │<──(auth code)      
      │<──────────────────┘                   
      │ 3. JWT token + user info               
      │───────────────┐                        
      │               │                        
      │               ├──>Create JWT          
      │               │<──(access_token)      
      │<──────────────┘                        
      │ 4. Store token, connect WebSocket      
      │───────────────────────┐               
      │                       │               
      │                       ├──>WebSocket + JWT
      │                       │<──(authenticated)
      │<───────────────────────┘               
      │ 5. Memory keyed to email               
```

## API Endpoints

### OAuth Endpoints

- **`GET /auth/login`**: Initiate OAuth flow
  - Redirects to Google OAuth consent screen
  
- **`GET /auth/callback`**: Handle OAuth callback
  - Query params: `code`, `state`
  - Returns: JWT access token + user info
  
- **`GET /auth/config`**: Check OAuth status
  - Returns: `{configured: bool, login_url: str}`
  
- **`GET /auth/me`**: Validate JWT and get user info
  - Headers: `Authorization: Bearer <token>`
  - Returns: User profile data

## Testing

### Manual Testing

1. Start the server with OAuth configured:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. Open `http://localhost:8000` in browser

3. Click "Sign in with Google"

4. Grant permission on Google OAuth screen

5. Verify user info appears in header after redirect

6. Click "Connect" to establish authenticated WebSocket

7. Say something like "My name is Alice and I prefer dark mode"

8. Disconnect and reconnect, then ask "What's my name?" - memory should persist

### Automated Testing

Run the OAuth integration tests:

```bash
pytest tests/integration/test_oauth_integration.py -v
```

## Memory Persistence

### How Memory Keying Works

When a user authenticates via OAuth:

1. JWT token contains `sub` claim with user email
2. WebSocket connection validates JWT and extracts email
3. `UserMemoryStore` initialized with email as user_id
4. Memory file created with SHA-256 hash of email as filename
5. All user facts stored in `/data/memory/{email_hash}.json`

### Memory Example

For user `alice@example.com`:
- Email hash: `a1b2c3d4e5f6...` (SHA-256 of email)
- Memory file: `/data/memory/a1b2c3d4e5f6....json`
- Contents:
  ```json
  {
    "user_id": "alice@example.com",
    "created_at": "2026-01-15T10:30:00Z",
    "facts": [
      {
        "text": "User prefers dark mode",
        "category": "preference",
        "confidence": 1.0,
        "source": "explicit",
        "created_at": "2026-01-15T10:31:00Z",
        "last_referenced": "2026-01-15T10:31:00Z"
      }
    ]
  }
  ```

## Security Features

### JWT Token Security

- **Expiration**: Access tokens expire in 2 hours (configurable)
- **Refresh tokens**: Valid for 7 days
- **Unique JTI**: Each token has unique ID for revocation
- **Signature verification**: Prevents token tampering
- **User agent binding**: Optional protection against session hijacking
- **IP binding**: Optional IP-based validation

### OAuth Security

- **State parameter**: CSRF protection
- **PKCE**: Not implemented (using server-side flow)
- **HTTPS required**: Production deployments must use HTTPS
- **Token storage**: Stored in localStorage (consider httpOnly cookies for production)

## Troubleshooting

### OAuth Button Not Showing

**Problem**: "Sign in with Google" button doesn't appear

**Solutions**:
1. Verify environment variables are set
2. Check `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are not empty
3. Check browser console for errors
4. Verify `/auth/config` returns `"configured": true`

### Callback Error

**Problem**: OAuth callback returns error

**Solutions**:
1. Verify redirect URI matches Google OAuth configuration
2. Check `GOOGLE_REDIRECT_URI` environment variable
3. Ensure Google OAuth client has correct authorized redirect URIs
4. Check server logs for detailed error messages

### Memory Not Persisting

**Problem**: User memory doesn't persist across sessions

**Solutions**:
1. Verify `/data/memory/` directory exists and is writable
2. Check JWT token is being sent with WebSocket connection
3. Verify user email is extracted from token (check logs)
4. Test memory system directly with integration tests

### Invalid Token Errors

**Problem**: JWT validation fails with 401 errors

**Solutions**:
1. Verify `JWT_SECRET_KEY` is consistent across all requests
2. Check token hasn't expired (default 2 hours)
3. Verify token hasn't been revoked
4. Check `Authorization` header format: `Bearer <token>`

## Deployment Considerations

### Production Checklist

- [ ] Set `GOOGLE_REDIRECT_URI` to production domain
- [ ] Add production redirect URI to Google OAuth client
- [ ] Use strong `JWT_SECRET_KEY` (generate with `openssl rand -hex 32`)
- [ ] Enable HTTPS (required for OAuth)
- [ ] Set appropriate cookie security flags
- [ ] Configure CORS for production domain
- [ ] Test OAuth flow end-to-end
- [ ] Set up monitoring for OAuth failures
- [ ] Configure backup for `/data/memory/` directory

### Docker Deployment

```dockerfile
# In your Dockerfile or docker-compose.yml
environment:
  - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
  - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
  - GOOGLE_REDIRECT_URI=https://your-domain.com/auth/callback
  - JWT_SECRET_KEY=${JWT_SECRET_KEY}
```

## User Tiers and OAuth

When users authenticate via OAuth, they automatically get **premium tier**:

- **Free tier** (anonymous): 5 connections/minute, $5 session budget
- **Premium tier** (OAuth): 20 connections/minute, $20 session budget
- **Enterprise tier**: 100 connections/minute, $100 session budget

OAuth-authenticated users can also be assigned enterprise tier by modifying the JWT creation logic in `handle_oauth_callback()`.

## Related Documentation

- [Memory System Documentation](../memory.md)
- [Security Documentation](./security.md)
- [API Documentation](./api.md)
- [Deployment Guide](./deployment.md)

## Support

For issues or questions:
1. Check server logs: `docker-compose logs -f duck-e`
2. Run integration tests: `pytest tests/integration/test_oauth_integration.py`
3. Verify environment variables: Check `/auth/config` endpoint
4. Review Google Cloud Console OAuth settings
