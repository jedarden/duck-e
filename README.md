# 🦆 DUCK-E: The Duck That Talks Back

## What is Rubber Ducking?

**Rubber duck debugging** is a time-honored programming technique where developers explain their code, line-by-line, to an inanimate rubber duck. The simple act of verbalizing the problem often leads to discovering the solution yourself.

The concept comes from *The Pragmatic Programmer* by Andrew Hunt and David Thomas:

> "A very simple but particularly useful technique for finding the cause of a problem is simply to explain it to someone else... They do not need to say a word; the simple act of explaining, step by step, what the code is supposed to do often causes the problem to leap off the screen and announce itself."

Traditional rubber ducking is a one-way conversation - you talk, the duck listens silently.

## The DUCK-E Revolution: When the Duck Talks Back

**DUCK-E** (pronounced "ducky") flips this paradigm on its head. Instead of a silent listener, DUCK-E is an **AI-powered voice assistant** that actively engages in your debugging process. It's rubber ducking 2.0 - a conversation, not a monologue.

### Why "The Duck That Talks Back" Changes Everything

Traditional rubber ducking relies on self-reflection. DUCK-E enhances this by:

- 🗣️ **Active Engagement**: Ask questions and get intelligent responses in real-time
- 🧠 **Contextual Understanding**: DUCK-E comprehends your code and architecture
- 🔍 **Intelligent Assistance**: Offers suggestions, explains concepts, and helps debug
- 🌐 **Real-time Information**: Can search the web for current documentation and solutions
- 🌤️ **Breaks the Monotony**: Even checks the weather while you're debugging

## Features

### Voice-First Interface
- **WebRTC Real-time Communication**: Low-latency voice interaction using OpenAI's Realtime API
- **Natural Conversation**: Speak naturally, just like you would to a colleague
- **Interrupt-friendly**: DUCK-E can handle natural conversation flow

### Intelligent Capabilities
- **Code Understanding**: Discuss algorithms, architecture, and implementation details
- **Web Search Integration**: Search for documentation, Stack Overflow answers, and current solutions
- **Weather Information**: Because even ducks need to know if it's raining
- **Context Retention**: Maintains conversation context throughout your debugging session

### Built on Modern Technology
- **OpenAI GPT-5 Models**: Powered by cutting-edge language models
  - `gpt-5-mini`: Fast responses for general queries
  - `gpt-5`: Deep reasoning for complex problems
  - `gpt-realtime`: Real-time voice interaction
- **AutoGen Framework**: Multi-agent orchestration for complex tasks
- **FastAPI Backend**: High-performance async Python API
- **WebSocket Communication**: Real-time bidirectional communication

## How It Works

### Architecture

```
┌─────────────────┐
│   Web Browser   │
│  (User's Voice) │
└────────┬────────┘
         │ WebSocket
         │ (Audio Stream)
         ▼
┌─────────────────────────────────┐
│      FastAPI Backend            │
│                                 │
│  ┌──────────────────────────┐  │
│  │   RealtimeAgent          │  │
│  │   (AutoGen Framework)    │  │
│  └──────────┬───────────────┘  │
│             │                   │
│  ┌──────────▼───────────────┐  │
│  │  Registered Functions    │  │
│  │  - get_current_weather   │  │
│  │  - get_weather_forecast  │  │
│  │  - web_search            │  │
│  └──────────────────────────┘  │
└─────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────┐
│     OpenAI Realtime API         │
│     (gpt-realtime model)        │
└─────────────────────────────────┘
```

### The Conversation Flow

1. **User speaks** into their browser microphone
2. **Audio streams** via WebSocket to the FastAPI backend
3. **RealtimeAgent** processes the audio through OpenAI's Realtime API
4. **DUCK-E responds** with intelligent, context-aware answers
5. **Tool calls** are made when needed (web search, weather data)
6. **Conversation continues** with maintained context

### Key Components

#### `app/main.py`
The FastAPI application with:
- WebSocket endpoint (`/session`) for real-time audio communication
- RealtimeAgent initialization with system prompts
- Function registration for tools (weather, web search)
- Configuration loading for OpenAI models

#### Configuration Management
- **Auto-generated configs**: Automatically creates OpenAI model configurations
- **Multiple model support**: Switches between models based on task complexity
- **Tag-based filtering**: Organizes models by capability (realtime, chat, advanced)

#### Registered Functions
- `get_current_weather(location)`: Real-time weather data via WeatherAPI
- `get_weather_forecast(location)`: 3-day weather forecast
- `web_search(query)`: Web search using OpenAI's native capabilities

## Quick Start with Docker (Recommended)

### Using Pre-built Container

