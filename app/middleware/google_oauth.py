"""
Google OAuth 2.0 Authentication Module
Implements OAuth 2.0 flow for Google user authentication
"""
import os
import json
import secrets
import logging
from typing import Optional, Dict, Any
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
import httpx
from jose import jwt

from app.middleware.auth import (
    create_access_token,
    create_refresh_token,
    JWT_SECRET_KEY,
    JWT_ALGORITHM
)

logger = logging.getLogger(__name__)

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
GOOGLE_AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

# OAuth State Storage (in-memory for now - can be replaced with Redis)
_oauth_states: Dict[str, Dict[str, Any]] = {}

# Session storage (in-memory for now - can be replaced with Redis)
_oauth_sessions: Dict[str, Dict[str, Any]] = {}


def is_oauth_configured() -> bool:
    """Check if Google OAuth is properly configured"""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def generate_state_param() -> str:
    """Generate a secure state parameter for OAuth flow"""
    return secrets.token_urlsafe(32)


def store_state(state: str, redirect_uri: str = "") -> None:
    """Store OAuth state parameter"""
    _oauth_states[state] = {
        "redirect_uri": redirect_uri,
        "created_at": datetime.now(timezone.utc)
    }


def validate_state(state: str) -> bool:
    """
    Validate OAuth state parameter
    Returns True if valid, False otherwise
    """
    if state not in _oauth_states:
        logger.warning(f"Invalid OAuth state: {state}")
        return False

    state_data = _oauth_states[state]
    created_at = state_data["created_at"]

    # State expires after 10 minutes
    if datetime.now(timezone.utc) - created_at > timedelta(minutes=10):
        logger.warning(f"Expired OAuth state: {state}")
        del _oauth_states[state]
        return False

    return True


def consume_state(state: str) -> Optional[str]:
    """
    Consume and return the redirect URI from state
    Removes state from storage after use
    """
    if state not in _oauth_states:
        return None

    redirect_uri = _oauth_states[state]["redirect_uri"]
    del _oauth_states[state]
    return redirect_uri


def store_session(session_id: str, tokens: Dict[str, Any], user_info: Dict[str, Any]) -> None:
    """Store OAuth session with tokens and user info"""
    _oauth_sessions[session_id] = {
        "tokens": tokens,
        "user_info": user_info,
        "created_at": datetime.now(timezone.utc),
        "last_accessed": datetime.now(timezone.utc)
    }


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get OAuth session by ID"""
    if session_id not in _oauth_sessions:
        return None

    session = _oauth_sessions[session_id]
    session["last_accessed"] = datetime.now(timezone.utc)
    return session


def cleanup_expired_states() -> None:
    """Clean up expired OAuth states (older than 10 minutes)"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    expired_states = [
        state for state, data in _oauth_states.items()
        if data["created_at"] < cutoff
    ]
    for state in expired_states:
        del _oauth_states[state]
    if expired_states:
        logger.info(f"Cleaned up {len(expired_states)} expired OAuth states")


