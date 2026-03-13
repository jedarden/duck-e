# DUCK-E Modernization Plan

## Current State Summary

DUCK-E is a real-time voice assistant built on:
- **Backend**: FastAPI (Python) with AG2 (AutoGen) `RealtimeAgent` for orchestrating OpenAI Realtime API sessions
- **Frontend**: Vanilla JS + WebRTC via bundled `ag2client.js`
- **Model**: `gpt-realtime` (configured via AG2's `config_list` system)
- **Tools**: Weather (current + forecast via WeatherAPI), Web Search (via OpenAI Responses API)
- **Auth**: Skeleton JWT middleware exists but no OAuth provider is integrated
- **No memory system** exists today

### Key AG2 Touchpoints
| File | AG2 Usage |
|------|-----------|
| `requirements.txt` | `ag2==0.9.10` dependency |
| `app/main.py:1` | `from autogen.agentchat.realtime_agent import RealtimeAgent` |
| `app/main.py:288-294` | `RealtimeAgent(name, system_message, llm_config, websocket, logger)` |
| `app/main.py:314-441` | `@realtime_agent.register_realtime_function()` decorators (3 tools) |
| `app/main.py:444` | `await realtime_agent.run()` — the main session loop |
| `app/config.py` | `generate_oai_config_list()` with AG2 tag-based filtering |
| `app/website_files/static/ag2client.js` | Bundled client — WebRTC+WebSocket bridge, handles `ag2.init` message type |

---

## Change 1: Remove AG2 Dependency Entirely

### What AG2 `RealtimeAgent` Actually Does

By reading `ag2client.js` and tracing the backend, `RealtimeAgent` provides:

1. **Ephemeral key generation** — Calls OpenAI's `/v1/realtime/sessions` to get a short-lived client secret
2. **Session configuration** — Sends an `ag2.init` message over WebSocket containing the ephemeral key, model name, and init chunks (system message, tool definitions, session config)
3. **Tool call relay** — Receives `function_call` messages from the WebRTC data channel (forwarded via WebSocket), executes registered Python functions, and sends results back
4. **WebSocket lifecycle** — Manages accept/close and message routing

### Security Issue: API Key Exposure

**The current design leaks the OpenAI API key to the frontend.** In `ag2client.js`, the ephemeral key flow works correctly (the *ephemeral* client secret is sent to the browser, not the real API key). However, the `RealtimeSession` must ensure:

1. The real `OPENAI_API_KEY` is **never** included in any WebSocket message to the client
2. Only the ephemeral `client_secret` from `/v1/realtime/sessions` is sent in the init message
3. The `config` object sent to the client must be stripped of any `api_key` fields before transmission

The ephemeral key endpoint returns a response like:
```json
{
  "id": "sess_...",
  "client_secret": {"value": "ek_...", "expires_at": 1234567890},
  "model": "gpt-realtime-1.5",
  ...
}
```

Only `client_secret.value` and `model` should reach the client. The `RealtimeSession` must explicitly construct a sanitized config object rather than forwarding the raw API response.

### Replacement Strategy

Replace `RealtimeAgent` with a custom `RealtimeSession` class (~150 lines) that:

1. Calls `POST https://api.openai.com/v1/realtime/sessions` **server-side** with the real API key to obtain an ephemeral client secret
2. Constructs a **sanitized** init message containing only the ephemeral key and model — never the real API key
3. Sends the init message over the WebSocket to the client
4. Listens for incoming WebSocket messages containing `function_call` / `function_call_output` types
5. Dispatches tool calls to registered handler functions
6. Returns results back through the WebSocket → data channel path

### Files to Modify

| File | Change |
|------|--------|
| `requirements.txt` | Remove `ag2==0.9.10` |
| `app/config.py` | Simplify — remove tag-based filtering, just provide `{model, api_key}` dicts directly |
| `app/main.py` | Replace `RealtimeAgent` import and usage with new `RealtimeSession` class |
| `app/realtime_session.py` | **NEW** — Custom `RealtimeSession` implementation |
| `app/website_files/static/ag2client.js` | Rename references from `ag2` to `ducke`, update `ag2.init` → `ducke.init` message type (or keep compatible) |

### Implementation Details for `RealtimeSession`

```python
class RealtimeSession:
    def __init__(self, websocket, model, api_key, system_message, tools, voice="alloy", logger=None):
        self.websocket = websocket
        self.model = model
        self.api_key = api_key
        self.system_message = system_message
        self.tools = tools  # List of tool definitions
        self.tool_handlers = {}  # name -> callable
        self.voice = voice
        self.logger = logger

    def register_tool(self, name, description, handler, parameters):
        """Register a tool function (replaces @register_realtime_function)"""
        self.tool_handlers[name] = handler
        self.tools.append({
            "type": "function",
            "name": name,
            "description": description,
            "parameters": parameters
        })

    async def _get_ephemeral_key(self):
        """Call OpenAI /v1/realtime/sessions to get client secret.
        Returns a SANITIZED config safe for the client — never exposes the real API key."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/realtime/sessions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model,
                    "voice": self.voice,
                    "instructions": self.system_message,
                    "tools": self.tools,
                    "input_audio_transcription": {"model": "whisper-1"}
                }
            )
            resp.raise_for_status()
            data = resp.json()

            # SECURITY: Only return fields the client needs.
            # Never forward the raw response — it may contain server-side metadata.
            return {
                "client_secret": data["client_secret"],  # ephemeral key (short-lived)
                "model": data.get("model", self.model),
            }

    async def run(self):
        """Main session loop — replaces realtime_agent.run()"""
        await self.websocket.accept()
        session_data = await self._get_ephemeral_key()

        # Send init message to client (compatible with ag2client.js)
        # SECURITY: session_data contains only ephemeral key + model, never the real API key
        await self.websocket.send_json({
            "type": "ag2.init",  # Keep compatible with existing client
            "config": session_data,
            "init": []  # Session config chunks
        })

        # Listen for tool calls from client
        try:
            while True:
                data = await self.websocket.receive_json()
                if data.get("type") == "response.function_call_arguments.done":
                    result = await self._handle_tool_call(data)
                    await self.websocket.send_json(result)
        except WebSocketDisconnect:
            pass

    async def _handle_tool_call(self, data):
        """Execute registered tool and return result"""
        name = data.get("name")
        args = json.loads(data.get("arguments", "{}"))
        handler = self.tool_handlers.get(name)
        if handler:
            result = handler(**args) if not asyncio.iscoroutinefunction(handler) else await handler(**args)
            return {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": data.get("call_id"),
                    "output": str(result)
                }
            }
```

### Migration of Tool Registration

**Before (AG2):**
```python
@realtime_agent.register_realtime_function(
    name="get_current_weather", description="Get the current weather"
)
def get_current_weather(location: Annotated[str, "city"]) -> str:
    ...
```

**After (Custom):**
```python
session.register_tool(
    name="get_current_weather",
    description="Get the current weather",
    handler=get_current_weather,
    parameters={"type": "object", "properties": {"location": {"type": "string", "description": "city"}}, "required": ["location"]}
)
```

Or use a decorator wrapper that mirrors the old API for minimal diff.

---

## Change 2: Replace Model with `gpt-realtime-1.5`

### Files to Modify

| File | Change |
|------|--------|
| `app/config.py` | Change `"model": "gpt-realtime"` → `"model": "gpt-realtime-1.5"` |
| `app/realtime_session.py` | Default model parameter = `"gpt-realtime-1.5"` |

### Notes
- The model name is passed to OpenAI's `/v1/realtime/sessions` endpoint and to the WebRTC SDP negotiation URL (`https://api.openai.com/v1/realtime?model=...`)
- `ag2client.js` already reads the model from the init config (`data.model`), so no client change needed
- Verify the model name is exactly what OpenAI expects (check API docs for `gpt-realtime-1.5` vs `gpt-4o-realtime-preview-2024-12-17` etc.)

---

## Change 3: Voice Change Tool

### Design

Add a tool that the user can invoke via speech: *"Switch to the ember voice"* or *"Change your voice to shimmer"*

**Challenge**: Changing voice requires creating a new OpenAI Realtime session with a different `voice` parameter. The current session must be torn down and a new one established, seamlessly.

### Available OpenAI Voices
`alloy`, `ash`, `ballad`, `coral`, `echo`, `fable`, `nova`, `onyx`, `sage`, `shimmer`, `verse`

### Implementation Strategy

1. **Register a `change_voice` tool** with the realtime session
2. When the tool is called:
   a. Save the current conversation context (recent messages, system message)
   b. Generate a new ephemeral key with the requested voice
   c. Send a `ducke.reinit` message to the client with the new session config
   d. Client tears down old WebRTC peer connection and establishes a new one
   e. Inject saved context into the new session via `conversation.item.create` messages in the init chunks
3. The user hears DUCK-E respond in the new voice confirming the change

### Files to Modify

| File | Change |
|------|--------|
| `app/realtime_session.py` | Add `change_voice()` method, track conversation context |
| `app/main.py` | Register `change_voice` tool |
| `app/website_files/static/ag2client.js` | Handle `ducke.reinit` message — tear down and reconnect WebRTC |
| `app/website_files/static/main.js` | Show voice change status in transcript |

### Context Preservation Details

When switching voices:
1. Collect the last N conversation items (system message + recent turns) from the transcript
2. These become `init` chunks in the new session's `ducke.reinit` message:
   ```json
   {
     "type": "ducke.reinit",
     "config": { /* new ephemeral key config */ },
     "init": [
       {"type": "conversation.item.create", "item": {"type": "message", "role": "system", "content": [{"type": "input_text", "text": "...system message..."}]}},
       {"type": "conversation.item.create", "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "...prior user message..."}]}},
       {"type": "conversation.item.create", "item": {"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": "...prior assistant response..."}]}}
     ]
   }
   ```
3. The client replays these into the new data channel after the new session is established

### Voice Change Tool Definition

```python
session.register_tool(
    name="change_voice",
    description="Change the assistant's voice. Available voices: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse",
    handler=handle_voice_change,
    parameters={
        "type": "object",
        "properties": {
            "voice": {
                "type": "string",
                "enum": ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"],
                "description": "The voice to switch to"
            }
        },
        "required": ["voice"]
    }
)
```

---

## Change 4: User Memory (Google OAuth)

### Architecture

```
Browser ──(Google OAuth)──> Backend ──(headers: x-forwarded-user, x-user-email)──> WebSocket
                                          │
                                          ▼
                                   Memory Store (per-user)
```

### Authentication Flow

The app sits behind a reverse proxy (e.g., oauth2-proxy, Tailscale with Google OIDC) that handles Google OAuth and injects headers. The backend already logs `x-forwarded-user` (see `main.py:214`).

1. **No OAuth implementation in DUCK-E itself** — rely on the upstream proxy to authenticate and inject headers:
   - `x-forwarded-user` — Google user ID or email
   - `x-forwarded-email` — User's email
   - `x-forwarded-name` — Display name
2. Backend extracts user identity from these headers on WebSocket connect
3. Memory is keyed to the user identity

### Memory Store Design

Use a simple JSON file store (one file per user) for v1. Can be upgraded to Redis/SQLite later.

```
/data/memory/
  user_sha256hash1.json
  user_sha256hash2.json
```

Each file:
```json
{
  "user_id": "user@example.com",
  "created_at": "2026-03-12T...",
  "updated_at": "2026-03-12T...",
  "facts": [
    {"text": "User prefers Celsius for weather", "created_at": "..."},
    {"text": "User's name is Alex", "created_at": "..."}
  ]
}
```

### Memory Tools

Register two tools with the realtime session:

1. **`save_memory`** — Save a fact about the user for future reference
   - Called proactively by the model when it learns something worth remembering
   - Parameters: `{"fact": "string"}`

2. **`recall_memories`** — Retrieve stored memories about the current user
   - Called at session start (injected into system message) and on-demand
   - Parameters: none
   - Returns: list of stored facts

### System Message Enhancement

On session start, after identifying the user from headers:
```
You are DUCK-E... [existing system message]

The current user is {user_name} ({user_email}).
Here are things you remember about this user:
- {fact1}
- {fact2}
...
Use save_memory when you learn preferences or important information about the user.
```

### Files to Create/Modify

| File | Change |
|------|--------|
| `app/memory.py` | **NEW** — `UserMemoryStore` class with load/save/add_fact/get_facts |
| `app/main.py` | Extract user from headers, load memories, inject into system message, register memory tools |
| `app/realtime_session.py` | Pass user context through |

### Security
- Hash user emails before using as filenames to prevent path traversal
- Validate header values (already have input validation patterns)
- Memory file directory should be outside the app root
- Set a max facts limit per user (e.g., 100) to prevent abuse

---

## Change 5: Agentation Library Integration

### What Agentation Does

Agentation is a React-based UI annotation tool that lets users click on page elements to annotate them with CSS selectors, position data, and comments. It provides structured feedback for AI agents.

### Integration Approach

Since DUCK-E's frontend is vanilla JS (not React), we need to either:

**Option A: Mount React for Agentation only** (recommended)
- Add React + ReactDOM via CDN
- Mount `<Agentation />` component into a dedicated div
- Configure callbacks to pipe annotations into the chat/voice context

**Option B: Use Agentation's CSS-only variant**
- If available, use a framework-agnostic build

### Implementation

1. Add React 18 CDN scripts to `chat.html`
2. Add Agentation via CDN or bundle it
3. Create a mount point `<div id="agentation-root"></div>` in the page
4. Initialize with callbacks:

```html
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/agentation/dist/index.umd.js"></script>
<script>
  const agentationRoot = document.getElementById('agentation-root');
  const root = ReactDOM.createRoot(agentationRoot);
  root.render(
    React.createElement(Agentation.Agentation, {
      onAnnotationAdd: (annotation) => {
        // Send annotation context to DUCK-E via the data channel or display in transcript
        console.log('Annotation added:', annotation);
        addTranscriptMessage('system', `Annotation on "${annotation.element}": ${annotation.comment}`);
      },
      onSubmit: (output, annotations) => {
        // Pipe structured feedback to the voice session
        console.log('Feedback submitted:', output);
      },
      copyToClipboard: true
    })
  );
</script>
```

### Files to Modify

| File | Change |
|------|--------|
| `app/website_files/templates/chat.html` | Add React CDN, Agentation script, mount point div |
| `app/website_files/static/main.js` | Add handlers for annotation events |
| `app/middleware/security.py` | Update CSP to allow React/Agentation CDN sources |
| `package.json` | Optionally add `agentation` as dependency for bundling |

---

## Change 6: Footer/Version Pushed Below Fold

### Current Behavior

The footer with version badge and GitHub link is always visible at the bottom of the viewport. The chat transcript is in a fixed-height card.

### Desired Behavior

- Footer stays in document flow (not fixed)
- As chat history grows, the transcript area expands
- The footer gets pushed down naturally, eventually below the fold
- User can scroll down to see it, but it doesn't consume screen real estate during active chat

### Implementation

1. Remove `min-height: 100vh` constraint from container or make it `height: auto`
2. Change transcript card from fixed height to `flex-grow: 1` with no max-height
3. Footer remains in normal flow — as content grows, it scrolls away
4. On initial load (no chat), footer is visible at bottom of viewport

### CSS Changes

```css
/* Container: use min-height instead of fixed height */
.container {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
}

/* Main content area: grows to fill space */
.main-content {
    flex: 1;
    display: flex;
    flex-direction: column;
}

/* Transcript: grows with content, no max-height cap */
.main-content.has-history .transcript-card {
    flex: 1;
    max-height: none; /* Remove any max-height */
    overflow-y: auto;
}

/* Footer: stays in flow, pushed down by content */
.footer {
    flex-shrink: 0;
    /* Remove any position: fixed/sticky */
}
```

### Files to Modify

| File | Change |
|------|--------|
| `app/website_files/templates/chat.html` | Update CSS for container, transcript card, and footer layout |

---

## Change 7: Tool Call Display in Chat + Button Design Consistency

### Tool Call Display

When DUCK-E makes a tool call (weather, web search, voice change, memory), show it in the chat transcript as a collapsible card.

#### Design

```
┌─────────────────────────────────────┐
│ 🔧 get_current_weather              │  ← Collapsed (default)
│    ▸ Click to expand                │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│ 🔧 get_current_weather              │  ← Expanded
│    ▾ Click to collapse              │
│ ┌─────────────────────────────────┐ │
│ │ location: "San Francisco"      │ │
│ │ timestamp: 2026-03-12T14:30    │ │
│ │ status: completed              │ │
│ │ result: {"temp_f": 62, ...}    │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

#### Implementation

1. Intercept `response.function_call_arguments.done` and `response.output_item.done` events in `main.js`
2. Add a new message type `tool_call` to the transcript system
3. Render tool call messages with a collapsible details/summary HTML element

```javascript
// New function in main.js
const addToolCallMessage = (name, args, callId) => {
  transcriptMessages.push({
    role: 'tool',
    type: 'tool_call',
    toolName: name,
    toolArgs: args,
    callId: callId,
    status: 'pending',
    result: null,
    timestamp: Date.now()
  });
  renderTranscript();
  showTranscript();
};

// In renderTranscript, handle tool_call type:
if (msg.type === 'tool_call') {
  return `
    <div class="transcript-message tool-call" data-idx="${idx}">
      <details class="tool-call-details">
        <summary class="tool-call-summary">
          <span class="tool-call-icon">🔧</span>
          <span class="tool-call-name">${msg.toolName}</span>
          <span class="tool-call-status ${msg.status}">${msg.status}</span>
        </summary>
        <div class="tool-call-metadata">
          <pre>${JSON.stringify(JSON.parse(msg.toolArgs), null, 2)}</pre>
          ${msg.result ? `<div class="tool-call-result"><strong>Result:</strong><pre>${msg.result}</pre></div>` : ''}
        </div>
      </details>
    </div>
  `;
}
```

### Button Design Consistency

Currently the controls use a mix of styles:
- Connect/Disconnect button: `audio-btn` class
- Mute button: `audio-btn` class (same)
- PTT toggle: Custom checkbox style
- PTT hold button: `ptt-btn` class (different style)
- Clear transcript: `clear-transcript-btn` (different style)

#### Unified Button System

Create a consistent button design system:

```css
/* Base button */
.ducke-btn {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 20px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--bg-secondary);
    color: var(--text-primary);
    font-family: inherit;
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
}