The easiest way to run DUCK-E is using our pre-built Docker container:

```bash
# Pull the latest image
docker pull ghcr.io/jedarden/duck-e:latest

# Run with your API keys (models auto-configured!)
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_openai_key \
  -e WEATHER_API_KEY=your_weather_key \
  ghcr.io/jedarden/duck-e:latest
```

Then navigate to `http://localhost:8000` and start talking!

### Docker Compose

Create a `docker-compose.yml`:

```yaml
version: '3.8'
services:
  duck-e:
    image: ghcr.io/jedarden/duck-e:latest
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - WEATHER_API_KEY=${WEATHER_API_KEY}
    restart: unless-stopped
```

Run with:
```bash
docker-compose up -d
```

## Setup & Installation

### Prerequisites
- Docker (for containerized deployment) OR
- Python 3.9+
- Node.js 16+ (for development tools)
- OpenAI API key with access to GPT-5/Realtime models
- WeatherAPI key (free at https://www.weatherapi.com/)

### Environment Configuration

DUCK-E now features **automatic configuration generation**! Simply provide your OpenAI API key, and the system will automatically configure all necessary models.

Create a `.env` file in the `ducke` directory:

```bash
# OpenAI API Configuration
# REQUIRED: Just add your API key - models are configured automatically!
OPENAI_API_KEY=sk-proj-your-api-key-here

# Weather API Configuration
WEATHER_API_KEY=your_weather_api_key_here
```

That's it! The application automatically configures:
- **gpt-5-mini**: Fast responses for general queries
- **gpt-5**: Advanced reasoning for complex problems
- **gpt-realtime**: Real-time voice interaction

**Advanced Users:** You can still manually override the configuration by providing `OAI_CONFIG_LIST` in your `.env` file (see `.env.example` for format).

### Installation

```bash
# Navigate to the ducke directory
cd ducke

# Install Python dependencies
pip install -r requirements.txt

# Run the development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker Deployment

```bash
# Build the Docker image
docker build -t ducke:latest .

# Run with docker-compose
docker-compose up
```

The application will be available at `http://localhost:8000`

## Usage

### Starting a Conversation

1. Navigate to `http://localhost:8000` in your browser
2. Click the microphone icon to start
3. Grant microphone permissions when prompted
4. Start speaking to DUCK-E!

### Example Interactions

**Debugging Help:**
```
You: "I'm getting a TypeError when I try to iterate over my API response"
DUCK-E: "Let's debug this together. Can you tell me what type of object
         you're receiving from the API? Is it a dictionary, list, or
         something else?"
```

**Web Search:**
```
You: "What's the latest syntax for async/await in Python 3.12?"
DUCK-E: "Let me search for the latest Python 3.12 documentation on that..."
        [Searches and provides accurate, current information]
```

**Weather Check:**
```
You: "What's the weather like in San Francisco?"
DUCK-E: "It's currently 62°F in San Francisco with partly cloudy skies..."
```

## Technology Stack

### Backend
- **FastAPI**: Modern, high-performance web framework
- **AutoGen**: Microsoft's framework for AI agent orchestration
- **OpenAI SDK**: Official Python client for OpenAI API
- **Uvicorn**: ASGI server for production deployment

### AI & Models
- **OpenAI GPT-5 Models**: Latest generation language models
- **Realtime API**: Low-latency voice interaction
- **Function Calling**: Tool integration for extended capabilities

### Frontend
- **WebRTC**: Real-time audio streaming
- **WebSocket**: Bidirectional communication
- **Jinja2 Templates**: Server-side rendering

### APIs & Services
- **OpenAI Realtime API**: Voice AI capabilities
- **WeatherAPI**: Weather data and forecasts
- **Native Web Search**: Built-in search functionality

## Configuration Details

### Model Configuration

DUCK-E uses three OpenAI models:

1. **gpt-5-mini**: Fast, efficient model for general queries
   - Used for: Quick questions, simple explanations
   - Response time: ~1-2 seconds

2. **gpt-5**: Advanced model for complex reasoning
   - Used for: Deep debugging, architecture discussions
   - Tagged: `gpt-5-full`

3. **gpt-realtime**: Specialized for voice interaction
   - Used for: Real-time conversation
   - Tagged: `gpt-realtime`
   - Features: Interruption handling, natural speech patterns

### RealtimeAgent Configuration

```python
realtime_llm_config = {
    "timeout": 86400,  # 24-hour timeout
    "config_list": realtime_config_list,
    "temperature": 1.0,  # Natural, varied responses
    "parallel_tool_calls": True,  # Execute multiple tools simultaneously
    "tool_choice": "auto",  # Intelligent tool selection
    "tools": [{"type": "web_search_preview"}]
}
```

## Development

### Project Structure

```
ducke/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application & WebSocket handler
│   └── website_files/
│       ├── static/          # CSS, JS, images
│       └── templates/       # HTML templates (Jinja2)
│           └── chat.html    # Main chat interface
├── .env                     # Environment configuration (not in git)
├── .env.example             # Example environment file
├── requirements.txt         # Python dependencies
├── dockerfile               # Docker container definition
├── docker-compose.yml       # Docker orchestration
└── README.md               # This file
```

### Adding New Functions

Register new capabilities by decorating functions:

```python
@realtime_agent.register_realtime_function(
    name="your_function_name",
    description="What this function does"
)
def your_function(param: Annotated[str, "description"]) -> str:
    # Your implementation
    return result
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key with Realtime access |
| `WEATHER_API_KEY` | Yes | WeatherAPI key for weather functions |
| `OAI_CONFIG_LIST` | Yes | JSON configuration for AI models |

## Deployment

### Production Considerations

1. **Security**:
   - Use environment variables for all secrets
   - Enable HTTPS/WSS for WebSocket connections
   - Implement rate limiting
   - Add authentication if needed

2. **Performance**:
   - Configure uvicorn workers based on CPU cores
   - Use a reverse proxy (nginx) for load balancing
   - Enable WebSocket compression
   - Monitor timeout settings

3. **Monitoring**:
   - Log all WebSocket connections and errors
   - Track API usage and costs
   - Monitor response times
   - Set up health check endpoints

### Docker Production Setup

```yaml
# docker-compose.prod.yml
version: '3.8'
services:
  ducke:
    build: .
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - WEATHER_API_KEY=${WEATHER_API_KEY}
      - OAI_CONFIG_LIST=${OAI_CONFIG_LIST}
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/status"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## The Philosophy: Why DUCK-E Matters

Traditional debugging is often a solitary activity. You stare at code, trace execution paths, and hope inspiration strikes. Rubber duck debugging acknowledged that **talking through problems helps** - but it stopped short of true conversation.

DUCK-E represents a new paradigm:

### 🤝 Collaborative Debugging
Instead of solving problems alone, you have an intelligent partner who understands context, asks clarifying questions, and offers insights.

### 💡 Learning While Debugging
DUCK-E doesn't just help you fix bugs - it explains concepts, suggests best practices, and helps you grow as a developer.

### 🚀 Faster Problem Resolution
Real-time feedback means less time stuck and more time shipping code.

### 🎯 Context-Aware Assistance
Unlike generic AI assistants, DUCK-E maintains conversation context, understanding your codebase and the specific problem you're tackling.

## Use Cases

### Solo Developers
- Brainstorm solutions when stuck
- Explain complex code to solidify understanding
- Get second opinions on architectural decisions

### Team Environments
- Quick help when teammates are unavailable
- Onboarding new developers with 24/7 assistance
- Document decisions through conversation

### Learning & Education
- Interactive coding tutorials
- Concept explanation on-demand
- Practice explaining code to improve understanding

## Limitations & Future Work

### Current Limitations
- Requires stable internet connection for OpenAI API
- API costs for usage
- Limited to audio interaction (no code viewing yet)
- No persistent memory across sessions

### Planned Features
- 🖥️ **Code Context Awareness**: Share your editor contents with DUCK-E
- 💾 **Session Memory**: Remember previous conversations
- 🔗 **IDE Integration**: VSCode and JetBrains plugins
- 📊 **Analytics Dashboard**: Track debugging patterns and improvements
- 🌍 **Multi-language Support**: Support for non-English conversations
- 🎨 **Customizable Personality**: Adjust DUCK-E's communication style

## Contributing

Contributions are welcome! This project is about making debugging more collaborative and accessible.

### How to Contribute
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Ideas for Contributions
- Add new tool functions (database queries, git operations, etc.)
- Improve the web interface
- Add support for different OpenAI models
- Create IDE plugins
- Improve error handling and logging
- Add automated tests

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- **Andrew Hunt & David Thomas** for introducing rubber duck debugging in *The Pragmatic Programmer*
- **OpenAI** for the Realtime API and GPT models
- **Microsoft AutoGen** team for the agent framework
- **FastAPI** and **Uvicorn** communities for excellent tools

## Support

For issues, questions, or suggestions:
- 🐛 **Bug Reports**: Open an issue on GitHub
- 💡 **Feature Requests**: Start a discussion
- 📧 **Contact**: [Your contact information]

---

**Remember**: Every great developer has talked to a rubber duck. Now it's time for the duck to talk back. 🦆✨

*"The best debugging sessions are conversations, not monologues."*
