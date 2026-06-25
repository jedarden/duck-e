from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from logging import getLogger
import openai
from openai import OpenAI
from pathlib import Path
from typing import Annotated
import asyncio
import httpx
import json
import os
import requests
import socket
import time
import uuid

# Import configuration module for automatic OAI_CONFIG_LIST generation
from app.config import get_realtime_config, get_swarm_config, validate_config
from app.memory import UserMemoryStore, FactCategory, FactSource
from app.realtime_session import RealtimeSession

# Import security middleware
from app.middleware import (
    create_security_headers_middleware,
    configure_cors,
    get_websocket_security_middleware
)

# Import Google OAuth (optional)
try:
    from app.middleware.google_oauth import (
        is_oauth_configured,
        get_oauth_login_url,
        initiate_login,
        handle_callback,
        get_user_info_from_token,
        cleanup_expired_states,
        cleanup_expired_sessions
    )
    _oauth_available = True
except ImportError:
    _oauth_available = False
    logger = getLogger("uvicorn.error")
    logger.warning("Google OAuth module not available - authentication will rely on proxy headers")

# Import cost protection middleware
from app.middleware.cost_protection import (
    CostProtectionMiddleware,
    get_cost_tracker
)

# Import rate limiting middleware
from app.middleware.rate_limiting import (
    limiter,
    get_rate_limit_config,
    custom_rate_limit_exceeded_handler
)
from slowapi.errors import RateLimitExceeded

# Import input validators and sanitizers
from app.models.validators import LocationInput, SearchQuery, AcceptLanguage, FetchUrl
from app.security.sanitizers import sanitize_url_parameter, sanitize_api_response
from pydantic import ValidationError
from bs4 import BeautifulSoup
from ipaddress import ip_address, IPv4Address, IPv6Address, AddressValueError

# Load and validate realtime configuration using auto-generated config
logger = getLogger("uvicorn.error")

try:
    realtime_config_list = get_realtime_config()

    if not realtime_config_list:
        logger.warning(
            "WARNING: No realtime models found in auto-generated configuration. "
            "WebSocket connections will fail. Please ensure OPENAI_API_KEY is set."
        )
except Exception as e:
    logger.error(f"Failed to load realtime configuration: {e}")
    realtime_config_list = []

# Create custom httpx client with longer timeout for OpenAI Realtime API connections
# The default timeout is too short for establishing TLS connections to OpenAI's API
httpx_client = httpx.AsyncClient(
    timeout=httpx.Timeout(
        timeout=120.0,  # 120 second total timeout
        connect=60.0,   # 60 seconds for connection establishment (including TLS handshake)
        read=60.0,      # 60 seconds for reading responses
        write=30.0,     # 30 seconds for writing requests
        pool=10.0       # 10 seconds for acquiring connection from pool
    ),
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=20
    )
)

realtime_llm_config = {
    "timeout": 300,  # 5 minute timeout for overall operation
    "config_list": realtime_config_list,
    "temperature": 1.0,
    "parallel_tool_calls": True,  # Enable parallel tool execution
    "tool_choice": "auto",  # Allow model to decide when to use tools
    "tools": [{ "type": "web_search_preview" }],
    "http_client": httpx_client  # Use custom httpx client with longer timeouts
}

# Load and validate swarm configuration using auto-generated config
try:
    swarm_config_list = get_swarm_config()

    if not swarm_config_list:
        logger.warning(
            "WARNING: No GPT-5 models found in auto-generated configuration. "
            "Some features may not work correctly."
        )
except Exception as e:
    logger.error(f"Failed to load swarm configuration: {e}")
    swarm_config_list = []

swarm_llm_config = {
    "temperature": 1,
    "config_list": swarm_config_list,
    "timeout": 120,
    "tools": [],
}

openai_client = OpenAI(
    api_key=os.getenv('OPENAI_API_KEY'),
    timeout=120.0,  # 120 second timeout for API calls (web searches can be slow)
    max_retries=3  # Retry 3 times on transient failures
)

# Initialize FastAPI application
app = FastAPI()

# Add rate limiter state to app
# This is required by slowapi
app.state.limiter = limiter

# Add rate limit exceeded exception handler
app.add_exception_handler(RateLimitExceeded, custom_rate_limit_exceeded_handler)

# Configure CORS for public access
# Reads from ALLOWED_ORIGINS environment variable
configure_cors(app)

