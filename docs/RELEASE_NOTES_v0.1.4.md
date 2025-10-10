## 🦆 DUCK-E v0.1.4 - Port Standardization

### 🐛 Bug Fix

**Standardized Port Configuration** - Fixed inconsistency between actual port (5050) and documented port (8000).

**Issue:**
- Dockerfile was configured to serve on port 5050
- All documentation referenced port 8000
- This caused confusion and connection failures when following documentation

### Changes

- ✅ **Dockerfile port**: Changed from 5050 to 8000
- ✅ **docker-compose.yml**: Updated port mapping from `5050:5050` to `8000:8000`
- ✅ **Healthcheck**: Updated to use `http://localhost:8000/status`
- ✅ **Consistency**: All configurations now align with documentation

### What's Included

This release includes all features from previous versions:
- 🚀 **Automatic Configuration**: Just provide OPENAI_API_KEY - models configured automatically
- 🎯 **Auto-configured Models**: gpt-5-mini, gpt-5, and gpt-realtime
- ⚡ **Timeout Fix**: Extended httpx timeout for reliable OpenAI API connections
- 🐛 **Import Fix**: Corrected module paths for proper Python imports
- 🔌 **Port 8000**: Standard web application port for easy access

### Docker Usage

#### Pull the image:
```bash
docker pull ghcr.io/jedarden/duck-e:latest
# or specific version
docker pull ghcr.io/jedarden/duck-e:0.1.4
```

#### Run with automatic configuration:
```bash
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_openai_key \
  -e WEATHER_API_KEY=your_weather_key \
  ghcr.io/jedarden/duck-e:latest
```

Then navigate to **http://localhost:8000** 🎉

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
4. Navigate to **http://localhost:8000**
5. Start talking to DUCK-E!

### Tech Stack

- FastAPI + Uvicorn
- OpenAI Realtime API
- Microsoft AutoGen
- WebRTC for audio streaming
- Docker multi-platform support (amd64/arm64)

### Version History

- **v0.1.4**: Port standardization to 8000 (current)
- **v0.1.3**: Docker import path fix
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

**The duck is ready to talk back on port 8000! 🦆✨**

**Full Changelog**: https://github.com/jedarden/duck-e/compare/v0.1.3...v0.1.4