.ducke-btn:hover {
    background: var(--bg-tertiary);
    border-color: var(--primary-light);
}

/* Variants */
.ducke-btn--primary {
    background: var(--primary);
    border-color: var(--primary);
}

.ducke-btn--primary:hover {
    background: var(--primary-dark);
}

.ducke-btn--danger {
    /* For disconnect state */
    background: rgba(239, 68, 68, 0.1);
    border-color: var(--danger);
    color: var(--danger);
}

/* Sizes */
.ducke-btn--sm {
    padding: 6px 12px;
    font-size: 12px;
}

/* Icon-only */
.ducke-btn--icon {
    padding: 10px;
    border-radius: 50%;
}
```

Apply this system to all interactive elements: Connect, Disconnect, Mute, PTT toggle, PTT hold, Clear transcript.

### Files to Modify

| File | Change |
|------|--------|
| `app/website_files/static/main.js` | Add `addToolCallMessage()`, update `handleWebRTCMessage()` to capture function call events, update `renderTranscript()` for tool call rendering |
| `app/website_files/templates/chat.html` | Add tool call CSS styles, create unified button CSS system, update button markup to use new classes |
| `app/website_files/static/ag2client.js` | Ensure function call messages are forwarded to `onMessage` (already done) |

---

## Change 8: Estimated Hourly Cost Display

### gpt-realtime-1.5 Pricing

| Token Type | Input (per 1M) | Cached Input (per 1M) | Output (per 1M) |
|------------|----------------|----------------------|-----------------|
| **Text** | $4.00 | $0.40 | $16.00 |
| **Audio** | $32.00 | $0.40 | $64.00 |
| **Image** | $5.00 | $0.50 | N/A |

For a voice assistant, the dominant costs are **audio tokens**:
- Audio input: $32.00 / 1M tokens
- Audio output: $64.00 / 1M tokens

### Cost Estimation Model

OpenAI's Realtime API uses audio tokens at roughly **50 tokens per second** of audio (based on the 24kHz PCM encoding and OpenAI's tokenization). Estimating for a typical voice conversation:

| Scenario | Audio In (tokens/hr) | Audio Out (tokens/hr) | Text In (tokens/hr) | Text Out (tokens/hr) | Estimated Cost/hr |
|----------|---------------------|----------------------|--------------------|--------------------|-------------------|
| **Light** (user talks 5 min/hr, DUCK-E responds 5 min/hr) | 15,000 | 15,000 | ~2,000 | ~2,000 | ~$1.48 |
| **Moderate** (user talks 15 min/hr, DUCK-E responds 15 min/hr) | 45,000 | 45,000 | ~5,000 | ~5,000 | ~$4.42 |
| **Heavy** (user talks 25 min/hr, DUCK-E responds 25 min/hr) | 75,000 | 75,000 | ~10,000 | ~10,000 | ~$7.36 |

Formula:
```
cost_per_hour = (audio_in_tokens * $32/1M) + (audio_out_tokens * $64/1M)
             + (text_in_tokens * $4/1M) + (text_out_tokens * $16/1M)
