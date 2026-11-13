# OpenAI Realtime API — facts verified live, 2026-11-12

Everything here was observed against the live API with DUCK-E's own key, on
`gpt-realtime-2` and again on `gpt-realtime-2.1` (20 of 20 checks passed on
each). Re-run the checks any time:

```bash
OPENAI_API_KEY="$(bao-as openbao-v2 bao kv get -field=OPENAI_API_KEY \
    secret/ardenone-cluster/ducke/openai)" python3 tests/live/realtime_api_check.py
```

The script prints PASS/FAIL per fact and never prints a secret. Unit tests must
not call the live API; they assert the request DUCK-E builds (see
`tests/test_realtime_session.py`) and the browser behaviour (see
`tests/frontend/`).

## Session creation — `POST /v1/realtime/client_secrets`

DUCK-E builds this body in `RealtimeSession._get_ephemeral_key`
(`app/realtime_session.py`). Field paths under `session`:

| Path | Verified values | Notes |
|---|---|---|
| `audio.input.turn_detection` | `{"type": "semantic_vad", "eagerness": "auto"}` accepted; also `low`. Omitted → the API applies `server_vad` (`threshold 0.5`, `silence_duration_ms 500`). `null` → no automatic turn detection. | Echoed back with `create_response: true`, `interrupt_response: true`. |
| `audio.input.noise_reduction` | `{"type": "near_field"}`, `{"type": "far_field"}`. Omitted → `null` (off). | |
| `audio.input.transcription` | `{"model": "gpt-4o-transcribe", "language": "en"}`; `gpt-4o-mini-transcribe`, `gpt-transcribe`, `gpt-live-transcribe` and `whisper-1` are also accepted. | `language` must be a two-letter code from the list below. `en-US` returns HTTP 400 and the session is not created. |
| `audio.output.voice` | `alloy`, `ash`, `ballad`, `coral`, `echo`, `sage`, `shimmer`, `verse`, `marin`, `cedar`. | `fable`, `nova`, `onyx` return HTTP 400: they are text-to-speech voices, not Realtime voices. |
| `audio.output.speed` | Echoed as `1.0` by default. | Not exercised beyond the default. |

Supported transcription `language` codes (from the API's own error message):

```
af ar az be bg bs ca cs cy da de el en es et fa fi fr gl he hi hr hu hy id is
it iw ja kk kn ko lt lv mi mk mr ms ne nl no pl pt ro ru sk sl sr sv sw ta th
tl tr uk ur vi zh
```

## `session.update` during a session

| Update | Result |
|---|---|
| `{"audio": {"input": {"turn_detection": {"type": "semantic_vad", "eagerness": "low"}}}}` | `session.updated` |
| `{"audio": {"input": {"turn_detection": null}}}` | `session.updated` |
| `{"output_modalities": ["text"]}` | `session.updated` |
| `{"audio": {"output": {"voice": "ash"}}}` before the assistant has produced audio | `session.updated` |
| the same after the assistant has produced audio | error: `Cannot update a conversation's voice if assistant audio is present.` |
| `{"voice": "ash"}` (flat field — what `RealtimeSession.change_voice` sent on this date) | error: `Unknown parameter: 'session.voice'.` |

Consequence: a mid-conversation voice change needs a new Realtime session. The
old conversation can be carried over by replaying it as text items (below).

## Conversation items and responses

- A typed user turn is `conversation.item.create` with
  `{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "..."}]}`
  followed by `{"type": "response.create"}`.
- An earlier assistant turn can be replayed with `role: "assistant"` and content
  type `output_text`. After replaying a user and an assistant item, the model
  answered a follow-up question from that context.
- A client-supplied `item.id` (for example `msg_ducke_client_0001`) is kept and
  echoed in `conversation.item.added`.
- `response.create` accepts per-response instructions:
  `{"type": "response.create", "response": {"instructions": "..."}}`.
- A `response.create` sent while a response is in progress returns the error
  `Conversation already has an active response in progress`. Send
  `response.cancel` first; it ends the active response with
  `response.done` (`status: "cancelled"`).
- Events seen for a text-only reply, in order: `conversation.item.added`,
  `conversation.item.done`, `response.created`, `response.output_item.added`,
  `response.content_part.added`, `response.output_text.delta`,
  `response.output_text.done`, `response.content_part.done`,
  `response.output_item.done`, `response.done`. Note the text events are
  `response.output_text.*`; `main.js` on this date listens for the older
  `response.text.*` names only.

## Not verified here

These checks used the WebSocket transport, so WebRTC-only events were not
observed: `output_audio_buffer.started`, `output_audio_buffer.stopped`,
`output_audio_buffer.cleared`. Code that relies on them must also work when they
never arrive.

## Models available to this key

| Family | Models (creation date) |
|---|---|
| Realtime | `gpt-realtime` (2025-08), `gpt-realtime-mini` (2025-10), `gpt-realtime-1.5` (2026-02), `gpt-realtime-2` (2026-05, in use), `gpt-realtime-2.1` and `gpt-realtime-2.1-mini` (2026-06) |
| Transcription | `whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `gpt-transcribe` and `gpt-live-transcribe` (2026-07) |

The realtime model is set by the `REALTIME_MODEL` environment variable
(`app/config.py`); the deployed value is in `ducke-configmap.yml` and
`ducke-demo-configmap.yml` under
`declarative-config/k8s/ardenone-cluster/ducke/`. Pricing for the 2.1 models was
not checked.
