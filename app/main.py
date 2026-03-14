from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from logging import getLogger
import openai
from openai import OpenAI
from pathlib import Path
from typing import Annotated
import httpx
import json
import meilisearch
import os
import requests
import time
import uuid

# Import configuration module for automatic OAI_CONFIG_LIST generation
from app.config import get_realtime_config, get_swarm_config, validate_config
from app.memory import UserMemoryStore
from app.realtime_session import RealtimeSession

# Import security middleware
from app.middleware import (
    create_security_headers_middleware,
    configure_cors,
    get_websocket_security_middleware
)

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
from app.models.validators import LocationInput, SearchQuery, AcceptLanguage
from app.security.sanitizers import sanitize_url_parameter, sanitize_api_response
from pydantic import ValidationError

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
    return templates.TemplateResponse("chat.html", {"request": request, "port": port, "version": APP_VERSION})


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

    # Extract user identity from OAuth proxy headers
    forwarded_user = headers.get('x-forwarded-user', '')
    forwarded_email = headers.get('x-forwarded-email', '')
    forwarded_name = headers.get('x-forwarded-name', '')

    # If standard headers are missing, note which auth headers ARE present for debugging
    if not forwarded_user and not forwarded_email:
        present = [k for k in header_dict if k.lower().startswith('x-forward') or 'cookie' in k.lower()]
        logger.info(json.dumps({"event": "ws.auth_missing", "session_id": session_id,
                                "present_auth_headers": present, "ts": time.time()}))
    logger.info(json.dumps({"event": "ws.user_identity", "session_id": session_id,
                            "user": forwarded_user, "email": forwarded_email,
                            "name": forwarded_name, "ts": time.time()}))

    # Load per-user memory (keyed by email if available, else user id)
    user_identity = forwarded_email or forwarded_user
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
            facts = memory_store.get_facts()
            memory_section = f"\n\nThe current user is {user_display}"
            if forwarded_email and forwarded_email != user_display:
                memory_section += f" ({forwarded_email})"
            memory_section += "."
            if facts:
                memory_section += "\nHere are things you remember about this user:\n"
                memory_section += "\n".join(f"- {f}" for f in facts)
            memory_section += (
                "\nUse save_memory when you learn preferences or important information about the user. "
                "Use recall_memories to retrieve what you know about the user on demand."
            )
            system_message = base_system_message + memory_section
        else:
            system_message = base_system_message

        session = RealtimeSession(
            websocket=websocket,
            model=first_config["model"],
            api_key=first_config["api_key"],
            system_message=system_message,
            logger=logger,
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

    def get_current_weather(location: Annotated[str, "city"]) -> str:
        """
        Get current weather with validated and sanitized location input.
        Security: Prevents SQL injection, command injection, XSS, SSRF, URL injection
        """
        try:
            validated_location = LocationInput(location=location)
            safe_location = validated_location.location

            logger.info(f"<-- Calling get_current_weather function for {safe_location} -->")

            response = requests.get(
                "https://api.weatherapi.com/v1/current.json",
                params={
                    "key": os.getenv('WEATHER_API_KEY'),
                    "q": safe_location,
                    "aqi": "no"
                },
                timeout=10
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

    def get_weather_forecast(location: Annotated[str, "city"]) -> str:
        """
        Get weather forecast with validated and sanitized location input.
        Security: Prevents SQL injection, command injection, XSS, SSRF, URL injection
        """
        try:
            validated_location = LocationInput(location=location)
            safe_location = validated_location.location

            logger.info(f"<-- Calling get_weather_forecast function for {safe_location} -->")

            response = requests.get(
                "https://api.weatherapi.com/v1/forecast.json",
                params={
                    "key": os.getenv('WEATHER_API_KEY'),
                    "q": safe_location,
                    "days": "3",
                    "aqi": "no",
                    "alerts": "no"
                },
                timeout=10
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

    def web_search(query: Annotated[str, "search_query"]) -> str:
        """
        Search the web using OpenAI's Responses API with web_search tool.
        Returns current information with sourced citations.
        """
        try:
            validated_query = SearchQuery(query=query)
            safe_query = validated_query.query

            t_request = time.monotonic()
            logger.info(json.dumps({"event": "web_search.request_sent", "query": safe_query,
                                    "ts": time.time()}))

            response = openai_client.responses.create(
                model="gpt-4o-mini",
                tools=[{"type": "web_search_preview"}],
                input=safe_query
            )

            t_response = time.monotonic()

            if hasattr(response, 'output_text') and response.output_text:
                result_text = response.output_text
                if len(result_text) > 500:
                    result_text = result_text[:500] + "..."
                logger.info(json.dumps({"event": "web_search.response_received", "query": safe_query,
                                        "result_size": len(result_text),
                                        "duration_ms": round((t_response - t_request) * 1000, 1),
                                        "ts": time.time()}))
                return result_text
            else:
                logger.warning(json.dumps({"event": "web_search.empty_response", "query": safe_query,
                                           "duration_ms": round((t_response - t_request) * 1000, 1),
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

    if memory_store is not None:
        def save_memory(fact: str) -> str:
            """Save a fact about the current user to persistent memory."""
            fact = fact.strip()
            if not fact:
                return json.dumps({"status": "error", "message": "Fact cannot be empty."})
            memory_store.add_fact(fact)
            logger.info(f"Saved memory for user {user_identity!r}: {fact!r}")
            return json.dumps({"status": "ok", "message": "Memory saved."})

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
                    }
                },
                "required": ["fact"]
            }
        )

        def recall_memories() -> str:
            """Retrieve stored memories about the current user."""
            facts = memory_store.get_facts()
            if facts:
                return json.dumps({"facts": facts})
            return json.dumps({"facts": [], "message": "No memories stored for this user yet."})

        session.register_tool(
            name="recall_memories",
            description="Retrieve stored facts and preferences about the current user.",
            handler=recall_memories,
            parameters={
                "type": "object",
                "properties": {}
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