```

Note: The existing `CostProtectionMiddleware` tracks session costs server-side but doesn't expose them to the user.

### Implementation

Every `response.done` event from the Realtime API includes a `usage` object with token counts:
```json
{
  "type": "response.done",
  "response": {
    "usage": {
      "total_tokens": 1234,
      "input_tokens": 500,
      "output_tokens": 734,
      "input_token_details": {
        "text_tokens": 100,
        "audio_tokens": 400,
        "cached_tokens": 50
      },
      "output_token_details": {
        "text_tokens": 134,
        "audio_tokens": 600
      }
    }
  }
}
```

Accumulate these counts from each `response.done` in the frontend, apply the pricing rates, and derive the hourly estimate from `(total_cost / session_elapsed_hours)`.

### UI Design

#### Collapsed (default)

```
┌──────────────────────────────────────────┐
│ ● Connected - DUCK-E is listening        │
│ Est. ~$3.20/hr  ▸                        │
└──────────────────────────────────────────┘
```

#### Expanded (click to toggle)

```
┌──────────────────────────────────────────┐
│ ● Connected - DUCK-E is listening        │
│ Est. ~$3.20/hr  ▾                        │
│ ┌──────────────────────────────────────┐ │
│ │ Session Duration   12m 34s           │ │
│ │ ─────────────────────────────────    │ │
│ │ Audio Input        $0.38             │ │
│ │ Audio Output       $0.72             │ │
│ │ Text Input         $0.01             │ │
│ │ Text Output        $0.04             │ │
│ │ Cached Input       $0.00             │ │
│ │ ─────────────────────────────────    │ │
│ │ Session Total      $1.15             │ │
│ └──────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

