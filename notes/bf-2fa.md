# Bead bf-2fa: Tool Call UI and Button Design

## Status: Already Complete

This task was implemented in commit 9db5a72 (2026-03-20): "Fix tool call continuation and improve tool card UI"

### Tool Call Events in Chat UI

Tool calls are displayed as collapsible cards in the conversation transcript with:

- **Visual indicators**: Tool-specific icons with gradient backgrounds
- **Metadata**: Timestamp, status badge (pending/completed)
- **Collapsible sections**:
  - Request: JSON-formatted tool arguments
  - Response: Tool result output (when available)
- **Animations**: Appear animation on creation, completion flash when result arrives
- **Auto-expand**: New tool calls are expanded by default

**Supported tools:**
- `get_current_weather` → Weather (🌤️)
- `web_search` → Web Search (🔍)
- `web_fetch` → Fetch URL (📄)
- `voice_change` → Voice Change (🎙️)
- `save_memory` → Save Memory (💾)
- `recall_memories` → Recall Memories (🧠)

### Unified Button Design

All buttons use the `.ducke-btn` base class with modifier system:

**Size variants:**
- `.ducke-btn--xs` - Tiny inline buttons
- `.ducke-btn--sm` - Small buttons (inline controls)
- `.ducke-btn--lg` - Large CTA buttons

**Style variants:**
- `.ducke-btn--accent` - Gradient primary action (Connect)
- `.ducke-btn--primary` - Solid primary action
- `.ducke-btn--danger` - Destructive actions (Disconnect)
- `.ducke-btn--icon` - Icon-only buttons (mute, PTT)

**State modifiers:**
- `.muted` - Muted microphone state
- `.connected` - Connected state
- `.active` - PTT button being held
- `.hidden` - Hidden element

All buttons in the UI follow this system consistently.

## Implementation Files

- `app/website_files/static/main.js` - Tool call handling and transcript rendering
- `app/website_files/static/ag2client.js` - WebRTC message relay
- `app/website_files/templates/chat.html` - Button markup and CSS

## Code References

- Tool call rendering: `main.js:688-747`
- Button styles: `chat.html:558-710`
- Tool call message handling: `main.js:920-966`
