# DUCK-E v0.1.5 Release Notes

**Release Date:** October 10, 2025
**Docker Image:** `ghcr.io/jedarden/duck-e:0.1.5`

## 🎯 What's New in v0.1.5

### ✨ Tool Call Logging UI

This release adds comprehensive **tool call logging** to the web interface, giving you full visibility into DUCK-E's function calls during conversations.

**Features:**
- 🛠️ **Real-time Tool Call Monitoring**: See every tool call (weather, web search, etc.) as it happens
- 📊 **Detailed Call Information**: View tool name, parameters (JSON), and responses
- ⏱️ **Timestamped Entries**: Track when each tool was called
- 🎯 **Badge Counter**: Quick visual indicator of total tool calls
- 🧹 **Clear Log**: Reset the log anytime
- 📦 **Collapsible Panel**: Expandable interface that stays out of your way

**How It Works:**
The interface now intercepts WebSocket messages from the OpenAI Realtime API to capture:
1. **Function Call Requests** (`response.function_call_arguments.done`)
2. **Function Call Responses** (`conversation.item.created`)
3. Matches them by `call_id` for complete call tracking

**Example Tool Calls You'll See:**
- `get_weather` - Location queries with temperature and conditions
- `web_search` - Search queries with relevant results
- Any custom tools you've added to your agent

## 🔧 Technical Changes

### Frontend Updates
- **chat.html**: Added 166 lines of CSS styling and HTML structure for tool log panel
- **main.js**: Complete rewrite with 180+ new lines for WebSocket interception and UI management

### New Functions
- `setupToolCallListeners()` - Intercepts WebSocket messages
- `addToolCall()` - Adds entries to the log
- `updateToolLogDisplay()` - Renders the tool log UI
- `clearToolLog()` - Clears all entries
- `toggleToolLog()` - Expands/collapses the panel
- `formatTime()` - Formats timestamps for display
- `truncateString()` - Limits long responses to 500 chars

### UI Components
- Collapsible tool log card with smooth transitions
- Tool entry cards with color-coded borders
- Badge counter with auto-hide when empty
- Empty state message for first-time users
- Clear log button (only visible when entries exist)

## 📦 Installation

### Pull Latest Image
```bash
docker pull ghcr.io/jedarden/duck-e:0.1.5
```

### Run Container
```bash
docker run -d \
  --name duck-e \
  -p 8000:8000 \
  -e OPENAI_API_KEY=sk-proj-your-key-here \
  -e WEATHER_API_KEY=your-weather-key-here \
  ghcr.io/jedarden/duck-e:0.1.5
```

### Using Docker Compose
```yaml
services:
  duck-e:
    image: ghcr.io/jedarden/duck-e:0.1.5
    container_name: duck-e
    ports:
      - "8000:8000"
    environment:
      - OPENAI_API_KEY=sk-proj-your-key-here
      - WEATHER_API_KEY=your-weather-key-here
    restart: unless-stopped
```

## 🔄 Upgrade Instructions

### From v0.1.4 or Earlier
```bash
# Stop existing container
docker stop duck-e
docker rm duck-e

# Pull new version
docker pull ghcr.io/jedarden/duck-e:0.1.5

# Start with new version (same command as above)
docker run -d --name duck-e -p 8000:8000 ...
```

No configuration changes needed - this is a frontend-only update!

## 🐛 Bug Fixes

None - this is a pure feature release.

## 📊 Testing the New Feature

1. **Connect to DUCK-E**: Click "Connect" button
2. **Ask Questions**: Try "What's the weather in San Francisco?" or "Search the web for latest AI news"
3. **Watch Tool Log**: See the tool log panel populate with function calls
4. **Expand/Collapse**: Click the header to show/hide details
5. **Clear Log**: Use the "Clear Log" button to reset

## 🎨 UI Improvements

- **Modern Design**: Glassmorphism effects with backdrop blur
- **Smooth Animations**: Fade-in and slide-in transitions for new entries
- **Color Coding**: Primary color accents for easy scanning
- **Responsive Layout**: Works on mobile and desktop
- **Auto-scroll**: Latest entries appear at the top

## 📚 Documentation

Full documentation available at: https://github.com/jedarden/duck-e

## 🤝 Contributing

- 🐛 Report bugs: https://github.com/jedarden/duck-e/issues
- 💡 Request features: https://github.com/jedarden/duck-e/discussions
- 📖 Read docs: https://github.com/jedarden/duck-e#readme

## 📝 Notes

This release maintains **100% backward compatibility** with v0.1.4 - no configuration changes required.

**Full Changelog**: https://github.com/jedarden/duck-e/compare/v0.1.4...v0.1.5