The cost display should:
- Only appear after the first `response.done` with usage data
- Update after each response
- Show estimated hourly rate in the collapsed view
- On click, expand to show per-category cost breakdown and session duration
- Use a muted color to not distract from the conversation
- Flash briefly when updated
- Use `<details>/<summary>` for the expand/collapse (consistent with tool call cards from Change 7)

#### Frontend Implementation

```javascript
// Cost tracking state
let sessionCost = {
  startTime: null,
  totalInputTextTokens: 0,
  totalInputAudioTokens: 0,
  totalOutputTextTokens: 0,
  totalOutputAudioTokens: 0,
  totalCachedTokens: 0,
};

// Pricing constants (gpt-realtime-1.5)
const PRICING = {
  textInput: 4.00 / 1_000_000,
  textOutput: 16.00 / 1_000_000,
  audioInput: 32.00 / 1_000_000,
  audioOutput: 64.00 / 1_000_000,
  cachedInput: 0.40 / 1_000_000,
};

const updateCostFromResponse = (usage) => {
  if (!sessionCost.startTime) sessionCost.startTime = Date.now();

  const details = usage.input_token_details || {};
  const outDetails = usage.output_token_details || {};

  sessionCost.totalInputTextTokens += details.text_tokens || 0;
  sessionCost.totalInputAudioTokens += details.audio_tokens || 0;
  sessionCost.totalCachedTokens += details.cached_tokens || 0;
  sessionCost.totalOutputTextTokens += outDetails.text_tokens || 0;
  sessionCost.totalOutputAudioTokens += outDetails.audio_tokens || 0;

  const totalCost =
    (sessionCost.totalInputTextTokens * PRICING.textInput) +
    (sessionCost.totalInputAudioTokens * PRICING.audioInput) +
    (sessionCost.totalOutputTextTokens * PRICING.textOutput) +
    (sessionCost.totalOutputAudioTokens * PRICING.audioOutput) +
    (sessionCost.totalCachedTokens * PRICING.cachedInput);

  const elapsedHours = (Date.now() - sessionCost.startTime) / 3_600_000;
  const hourlyRate = elapsedHours > 0 ? totalCost / elapsedHours : 0;

  // Per-category costs for breakdown
  const costs = {
    audioInput:  sessionCost.totalInputAudioTokens  * PRICING.audioInput,
    audioOutput: sessionCost.totalOutputAudioTokens * PRICING.audioOutput,
    textInput:   sessionCost.totalInputTextTokens   * PRICING.textInput,
    textOutput:  sessionCost.totalOutputTextTokens  * PRICING.textOutput,
    cached:      sessionCost.totalCachedTokens      * PRICING.cachedInput,
  };

  const elapsedMs = Date.now() - sessionCost.startTime;
  updateCostDisplay(totalCost, hourlyRate, costs, elapsedMs);
};

const updateCostDisplay = (totalCost, hourlyRate, costs, elapsedMs) => {
  const el = document.getElementById('cost-display');
  if (!el) return;
  el.style.display = 'block';

  const fmt = (v) => '$' + v.toFixed(2);
  const mins = Math.floor(elapsedMs / 60000);
  const secs = Math.floor((elapsedMs % 60000) / 1000);
  const duration = `${mins}m ${secs.toString().padStart(2, '0')}s`;

  el.innerHTML = `
    <details class="cost-details">
      <summary class="cost-summary">
        Est. ~${fmt(hourlyRate)}/hr
      </summary>
      <div class="cost-breakdown">
        <div class="cost-row"><span>Session Duration</span><span>${duration}</span></div>
        <hr class="cost-divider">
        <div class="cost-row"><span>Audio Input</span><span>${fmt(costs.audioInput)}</span></div>
        <div class="cost-row"><span>Audio Output</span><span>${fmt(costs.audioOutput)}</span></div>
        <div class="cost-row"><span>Text Input</span><span>${fmt(costs.textInput)}</span></div>
        <div class="cost-row"><span>Text Output</span><span>${fmt(costs.textOutput)}</span></div>
        <div class="cost-row"><span>Cached Input</span><span>${fmt(costs.cached)}</span></div>
        <hr class="cost-divider">
        <div class="cost-row cost-total"><span>Session Total</span><span>${fmt(totalCost)}</span></div>
      </div>
    </details>
  `;
};

// In handleWebRTCMessage, add:
if (data.type === 'response.done' && data.response?.usage) {
  updateCostFromResponse(data.response.usage);
}
```