def cleanup_expired_sessions() -> None:
    """Clean up expired OAuth sessions (older than 7 days)"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    expired_sessions = [
        session_id for session_id, data in _oauth_sessions.items()
        if data["created_at"] < cutoff
    ]
    for session_id in expired_sessions:
        del _oauth_sessions[session_id]
    if expired_sessions:
        logger.info(f"Cleaned up {len(expired_sessions)} expired OAuth sessions")


async def get_google_authorization_url(
    redirect_uri: str = "",
    state_hint: str = ""
) -> Dict[str, str]:
    """
    Generate Google OAuth authorization URL
    Returns dict with authorization_url and state parameter
    """
    if not is_oauth_configured():
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
        )

    # Use provided redirect_uri or fall back to configured one
    final_redirect = redirect_uri or GOOGLE_REDIRECT_URI

    # Generate state parameter
    state = state_hint or generate_state_param()
    store_state(state, redirect_uri=redirect_uri)

    # Build OAuth parameters
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": final_redirect,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "offline",  # Get refresh token
        "prompt": "consent"  # Force consent to get refresh token
    }

    # Build authorization URL
    auth_url = f"{GOOGLE_AUTHORIZATION_URL}?{urlencode(params)}"

    return {
        "authorization_url": auth_url,
        "state": state
    }


async def exchange_code_for_tokens(code: str, redirect_uri: str = "") -> Dict[str, Any]:
    """
    Exchange authorization code for access token
    Returns dict with access_token, refresh_token, expires_in
    """
    if not is_oauth_configured():
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured"
        )

    # Use provided redirect_uri or fall back to configured one
    final_redirect = redirect_uri or GOOGLE_REDIRECT_URI

    # Prepare token request
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": final_redirect,
        "grant_type": "authorization_code"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(GOOGLE_TOKEN_URL, data=data)
            response.raise_for_status()

            token_data = response.json()

            # Return relevant token information
            return {
                "access_token": token_data.get("access_token"),
                "refresh_token": token_data.get("refresh_token"),
                "expires_in": token_data.get("expires_in", 3600),
                "token_type": token_data.get("token_type", "Bearer")
            }

        except httpx.HTTPError as e:
            logger.error(f"Error exchanging code for tokens: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to exchange authorization code for tokens"
            )


async def get_user_info(access_token: str) -> Dict[str, Any]:
    """
    Get user information from Google using access token
    Returns dict with id, email, name, picture, verified_email
    """
    if not is_oauth_configured():
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured"
        )

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(GOOGLE_USERINFO_URL, headers=headers)
            response.raise_for_status()

            user_info = response.json()

            # Return relevant user information
            return {
                "id": user_info.get("id"),
                "email": user_info.get("email"),
                "name": user_info.get("name"),
                "given_name": user_info.get("given_name"),
                "family_name": user_info.get("family_name"),
                "picture": user_info.get("picture"),
                "verified_email": user_info.get("verified_email", False)
            }

        except httpx.HTTPError as e:
            logger.error(f"Error fetching user info: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to fetch user information from Google"
            )


async def handle_oauth_callback(
    code: str,
    state: str,
    redirect_uri: str = ""
) -> Dict[str, Any]:
    """
    Handle OAuth callback from Google
    Returns dict with jwt_tokens and user_info
    """
    # Validate state parameter
    if not validate_state(state):
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state parameter"
        )

    # Consume state and get redirect URI
    original_redirect = consume_state(state)

    # Exchange code for tokens
    tokens = await exchange_code_for_tokens(code, redirect_uri or original_redirect or "")

    # Get user information
    user_info = await get_user_info(tokens["access_token"])

    # Create JWT tokens with user identity
    jwt_payload = {
        "sub": user_info["email"],  # Subject is the user's email
        "tier": "premium",  # OAuth users get premium tier
        "name": user_info.get("name", ""),
        "email": user_info["email"],
        "picture": user_info.get("picture", ""),
        "google_id": user_info["id"],
        "auth_method": "google_oauth"
    }

    # Create access and refresh tokens
    access_token = create_access_token(jwt_payload)
    refresh_token = create_refresh_token(jwt_payload)

    # Store session
    session_id = jwt_payload["sub"]
    store_session(session_id, {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_in": tokens["expires_in"]
    }, user_info)

    logger.info(f"User authenticated via Google OAuth: {user_info['email']}")

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "user_info": user_info,
        "tier": "premium"
    }


async def refresh_google_token(refresh_token: str) -> Dict[str, Any]:
    """
    Refresh Google OAuth access token using refresh token
    Returns dict with new access_token and optionally new refresh_token
    """
    if not is_oauth_configured():
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured"
        )

    data = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(GOOGLE_TOKEN_URL, data=data)
            response.raise_for_status()

            token_data = response.json()

            return {
                "access_token": token_data.get("access_token"),
                "refresh_token": token_data.get("refresh_token", refresh_token),  # May return new refresh token
                "expires_in": token_data.get("expires_in", 3600)
            }

        except httpx.HTTPError as e:
            logger.error(f"Error refreshing Google token: {e}")
            raise HTTPException(
                status_code=500,
                detail="Failed to refresh Google access token"
            )


def get_user_info_from_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Extract user information from JWT token
    Returns dict with email, name, picture, tier, auth_method or None
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM]
        )

        # Check if this is an OAuth-authenticated token
        if payload.get("auth_method") != "google_oauth":
            return None

        return {
            "email": payload.get("email"),
            "name": payload.get("name"),
            "picture": payload.get("picture"),
            "tier": payload.get("tier", "premium"),
            "auth_method": payload.get("auth_method"),
            "google_id": payload.get("google_id")
        }

    except jwt.JWTError as e:
        logger.error(f"Error decoding JWT token: {e}")
        return None


async def initiate_login(request: Request, redirect_uri: str = "") -> Response:
    """
    Initiate Google OAuth login flow
    Returns redirect to Google authorization page
    """
    if not is_oauth_configured():
        return JSONResponse(
            status_code=500,
            content={"error": "Google OAuth is not configured"}
        )

    # Generate authorization URL
    auth_data = await get_google_authorization_url(redirect_uri=redirect_uri)

    # Redirect to Google OAuth
    return RedirectResponse(url=auth_data["authorization_url"])


async def handle_callback(request: Request) -> Response:
    """
    Handle OAuth callback from Google
    Returns JSON response with tokens or redirects to frontend
    """
    # Get query parameters
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")
    redirect_to_frontend = request.query_params.get("redirect_to_frontend", "true")

    # Handle errors
    if error:
        logger.warning(f"OAuth error: {error}")
        if redirect_to_frontend == "true":
            # Redirect to frontend with error
            error_url = f"/?error={error}"
            return RedirectResponse(url=error_url, status_code=302)
        return JSONResponse(
            status_code=400,
            content={"error": f"OAuth authentication failed: {error}"}
        )

    if not code or not state:
        if redirect_to_frontend == "true":
            return RedirectResponse(url="/?error=missing_code_or_state", status_code=302)
        return JSONResponse(
            status_code=400,
            content={"error": "Missing code or state parameter"}
        )

    try:
        # Handle the callback
        result = await handle_oauth_callback(code, state)

        # For frontend redirect (default behavior)
        if redirect_to_frontend == "true":
            # Redirect to frontend with access token and user info
            from urllib.parse import urlencode
            params = {
                "access_token": result["access_token"],
                "user_info": json.dumps(result["user_info"])
            }
            frontend_url = f"/?{urlencode(params)}"
            return RedirectResponse(url=frontend_url, status_code=302)

        # For API usage, return JSON
        return JSONResponse(content=result)

    except HTTPException as e:
        if redirect_to_frontend == "true":
            error_url = f"/?error={e.detail}"
            return RedirectResponse(url=error_url, status_code=302)
        return JSONResponse(
            status_code=e.status_code,
            content={"error": e.detail}
        )
    except Exception as e:
        logger.error(f"Unexpected error in OAuth callback: {e}")
        if redirect_to_frontend == "true":
            return RedirectResponse(url="/?error=internal_server_error", status_code=302)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"}
        )


def get_oauth_login_url(request: Request, redirect_uri: str = "") -> str:
    """
    Get OAuth login URL (for frontend to initiate login)
    Returns authorization URL directly without redirecting
    """
    if not is_oauth_configured():
        return ""

    # Build redirect URI based on request
    if not redirect_uri:
        # Use the current request's scheme + host + /auth/callback
        scheme = request.url.scheme
        host = request.url.netloc
        redirect_uri = f"{scheme}://{host}/auth/callback"

    # Generate authorization URL
    import asyncio
    auth_data = asyncio.run(get_google_authorization_url(redirect_uri=redirect_uri))

    return auth_data["authorization_url"]
