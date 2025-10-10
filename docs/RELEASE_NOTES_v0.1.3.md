## 🦆 DUCK-E v0.1.3 - Critical Docker Fix

### 🐛 Bug Fix

**Fixed ModuleNotFoundError in Docker Container** - Resolved critical import error that prevented the container from starting.

**Error Fixed:**
```
ModuleNotFoundError: No module named 'app'
```

### Changes

- ✅ **Corrected Dockerfile paths**: Changed WORKDIR from `/python-docker` to `/app`
- ✅ **Fixed module structure**: App directory now copied as `./app` to maintain proper Python import paths
- ✅ **Updated startup command**: Changed CMD to use `app.main:app` instead of `main:app`

### What's Included

This release includes all features from v0.1.2:
- 🚀 **Automatic Configuration**: Just provide OPENAI_API_KEY - models configured automatically
- 🎯 **Auto-configured Models**: gpt-5-mini, gpt-5, and gpt-realtime
- ⚡ **Timeout Fix**: Extended httpx timeout for reliable OpenAI API connections
- 🦆 **Real-time Voice Interaction**: WebRTC-powered debugging assistant

### Docker Usage

#### Pull the image:
```bash
docker pull ghcr.io/jedarden/duck-e:latest
# or specific version
docker pull ghcr.io/jedarden/duck-e:0.1.3
```

#### Run with automatic configuration:
```bash
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_openai_key \
  -e WEATHER_API_KEY=your_weather_key \
  ghcr.io/jedarden/duck-e:latest
```

#### Using docker-compose:
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

### Requirements

- OpenAI API key with access to GPT-5/Realtime models
- WeatherAPI key (free at https://www.weatherapi.com/)

### Quick Start

1. Pull the container image
2. Set your API keys in environment variables
3. Run the container
4. Navigate to http://localhost:8000
5. Start talking to DUCK-E!

### Tech Stack

- FastAPI + Uvicorn
- OpenAI Realtime API
- Microsoft AutoGen
- WebRTC for audio streaming
- Docker multi-platform support (amd64/arm64)

### Version History

- **v0.1.3**: Docker import path fix (current)
- **v0.1.2**: Automatic configuration generation
- **v0.1.1**: Connection timeout fix
- **v0.1.0**: Initial release

### Documentation

Full documentation available at: https://github.com/jedarden/duck-e

### Support

- 🐛 Report bugs: https://github.com/jedarden/duck-e/issues
- 💡 Request features: https://github.com/jedarden/duck-e/discussions
- 📖 Read docs: https://github.com/jedarden/duck-e#readme

---

**The duck is ready to talk back! 🦆✨**

**Full Changelog**: https://github.com/jedarden/duck-e/compare/v0.1.2...v0.1.3