### Files to Modify

| File | Change |
|------|--------|
| `app/website_files/static/main.js` | Add cost tracking state, `updateCostFromResponse()`, intercept `response.done` events |
| `app/website_files/templates/chat.html` | Add cost display element near status indicator, CSS for cost badge |
| `app/website_files/static/ag2client.js` | Ensure `response.done` events are forwarded via `onMessage` (already done — all events are forwarded) |

### Security Note

Cost data is computed entirely client-side from usage metadata in API responses. No API keys or billing credentials are exposed. The server-side `CostProtectionMiddleware` remains the authoritative cost limiter — this frontend display is informational only.

---

## Execution Order

The changes have dependencies and should be implemented in this order:

```
Phase 1 — Foundation (no visible changes yet)
  1. Change 1: Remove AG2 → create RealtimeSession
  2. Change 2: Switch to gpt-realtime-1.5

Phase 2 — Backend Features
  3. Change 4: Memory system (needs RealtimeSession for tool registration)
  4. Change 3: Voice change tool (needs RealtimeSession + reinit protocol)

Phase 3 — Frontend
  5. Change 7: Tool call display + button consistency
  6. Change 8: Cost display (depends on Change 7 for consistent UI patterns)
  7. Change 6: Footer/layout changes
  8. Change 5: Agentation integration
```

### Risk Notes

- **Change 1** is the riskiest — it replaces the core session management. Must verify the exact WebSocket message protocol AG2's RealtimeAgent uses by tracing actual messages.
- **Change 3** requires client-side reconnection logic, which is complex. The WebRTC peer connection teardown/rebuild needs careful handling of audio state.
- **Change 5** introduces React as a dependency for a single component. If Agentation's UMD bundle size is too large, consider lazy-loading it.
- **Change 2** depends on `gpt-realtime-1.5` being a valid model identifier at OpenAI. Verify before implementing.

### Testing Strategy

- Each phase should be testable independently
- Phase 1: Verify WebSocket connects, audio streams, tools execute
- Phase 2: Verify memory persists across sessions, voice changes seamlessly
- Phase 3: Verify tool calls render, layout scrolls correctly, Agentation toolbar loads
