## 🦆 DUCK-E v0.1.2 - Automatic Configuration

### ✨ New Features

**Automatic OAI_CONFIG_LIST Generation** - DUCK-E now automatically configures all OpenAI models from a single API key!

- 🚀 **Simplified Setup**: Just provide OPENAI_API_KEY - no manual JSON configuration needed
- 🎯 **Auto-Configuration**: Automatically sets up gpt-5-mini, gpt-5, and gpt-realtime models
- 🔧 **Flexible**: Advanced users can still manually override with custom configurations
- 💡 **Better Errors**: Clearer feedback when configuration is missing or invalid

### What's New

DUCK-E (The Duck That Talks Back) is an AI-powered voice assistant that revolutionizes rubber duck debugging by actively engaging in your debugging process.

### Core Features

- ✅ **Real-time Voice Interaction**: WebRTC-powered voice conversations using OpenAI's Realtime API
- ✅ **Intelligent Code Assistance**: Context-aware debugging help and suggestions
- ✅ **Web Search Integration**: Search for documentation and solutions in real-time
- ✅ **Weather Information**: Check weather while you debug
- ✅ **Multi-Agent Support**: Powered by AutoGen framework for complex tasks

### Docker Usage (Simplified!)

#### Pull the image:
```bash
docker pull ghcr.io/jedarden/duck-e:latest
# or specific version
docker pull ghcr.io/jedarden/duck-e:v0.1.2
```

#### Run with automatic configuration (recommended):
```bash
docker run -d \
  -p 8000:8000 \
  -e OPENAI_API_KEY=your_openai_key \
  -e WEATHER_API_KEY=your_weather_key \
  ghcr.io/jedarden/duck-e:latest
```

That's it! Models are configured automatically. 🎉

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

#### Advanced: Manual Configuration Override
For advanced users who need custom model configurations, you can still provide OAI_CONFIG_LIST manually.

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

### Previous Releases

- **v0.1.1**: Connection timeout fix for OpenAI Realtime API
- **v0.1.0**: Initial release with manual configuration

### Documentation

Full documentation available at: https://github.com/jedarden/duck-e

### Support

- 🐛 Report bugs: https://github.com/jedarden/duck-e/issues
- 💡 Request features: https://github.com/jedarden/duck-e/discussions
- 📖 Read docs: https://github.com/jedarden/duck-e#readme

---

**The duck is ready to talk back! 🦆✨**

**Full Changelog**: https://github.com/jedarden/duck-e/compare/v0.1.1...v0.1.2