# Add security headers middleware
# Reads from ENABLE_HSTS, CSP_REPORT_URI environment variables
security_middleware = create_security_headers_middleware()
app.add_middleware(security_middleware)

# Add cost protection middleware
# Reads from COST_PROTECTION_* environment variables
app.add_middleware(CostProtectionMiddleware)

# Initialize WebSocket security validator
ws_security = get_websocket_security_middleware()

# Initialize cost tracker for WebSocket sessions
cost_tracker = get_cost_tracker()

# Load rate limit configuration
rate_limit_config = get_rate_limit_config()


def get_app_version() -> str:
    """Read version from VERSION file"""
    version_paths = [
        Path("/app/VERSION"),  # Docker container path
        Path(__file__).parent.parent / "VERSION",  # Local development
    ]
    for version_path in version_paths:
        if version_path.exists():
            return version_path.read_text().strip()
    return "unknown"

APP_VERSION = get_app_version()
logger.info(f"DUCK-E version: {APP_VERSION}")

@app.get("/status")
async def index_page(request: Request):
    """
    Health check endpoint (no rate limiting for monitoring)
    """
    return JSONResponse({"message": "WebRTC DUCK-E Server is running!", "version": APP_VERSION})


@app.get("/health/openai")
async def health_openai():
    """Test OpenAI realtime session creation. No auth — VPN entrypoint only."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("REALTIME_MODEL", "gpt-realtime-2")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"session": {"type": "realtime", "model": model}},
            )
        if resp.is_success:
            return JSONResponse({"status": "ok", "model": model, "version": APP_VERSION})
        return JSONResponse({"status": "error", "http_status": resp.status_code, "detail": resp.json()})
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)})


website_files_path = Path(__file__).parent / "website_files"

app.mount(
    "/static", StaticFiles(directory=website_files_path / "static"), name="static"
)

# Templates for HTML responses

templates = Jinja2Templates(directory=website_files_path / "templates")


@app.get("/", response_class=HTMLResponse)
@limiter.limit(rate_limit_config.main_page_limit)
async def start_chat(request: Request):
    """
    Main page endpoint with rate limiting
    Rate limit: 30 requests per minute per IP
    """
    port = request.url.port
    realtime_configs = get_realtime_config()
    realtime_model = realtime_configs[0]["model"] if realtime_configs else "unknown"

    # Check if OAuth is configured
    oauth_configured = is_oauth_configured() if _oauth_available else False

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "port": port,
        "version": APP_VERSION,
        "model": realtime_model,
        "oauth_configured": oauth_configured
    })


# Google OAuth Endpoints (if available)
if _oauth_available:
    @app.get("/auth/login")
    async def auth_login(request: Request, redirect_uri: str = ""):
        """
        Initiate Google OAuth login flow
        Redirects to Google authorization page
        """
        if not is_oauth_configured():
            return JSONResponse(
                status_code=500,
                content={"error": "Google OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."}
            )

        return await initiate_login(request, redirect_uri=redirect_uri)


    @app.get("/auth/callback")
    async def auth_callback(request: Request):
        """
        Handle Google OAuth callback
        Returns JSON with access token and user info
        """
        return await handle_callback(request)


    @app.get("/auth/config")
    async def auth_config(request: Request):
        """
        Check if OAuth is configured and get login URL
        Returns configuration status and login URL for frontend
        """
        if not is_oauth_configured():
            return JSONResponse(content={
                "configured": False,
                "login_url": None,
                "message": "Google OAuth is not configured. Please set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables."
            })

        # Generate login URL
        login_url = get_oauth_login_url(request)

        return JSONResponse(content={
            "configured": True,
            "login_url": login_url,
            "message": "Google OAuth is configured"
        })


    @app.get("/auth/me")
    async def auth_me(request: Request):
        """
        Validate JWT token and return user information
        Returns user info from valid JWT token or 401 if invalid
        """
        # Extract Authorization header
        auth_header = request.headers.get('authorization', '')

        if not auth_header or not auth_header.startswith('Bearer '):
            return JSONResponse(
                status_code=401,
                content={"error": "No authorization token provided"}
            )

        token = auth_header[7:]  # Remove 'Bearer ' prefix

        # Validate token and extract user info
        user_info = get_user_info_from_token(token)

        if not user_info:
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid or expired token"}
            )

        return JSONResponse(content={
            "authenticated": True,
            "user_info": user_info
        })


    @app.on_event("startup")
    async def startup_cleanup_tasks():
        """Start periodic cleanup of expired OAuth states and sessions"""
        if not _oauth_available:
            return

        async def cleanup_task():
            """Periodic cleanup task"""
            import asyncio
            while True:
                try:
                    cleanup_expired_states()
                    cleanup_expired_sessions()
                    await asyncio.sleep(300)  # Run every 5 minutes
                except Exception as e:
                    logger.error(f"Error in cleanup task: {e}")
                    await asyncio.sleep(60)  # Retry after 1 minute on error

        # Start cleanup task in background
        import asyncio
        asyncio.create_task(cleanup_task())


@app.websocket("/session")
async def handle_media_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time audio streaming.

    Note: Rate limiting via slowapi is not supported for WebSocket endpoints.
    Connection limits are enforced via the CostProtectionMiddleware instead.

    Handles real-time audio streaming with OpenAI's Realtime API
    """
    # Validate WebSocket origin before accepting connection
    if not await ws_security.validate_connection(websocket):
        # Connection rejected by security middleware
        return

    # Generate unique session ID for cost tracking
    session_id = str(uuid.uuid4())

    # Retrieve and log incoming headers
    headers = websocket.headers
    logger = getLogger("uvicorn.error")
    logger.info(f"Session ID for cost tracking: {session_id}")

    # Log all headers for auth proxy debugging
    header_dict = dict(headers)
    auth_relevant = {k: v for k, v in header_dict.items()
                     if any(k.lower().startswith(p) for p in ('x-forward', 'x-real', 'cookie', 'authorization'))}
    logger.info(json.dumps({"event": "ws.connect", "session_id": session_id,
                            "auth_headers": auth_relevant, "ts": time.time()}))

    # Extract user identity from OAuth proxy headers or JWT token
    forwarded_user = headers.get('x-forwarded-user', '')
    forwarded_email = headers.get('x-forwarded-email', '')
    forwarded_name = headers.get('x-forwarded-name', '')

    # Also check for JWT token in Authorization header or query parameter
    # Browser WebSockets can't send custom headers, so we fall back to query params
    auth_header = headers.get('authorization', '')
    jwt_user_info = None
    jwt_token = None

    # Try Authorization header first (for reverse proxy auth)
    if auth_header and auth_header.startswith('Bearer '):
        jwt_token = auth_header[7:]  # Remove 'Bearer ' prefix

    # Fallback: check query parameter for browser WebSocket connections
    if not jwt_token:
        try:
            from urllib.parse import urlparse, parse_qs
            ws_url = str(websocket.url)
            if 'token=' in ws_url:
                query_params = parse_qs(urlparse(ws_url).query)
                jwt_token = query_params.get('token', [None])[0]
        except Exception as e:
            logger.warning(f"Failed to parse token from query params: {e}")

    # Validate JWT token if present
    if jwt_token:
        try:
            jwt_user_info = get_user_info_from_token(jwt_token) if _oauth_available else None
            if jwt_user_info:
                logger.info(json.dumps({"event": "ws.jwt_auth", "session_id": session_id,
                                       "user_email": jwt_user_info.get("email"),
                                       "user_name": jwt_user_info.get("name"),
                                       "ts": time.time()}))
        except Exception as e:
            logger.warning(f"Failed to extract user info from JWT: {e}")

    # If standard headers are missing, note which auth headers ARE present for debugging
    if not forwarded_user and not forwarded_email and not jwt_user_info:
        present = [k for k in header_dict if k.lower().startswith('x-forward') or 'cookie' in k.lower() or 'authorization' in k.lower()]
        logger.info(json.dumps({"event": "ws.auth_missing", "session_id": session_id,
                                "present_auth_headers": present, "ts": time.time()}))

    # Determine user identity - JWT token takes priority, then proxy headers
    user_identity = None
    user_display_name = None

    if jwt_user_info and jwt_user_info.get("email"):
        user_identity = jwt_user_info["email"]
        user_display_name = jwt_user_info.get("name", jwt_user_info["email"])
    elif forwarded_email:
        user_identity = forwarded_email
        user_display_name = forwarded_name or forwarded_email
    elif forwarded_user:
        user_identity = forwarded_user
        user_display_name = forwarded_user

    logger.info(json.dumps({"event": "ws.user_identity", "session_id": session_id,
                            "user_identity": user_identity, "user_display": user_display_name,
                            "ts": time.time()}))

    # Load per-user memory (keyed by email if available, else user id)
    memory_store: UserMemoryStore | None = None
    if user_identity:
        memory_store = UserMemoryStore(user_identity)
        memory_store.load()

    # Initialize cost tracking for this session
    await cost_tracker.start_session(session_id)
    logger.info(f"Cost tracking started for session: {session_id}")

    # Check circuit breaker before allowing connection
    await cost_tracker.check_circuit_breaker()
    if cost_tracker.circuit_breaker_active:
        logger.warning(f"Circuit breaker active - rejecting session: {session_id}")
        try:
            await websocket.send_json({
                "type": "service_unavailable",
                "error": "System is currently under high load due to cost limits",
                "message": "Please try again later",
                "circuit_breaker_active": True,
                "reset_time": cost_tracker.circuit_breaker_reset_time.isoformat() if cost_tracker.circuit_breaker_reset_time else None
            })
        except Exception:
            pass
        try:
            await websocket.close(code=1013, reason="Service temporarily unavailable")
        except Exception:
            pass
        await cost_tracker.end_session(session_id)
        return

    # Validate configuration before initializing RealtimeAgent
    try:
        # Check if config_list exists and is not empty
        if not realtime_llm_config.get("config_list"):
            error_msg = "Configuration error: No realtime models found in OAI_CONFIG_LIST. Please ensure you have entries tagged with 'gpt-realtime'."
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "error": error_msg
            })
            await websocket.close(code=1008, reason="Missing realtime model configuration")
            return

        # Validate that config_list has at least one entry
        if len(realtime_llm_config["config_list"]) == 0:
            error_msg = "Configuration error: config_list is empty. Check OAI_CONFIG_LIST file for 'gpt-realtime' tagged entries."
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "error": error_msg
            })
            await websocket.close(code=1008, reason="Empty configuration list")
            return

        # Validate API key is present in first config entry
        first_config = realtime_llm_config["config_list"][0]
        if not first_config.get("api_key"):
            error_msg = "Configuration error: API key missing from realtime model configuration."
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "error": error_msg
            })
            await websocket.close(code=1008, reason="Missing API key")
            return

        logger.info(f"Initializing RealtimeSession with config: {len(realtime_llm_config['config_list'])} model(s) configured")

        # Validate accept-language header
        accept_language_raw = headers.get('accept-language', 'en-US')
        try:
            validated_language = AcceptLanguage(language=accept_language_raw)
            safe_language = validated_language.language
        except ValidationError as e:
            logger.warning(f"Invalid accept-language header: {accept_language_raw}, error: {e}")
            safe_language = "en-US"  # Fallback to safe default

        first_config = realtime_llm_config["config_list"][0]

        # Build system message, optionally augmented with user identity and memories
        base_system_message = (
            f"You are an AI voice assistant named DUCK-E (pronounced ducky). "
            f"You can answer questions about weather (make sure to localize units based on the location), "
            f"or search the web for current information. \n\n"
            f"IMPORTANT: Before calling web_search, you MUST first speak to the user saying something like "
            f"'Let me search for that' or 'Searching the web for [topic]'. Only after announcing the search "
            f"should you call the web_search function. Keep responses brief, two short sentences maximum. "
            f"The user's browser is configured for this language <language>{safe_language}</language>"
        )

        if memory_store is not None:
            user_display = forwarded_name or forwarded_email or forwarded_user
            summary = await memory_store.get_or_generate_summary(first_config["api_key"])
            memory_section = f"\n\nThe current user is {user_display}"
            if forwarded_email and forwarded_email != user_display:
                memory_section += f" ({forwarded_email})"
            memory_section += "."
            if summary:
                memory_section += f"\n{summary}"
            else:
                # Fallback: list facts directly if summary unavailable
                structured_facts = memory_store.get_structured_facts()
                if structured_facts:
                    memory_section += "\nHere are things you remember about this user:\n"
                    memory_section += "\n".join(
                        f"- [{f.category.value}] {f.text}" for f in structured_facts
                    )
            memory_section += (
                "\nUse save_memory when you learn preferences or important information about the user. "
                "Use recall_memories to retrieve what you know about the user on demand; "
                "pass a topic to filter for relevant facts only."
            )
            system_message = base_system_message + memory_section
        else:
            system_message = base_system_message

        _extraction_api_key = first_config["api_key"]

        async def _on_turn_done(user_text: str, assistant_text: str) -> None:
            if memory_store is not None:
                await memory_store.extract_and_save(
                    user_text, assistant_text, _extraction_api_key,
                    cost_tracker=cost_tracker, session_id=session_id,
                    on_backend_cost=session.send_backend_cost,
                )

        session = RealtimeSession(
            websocket=websocket,
            model=first_config["model"],
            api_key=first_config["api_key"],
            system_message=system_message,
            logger=logger,
            on_turn_done=_on_turn_done if memory_store is not None else None,
        )
    except IndexError as e:
        error_msg = f"Configuration error: Failed to access config_list - {str(e)}"
        logger.error(error_msg, exc_info=True)
        await websocket.send_json({
            "type": "error",
            "error": error_msg
        })
        await websocket.close(code=1008, reason="Configuration access error")
        return
    except Exception as e:
        error_msg = f"Failed to initialize RealtimeAgent: {str(e)}"
        logger.error(error_msg, exc_info=True)
        await websocket.send_json({
            "type": "error",
            "error": error_msg
        })
        await websocket.close(code=1011, reason="Agent initialization failed")
        return

    def _geocode_location(safe_location: str):
        """Geocode a city name to lat/lon using Open-Meteo geocoding API."""
        geo_response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": safe_location, "count": 1, "language": "en", "format": "json"},
            timeout=10
        )
        geo_response.raise_for_status()
        geo_data = geo_response.json()
        results = geo_data.get("results")
        if not results:
            return None, None, None
        result = results[0]
        return result["latitude"], result["longitude"], result.get("timezone", "auto")

    async def get_current_weather(location: Annotated[str, "city"]) -> str:
        """
        Get current weather with validated and sanitized location input.
        Security: Prevents SQL injection, command injection, XSS, SSRF, URL injection
        Runs blocking HTTP requests in a thread to avoid blocking the event loop.
        """
        try:
            validated_location = LocationInput(location=location)
            safe_location = validated_location.location

            logger.info(f"<-- Calling get_current_weather function for {safe_location} -->")

            lat, lon, timezone = await asyncio.to_thread(_geocode_location, safe_location)
            if lat is None:
                return json.dumps({"error": f"Location not found: {safe_location}"})

            response = await asyncio.to_thread(
                requests.get,
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                    "timezone": timezone or "auto",
                },
                timeout=10,
            )

            if response.status_code == 200:
                response_data = response.json()
                sanitized_response = sanitize_api_response(response_data)
                return json.dumps(sanitized_response)
            else:
                logger.error(f"Weather API error: {response.status_code} - {response.text}")
                return json.dumps({"error": "Unable to fetch weather data"})

        except ValidationError as e:
            logger.error(f"Location validation failed for '{location}': {e}")
            return json.dumps({"error": "Invalid location format. Please provide a valid city name."})
        except Exception as e:
            logger.error(f"Weather API error for '{location}': {e}")
            return json.dumps({"error": "Unable to fetch weather data"})

    session.register_tool(
        name="get_current_weather",
        description="Get the current weather in a given city.",
        handler=get_current_weather,
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "city"}
            },
            "required": ["location"]
        }
    )

    async def get_weather_forecast(location: Annotated[str, "city"]) -> str:
        """
        Get weather forecast with validated and sanitized location input.
        Security: Prevents SQL injection, command injection, XSS, SSRF, URL injection
        Runs blocking HTTP requests in a thread to avoid blocking the event loop.
        """
        try:
            validated_location = LocationInput(location=location)
            safe_location = validated_location.location

            logger.info(f"<-- Calling get_weather_forecast function for {safe_location} -->")

            lat, lon, timezone = await asyncio.to_thread(_geocode_location, safe_location)
            if lat is None:
                return json.dumps({"error": f"Location not found: {safe_location}"})

            response = await asyncio.to_thread(
                requests.get,
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                    "timezone": timezone or "auto",
                    "forecast_days": 3,
                },
                timeout=10,
            )

            if response.status_code == 200:
                response_data = response.json()
                sanitized_response = sanitize_api_response(response_data)
                return json.dumps(sanitized_response)
            else:
                logger.error(f"Weather API error: {response.status_code} - {response.text}")
                return json.dumps({"error": "Unable to fetch weather forecast"})

        except ValidationError as e:
            logger.error(f"Location validation failed for '{location}': {e}")
            return json.dumps({"error": "Invalid location format. Please provide a valid city name."})
        except Exception as e:
            logger.error(f"Weather API error for '{location}': {e}")
            return json.dumps({"error": "Unable to fetch weather forecast"})

    session.register_tool(
        name="get_weather_forecast",
        description="Get the weather forecast in a given city.",
        handler=get_weather_forecast,
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "city"}
            },
            "required": ["location"]
        }
    )

    async def web_search(query: Annotated[str, "search_query"]) -> str:
        """
        Search the web using OpenAI's Responses API with web_search tool.
        Returns current information with sourced citations.
        Runs the blocking SDK call in a thread to avoid blocking the event loop.
        """
        try:
            validated_query = SearchQuery(query=query)
            safe_query = validated_query.query

            t_request = time.monotonic()
            logger.info(json.dumps({"event": "web_search.request_sent", "query": safe_query,
                                    "ts": time.time()}))

            response = await asyncio.to_thread(
                openai_client.responses.create,
                model="gpt-5.4-nano",
                tools=[{"type": "web_search_preview"}],
                input=safe_query,
            )

            t_response = time.monotonic()
            duration_ms = round((t_response - t_request) * 1000, 1)

            # Track token usage for cost accounting
            if hasattr(response, 'usage') and response.usage:
                try:
                    await cost_tracker.track_usage(
                        session_id=session_id,
                        model="gpt-5.4-nano",
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                    )
                except Exception:
                    pass  # Never crash the session over cost tracking
                try:
                    await session.send_backend_cost(
                        "gpt-5.4-nano",
                        response.usage.input_tokens,
                        response.usage.output_tokens,
                    )
                except Exception:
                    pass

            if hasattr(response, 'output_text') and response.output_text:
                result_text = response.output_text
                full_size = len(result_text)
                if full_size > 2000:
                    result_text = result_text[:2000] + "..."
                logger.info(json.dumps({"event": "web_search.response_received", "query": safe_query,
                                        "full_size": full_size,
                                        "result_size": len(result_text),
                                        "duration_ms": duration_ms,
                                        "ts": time.time()}))
                return result_text
            else:
                logger.warning(json.dumps({"event": "web_search.empty_response", "query": safe_query,
                                           "duration_ms": duration_ms,
                                           "ts": time.time()}))
                return "I couldn't find relevant information. Please try a different query."

        except ValidationError as e:
            logger.error(f"Search query validation failed for '{query}': {e}")
            return "Invalid search query. Please rephrase your question."
        except Exception as e:
            logger.error(f"Web search error: {str(e)}", exc_info=True)
            return "I'm having trouble searching the web right now. Please try again."

    session.register_tool(
        name="web_search",
        description="Search the web for current information, recent news, or specific topics using OpenAI's web search. Returns up-to-date results with citations.",
        handler=web_search,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search_query"}
            },
            "required": ["query"]
        }
    )

    def _is_private_ip(ip_str: str) -> bool:
        """Check if an IP address is private/internal (SSRF protection)."""
        try:
            addr = ip_address(ip_str)
            if isinstance(addr, IPv4Address):
                return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
            elif isinstance(addr, IPv6Address):
                return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
        except (ValueError, AddressValueError):
            pass
        return False

    async def web_fetch(url: Annotated[str, "url"]) -> str:
        """
        Fetch content from a URL with SSRF protection.
        Extracts visible text from HTML, returns raw text for text/plain and application/json.
        Truncates output to 3000 characters.

        SSRF Protection: Validates IP addresses on EVERY redirect hop, not just the initial URL.
        """
        try:
            # Validate and sanitize URL
            validated_url = FetchUrl(url=url)
            safe_url = validated_url.url

            t_request = time.monotonic()
            logger.info(json.dumps({"event": "web_fetch.request_sent", "url": safe_url,
                                    "ts": time.time()}))

            # Browser-like User-Agent
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }

            # Parse hostname for initial SSRF check
            from urllib.parse import urlparse
            parsed = urlparse(safe_url)
            initial_hostname = parsed.hostname

            async def validate_redirect_hop(response: httpx.Response) -> None:
                """
                Event hook to validate each redirect hop before following.
                Raises httpx.HTTPStatusError if redirect target resolves to a private IP.
                Called by httpx for each redirect response (3xx status codes).
                """
                if response.is_redirect and response.next_request:
                    redirect_url = str(response.next_request.url)
                    redirect_hostname = urlparse(redirect_url).hostname

                    if redirect_hostname:
                        try:
                            loop = asyncio.get_event_loop()
                            redirect_ip = await loop.run_in_executor(
                                None,
                                lambda: socket.gethostbyname(redirect_hostname)
                            )
                            if _is_private_ip(redirect_ip):
                                logger.warning(json.dumps({
                                    "event": "web_fetch.redirect_ssrf_blocked",
                                    "original_url": safe_url,
                                    "redirect_url": redirect_url,
                                    "hostname": redirect_hostname,
                                    "ip": redirect_ip,
                                    "ts": time.time()
                                }))
                                raise httpx.HTTPStatusError(
                                    f"Redirect to private IP blocked: {redirect_hostname} -> {redirect_ip}",
                                    request=response.request,
                                    response=response
                                )
                        except socket.gaierror as e:
                            logger.warning(f"DNS resolution failed for redirect hostname: {redirect_hostname}")
                            raise httpx.HTTPStatusError(
                                f"Failed to resolve redirect hostname: {redirect_hostname}",
                                request=response.request,
                                response=response
                            )

            if initial_hostname:
                # Resolve initial hostname to IP and check if private
                try:
                    loop = asyncio.get_event_loop()
                    initial_ip = await loop.run_in_executor(None, lambda: socket.gethostbyname(initial_hostname))
                    if _is_private_ip(initial_ip):
                        logger.warning(json.dumps({"event": "web_fetch.ssrf_blocked",
                                                    "url": safe_url, "hostname": initial_hostname,
                                                    "ip": initial_ip, "ts": time.time()}))
                        return json.dumps({"error": "Cannot fetch from internal/private addresses"})
                except socket.gaierror:
                    logger.warning(f"DNS resolution failed for hostname: {initial_hostname}")
                    return json.dumps({"error": "Failed to resolve hostname"})

            # Event hooks for redirect validation
            event_hooks = {"response": [validate_redirect_hop]}
            async with httpx.AsyncClient(
                timeout=15.0,
                follow_redirects=True,
                max_redirects=5,
                event_hooks=event_hooks
            ) as client:
                response = await client.get(safe_url, headers=headers)

            t_response = time.monotonic()
            duration_ms = round((t_response - t_request) * 1000, 1)

            content_type = response.headers.get("content-type", "")
            response_size = len(response.content)

            # Log fetch metrics
            logger.info(json.dumps({"event": "web_fetch.response_received",
                                    "url": safe_url,
                                    "status": response.status_code,
                                    "content_type": content_type,
                                    "response_size": response_size,
                                    "duration_ms": duration_ms,
                                    "ts": time.time()}))

            if response.status_code != 200:
                logger.warning(f"web_fetch returned status {response.status_code} for {safe_url}")
                return json.dumps({"error": f"HTTP {response.status_code}", "url": safe_url})

            # Handle different content types
            if "text/html" in content_type:
                # Parse HTML and extract visible text
                soup = BeautifulSoup(response.text, "html.parser")

                # Remove script, style, nav, footer, header, aside elements
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()

                # Get text
                text = soup.get_text(separator=" ", strip=True)

                # Truncate to 3000 chars
                if len(text) > 3000:
                    text = text[:3000] + "..."

                return json.dumps({
                    "url": safe_url,
                    "content_type": "text/html",
                    "text": text,
                    "truncated": len(soup.get_text(separator=" ", strip=True)) > 3000
                })

            elif "text/plain" in content_type:
                text = response.text
                if len(text) > 3000:
                    text = text[:3000] + "..."

                return json.dumps({
                    "url": safe_url,
                    "content_type": "text/plain",
                    "text": text,
                    "truncated": len(response.text) > 3000
                })

            elif "application/json" in content_type:
                text = response.text
                if len(text) > 3000:
                    text = text[:3000] + "..."

                return json.dumps({
                    "url": safe_url,
                    "content_type": "application/json",
                    "text": text,
                    "truncated": len(response.text) > 3000
                })

            else:
                # Unknown content type - return first 3000 chars as-is
                text = response.text
                if len(text) > 3000:
                    text = text[:3000] + "..."

                return json.dumps({
                    "url": safe_url,
                    "content_type": content_type,
                    "text": text,
                    "truncated": len(response.text) > 3000
                })

        except ValidationError as e:
            logger.error(f"URL validation failed for '{url}': {e}")
            return json.dumps({"error": "Invalid URL format"})
        except httpx.TimeoutException:
            logger.error(f"web_fetch timeout for {url}")
            return json.dumps({"error": "Request timed out after 15 seconds"})
        except httpx.HTTPError as e:
            logger.error(f"web_fetch HTTP error for {url}: {e}")
            return json.dumps({"error": f"HTTP error: {str(e)}"})
        except Exception as e:
            logger.error(f"web_fetch error: {str(e)}", exc_info=True)
            return json.dumps({"error": "Failed to fetch URL"})

    session.register_tool(
        name="web_fetch",
        description="Fetch and read the content of a web page at a given URL. Returns the extracted text content. Use this when the user provides a specific URL they want you to read or summarize.",
        handler=web_fetch,
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"}
            },
            "required": ["url"]
        }
    )

    if memory_store is not None:
        async def save_memory(
            fact: str,
            category: str = "context",
            confidence: float = 1.0,
        ) -> str:
            """Save a fact about the current user to persistent memory."""
            fact = fact.strip()
            if not fact:
                return json.dumps({"status": "error", "message": "Fact cannot be empty."})
            try:
                cat = FactCategory(category.lower())
            except ValueError:
                cat = FactCategory.CONTEXT
            confidence = min(1.0, max(0.0, confidence))
            added = await memory_store.add_fact_with_semantic_dedup(
                text=fact,
                api_key=_extraction_api_key,
                category=cat,
                confidence=confidence,
                source=FactSource.EXPLICIT,
            )
            if added:
                logger.info(f"Saved memory for user {user_identity!r}: {fact!r} (category={cat.value}, confidence={confidence})")
                return json.dumps({"status": "ok", "message": "Memory saved.", "category": cat.value, "confidence": confidence})
            else:
                logger.info(f"Skipped duplicate memory for user {user_identity!r}: {fact!r}")
                return json.dumps({"status": "ok", "message": "Already known.", "category": cat.value, "confidence": confidence})

        session.register_tool(
            name="save_memory",
            description="Save a fact or preference about the current user for future sessions.",
            handler=save_memory,
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "The fact or preference to remember about the user."
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "personal", "correction", "context"],
                        "description": "Category of the fact. Default: context"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "Confidence level 0.0-1.0. Default: 1.0 for explicit saves"
                    }
                },
                "required": ["fact"]
            }
        )

        async def recall_memories(topic: str = "") -> str:
            """Retrieve stored memories about the current user, optionally filtered by topic."""
            if topic and topic.strip():
                facts = await memory_store.get_facts_by_topic_async(topic.strip(), _extraction_api_key)
                if not facts:
                    facts = memory_store.get_facts()
            else:
                facts = memory_store.get_facts()
            if facts:
                return json.dumps({"facts": facts, "topic": topic or None})
            return json.dumps({"facts": [], "message": "No memories stored for this user yet."})

        session.register_tool(
            name="recall_memories",
            description="Retrieve stored facts and preferences about the current user. Provide a topic to filter for relevant facts only.",
            handler=recall_memories,
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Optional topic to filter memories (e.g. 'food preferences', 'location'). Returns all memories if omitted."
                    }
                }
            }
        )

    async def handle_voice_change(voice: str) -> str:
        return await session.change_voice(voice)

    session.register_tool(
        name="change_voice",
        description="Change the assistant's voice. Available voices: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse",
        handler=handle_voice_change,
        parameters={
            "type": "object",
            "properties": {
                "voice": {
                    "type": "string",
                    "enum": [
                        "alloy", "ash", "ballad", "coral", "echo",
                        "fable", "nova", "onyx", "sage", "shimmer", "verse"
                    ],
                    "description": "The voice to switch to"
                }
            },
            "required": ["voice"]
        }
    )

    try:
        await session.run()
    except Exception as e:
        error_msg = f"RealtimeSession runtime error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        try:
            await websocket.send_json({
                "type": "error",
                "error": "Connection to AI service failed. Please check your API key and network connection."
            })
        except:
            pass  # Websocket may already be closed
        try:
            await websocket.close(code=1011, reason="Runtime error")
        except:
            pass  # Websocket may already be closed
