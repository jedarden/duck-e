from autogen.agentchat.realtime_agent import RealtimeAgent
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from logging import getLogger
from openai import OpenAI
from pathlib import Path
from typing import Annotated
import httpx
import json
import meilisearch
import os
import requests

# Import configuration module for automatic OAI_CONFIG_LIST generation
from app.config import get_realtime_config, get_swarm_config, validate_config

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
    timeout=60.0,  # 60 second timeout for API calls
    max_retries=2  # Retry twice on failure
)

app = FastAPI()


@app.get("/status", response_class=JSONResponse)
async def index_page():
    return {"message": "WebRTC DUCK-E Server is running!"}


website_files_path = Path(__file__).parent / "website_files"

app.mount(
    "/static", StaticFiles(directory=website_files_path / "static"), name="static"
)

# Templates for HTML responses

templates = Jinja2Templates(directory=website_files_path / "templates")


@app.get("/", response_class=HTMLResponse)
async def start_chat(request: Request):
    """Endpoint to return the HTML page for audio chat."""
    port = request.url.port
    return templates.TemplateResponse("chat.html", {"request": request, "port": port})


@app.websocket("/session")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections providing audio stream and OpenAI."""
    await websocket.accept()

    # Retrieve and log incoming headers
    headers = websocket.headers
    logger = getLogger("uvicorn.error")
    logger.info(f"Incoming WebSocket headers: {headers}")
    logger.info(headers.get('x-forwarded-user'))

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

        logger.info(f"Initializing RealtimeAgent with config: {len(realtime_llm_config['config_list'])} model(s) configured")

        realtime_agent = RealtimeAgent(
            name="DUCK-E",
            system_message=f"You are an AI voice assistant named DUCK-E (pronounced ducky). You can answer questions about weather (make sure to localize units based on the location), or search the web for current information. \n\nUse the web_search_preview tool for recent news, current events, or information beyond your knowledge fall back to the web_search tool if needed. The tool will automatically acknowledge the request and provide search results. Keep responses brief, two short sentences maximum. If conducting a web search, explain what is being searched. The user's browser is configured for this language <language>{headers.get('accept-language')}</language>",
            llm_config=realtime_llm_config,
            websocket=websocket,
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

    @realtime_agent.register_realtime_function(  # type: ignore [misc]
        name="get_current_weather", description="Get the current weather in a given city."
    )
    def get_current_weather(location: Annotated[str, "city"]) -> str:
        url = f"https://api.weatherapi.com/v1/current.json?key={os.getenv('WEATHER_API_KEY')}&q={location}&aqi=no"
        response = requests.get(url)
        logger.info(f"<-- Calling get_current_weather function for {location} -->")
        return response.text

    @realtime_agent.register_realtime_function(  # type: ignore [misc]
        name="get_weather_forecast", description="Get the weather forecast in a given city."
    )
    def get_weather_forecast(location: Annotated[str, "city"]) -> str:
        url = f"https://api.weatherapi.com/v1/forecast.json?key={os.getenv('WEATHER_API_KEY')}&q={location}&days=3&aqi=no&alerts=no"
        response = requests.get(url)
        logger.info(f"<-- Calling get_weather_forecast function for {location} -->")
        return response.text

    @realtime_agent.register_realtime_function(  # type: ignore [misc]
        name="web_search",
        description="Search the web for current information, recent news, or specific topics using OpenAI's native web search tool. Returns comprehensive search results with sources."
    )
    def web_search(query: Annotated[str, "search_query"]) -> str:
        """
        Search the web using OpenAI's native web_search tool.
        This function leverages OpenAI's built-in web search capabilities for real-time information.
        """
        logger.info(f"<-- Executing native web search for query: {query} -->")

        try:
            # Use OpenAI's chat completions with native web_search tool
            # This is the updated approach for gpt-realtime models with native tool support
            response = openai_client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a web search assistant. Provide concise, accurate information from web searches."
                    },
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "Search the web for current information",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "The search query"
                                    }
                                },
                                "required": ["query"]
                            }
                        }
                    }
                ],
                tool_choice="auto",
                temperature=1.0,
                user="duck-e-web-search"
            )

            # Extract the response content
            if response.choices and len(response.choices) > 0:
                message = response.choices[0].message

                # Check if tool was called
                if hasattr(message, 'tool_calls') and message.tool_calls:
                    logger.info(f"Web search tool called: {len(message.tool_calls)} call(s)")
                    # Return the assistant's response after tool use
                    return message.content if message.content else "Search completed. Please ask me about the results."

                # Regular response without tool call
                if message.content:
                    logger.info(f"Web search result length: {len(message.content)} characters")
                    return message.content
                else:
                    logger.warning("No content in web search response")
                    return "I couldn't retrieve web search results. Please try rephrasing your question."
            else:
                logger.warning("Empty response from web search")
                return "No search results found. Please try a different query."

        except Exception as e:
            logger.error(f"Web search error: {str(e)}", exc_info=True)
            return "I'm having trouble searching the web right now. I can help with general questions or information about our business clients."

    try:
        await realtime_agent.run()
    except Exception as e:
        error_msg = f"RealtimeAgent runtime error: {str(e)}"
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
