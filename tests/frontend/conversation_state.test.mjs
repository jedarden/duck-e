// Live conversation state: while connected the status line always says what
// DUCK-E is doing right now (Listening / Hearing you… / Thinking… / Using … /
// Speaking / Microphone muted / Hold to talk) and silence is never ambiguous.
//
// The transition logic lives in the pure nextConversationState(); the table
// tests below pin every transition. The page tests drive the real flow
// through the harness: voice turns with and without the WebRTC-only
// output_audio_buffer events (never observed in the live-API verification —
// docs/research/realtime-api-verified-2026-11-12.md), tool turns, mute/PTT
// idle variants, and the indicator/title DOM.
import test from 'node:test';
import assert from 'node:assert/strict';
import { connectPage, loadPage, makeBrowser } from './harness.mjs';

const LISTEN_FLAGS = { isMuted: false, isPushToTalk: false, talkButtonHeld: false, sawOutputAudioBuffer: false };
const MUTED_FLAGS = { ...LISTEN_FLAGS, isMuted: true };
const PTT_FLAGS = { ...LISTEN_FLAGS, isPushToTalk: true };

// One page instance is enough for the pure-function tests: they touch no DOM.
const page = loadPage();

// Call the pure transition function inside the vm and JSON-round-trip the
// result — objects from the vm realm defeat deepStrictEqual on prototypes.
const step = (current, event, flags) => page.snapshot(
  `nextConversationState(${JSON.stringify(current)}, ${JSON.stringify(event)}, ${JSON.stringify(flags)})`,
);

const label = (state, detail) => page.snapshot(
  `conversationStateLabel(${JSON.stringify(state)}, ${JSON.stringify(detail)})`,
);

test('pure transitions: speech, response and audio-buffer events', () => {
  const listening = { state: 'listening', detail: null };
  assert.deepEqual(step(listening, { type: 'input_audio_buffer.speech_started' }, LISTEN_FLAGS),
    { state: 'hearing', detail: null });
  assert.deepEqual(step({ state: 'hearing', detail: null }, { type: 'input_audio_buffer.speech_stopped' }, LISTEN_FLAGS),
    { state: 'thinking', detail: null });
  assert.deepEqual(step({ state: 'thinking', detail: null }, { type: 'response.created' }, LISTEN_FLAGS),
    { state: 'thinking', detail: null });
  // a follow-up response.created belongs to the running tool turn
  assert.deepEqual(step({ state: 'tool', detail: 'web_search' }, { type: 'response.created' }, LISTEN_FLAGS),
    { state: 'tool', detail: 'web_search' });
  assert.deepEqual(
    step(listening,
      { type: 'response.function_call_arguments.done', name: 'web_search', call_id: 'c1' }, LISTEN_FLAGS),
    { state: 'tool', detail: 'web_search' },
  );
  assert.deepEqual(
    step({ state: 'tool', detail: 'web_search' },
      { type: 'conversation.item.create', item: { type: 'function_call_output', call_id: 'c1' } }, LISTEN_FLAGS),
    { state: 'thinking', detail: null },
  );
  // non-output conversation items change nothing
  assert.deepEqual(
    step(listening, { type: 'conversation.item.create', item: { type: 'message', role: 'user' } }, LISTEN_FLAGS),
    listening,
  );
  assert.deepEqual(
    step(listening, { type: 'response.output_audio_transcript.delta', item_id: 'a', delta: 'Quack' }, LISTEN_FLAGS),
    { state: 'speaking', detail: null },
  );
  assert.deepEqual(
    step(listening, { type: 'response.audio_transcript.delta', item_id: 'a', delta: 'Quack' }, LISTEN_FLAGS),
    { state: 'speaking', detail: null },
  );
  assert.deepEqual(step(listening, { type: 'output_audio_buffer.started' }, LISTEN_FLAGS),
    { state: 'speaking', detail: null });
  assert.deepEqual(step({ state: 'speaking', detail: null }, { type: 'output_audio_buffer.stopped' }, LISTEN_FLAGS),
    { state: 'listening', detail: null });
  assert.deepEqual(step({ state: 'speaking', detail: null }, { type: 'output_audio_buffer.cleared' }, LISTEN_FLAGS),
    { state: 'listening', detail: null });
  assert.deepEqual(step(listening, { type: 'response.cancelled' }, LISTEN_FLAGS),
    { state: 'listening', detail: null });
  // unrelated events (transcription, rate limits, backend notices…) change nothing
  assert.deepEqual(
    step({ state: 'speaking', detail: null },
      { type: 'conversation.item.input_audio_transcription.completed', transcript: 'hi' }, LISTEN_FLAGS),
    { state: 'speaking', detail: null },
  );
  assert.deepEqual(
    step({ state: 'thinking', detail: null }, { type: 'rate_limits.updated' }, LISTEN_FLAGS),
    { state: 'thinking', detail: null },
  );
});

test('pure transitions: response.done', () => {
  const listening = { state: 'listening', detail: null };
  const done = (output, flags) =>
    step({ state: 'speaking', detail: null }, { type: 'response.done', response: { output } }, flags);

  // No audio-buffer events in this session: response.done returns to idle.
  assert.deepEqual(done([{ type: 'message', role: 'assistant' }], LISTEN_FLAGS), listening);
  assert.deepEqual(done([{ type: 'function_call' }, { type: 'message', role: 'assistant' }], LISTEN_FLAGS),
    listening);

  // Tool-only response: keep the current state — the tool result is still coming.
  assert.deepEqual(
    step({ state: 'tool', detail: 'web_search' },
      { type: 'response.done', response: { output: [{ type: 'function_call', name: 'web_search' }] } },
      LISTEN_FLAGS),
    { state: 'tool', detail: 'web_search' },
  );

  // Audio-buffer session: the audio is still playing when response.done fires;
  // output_audio_buffer.stopped ends the speaking state instead.
  const audioSession = { ...LISTEN_FLAGS, sawOutputAudioBuffer: true };
  assert.deepEqual(done([{ type: 'message', role: 'assistant' }], audioSession),
    { state: 'speaking', detail: null });
});

test('pure transitions: idle respects mute, PTT and the held talk button', () => {
  const idleVia = (event, flags) => step({ state: 'speaking', detail: null }, event, flags);

  assert.deepEqual(idleVia({ type: 'output_audio_buffer.stopped' }, LISTEN_FLAGS),
    { state: 'listening', detail: null });
  assert.deepEqual(idleVia({ type: 'output_audio_buffer.stopped' }, MUTED_FLAGS),
    { state: 'muted', detail: null });
  assert.deepEqual(idleVia({ type: 'output_audio_buffer.cleared' }, PTT_FLAGS),
    { state: 'ptt-idle', detail: null });
  assert.deepEqual(idleVia({ type: 'response.cancelled' }, PTT_FLAGS),
    { state: 'ptt-idle', detail: null });

  // muted wins over ptt-idle; holding the talk button leaves ptt-idle
  const mutedPTT = { ...MUTED_FLAGS, isPushToTalk: true };
  assert.deepEqual(idleVia({ type: 'response.cancelled' }, mutedPTT), { state: 'muted', detail: null });
  assert.deepEqual(idleVia({ type: 'response.cancelled' }, { ...PTT_FLAGS, talkButtonHeld: true }),
    { state: 'listening', detail: null });

  // The internal refresh marker recomputes the idle variant from any idle state.
  assert.deepEqual(step({ state: 'listening', detail: null }, { type: 'ducke.refresh_idle' }, MUTED_FLAGS),
    { state: 'muted', detail: null });
  assert.deepEqual(step({ state: 'muted', detail: null }, { type: 'ducke.refresh_idle' }, PTT_FLAGS),
    { state: 'ptt-idle', detail: null });
  assert.deepEqual(
    step({ state: 'ptt-idle', detail: null },
      { type: 'ducke.refresh_idle' }, { ...PTT_FLAGS, talkButtonHeld: true }),
    { state: 'listening', detail: null },
  );
});

test('labels: states are short, tool names are formatted', () => {
  assert.equal(label('listening'), 'Listening');
  assert.equal(label('hearing'), 'Hearing you…');
  assert.equal(label('thinking'), 'Thinking…');
  assert.equal(label('speaking'), 'Speaking');
  assert.equal(label('muted'), 'Microphone muted');
  assert.equal(label('ptt-idle'), 'Hold to talk');
  assert.equal(label('tool', 'web_search'), 'Using Web Search…');
  assert.equal(label('tool', 'get_current_weather'), 'Using Weather…');
  assert.equal(label('tool', 'custom_thing'), 'Using Custom Thing…');
});

test('page: a voice turn with output_audio_buffer events ends when the audio stops', async () => {
  const { browser, dc } = await connectPage();
  const status = () => browser.document.getElementById('status-text').textContent;
  const state = () => browser.document.getElementById('status-indicator').dataset.state;

  assert.equal(status(), 'Listening');
  assert.equal(state(), 'listening');
  dc.openaiSends({ type: 'input_audio_buffer.speech_started' });
  assert.equal(status(), 'Hearing you…');
  assert.equal(state(), 'hearing');
  dc.openaiSends({ type: 'input_audio_buffer.speech_stopped' });
  assert.equal(status(), 'Thinking…');
  assert.equal(state(), 'thinking');
  dc.openaiSends({ type: 'response.created' });
  assert.equal(status(), 'Thinking…');
  dc.openaiSends({ type: 'output_audio_buffer.started' });
  assert.equal(status(), 'Speaking');
  assert.equal(state(), 'speaking');
  dc.openaiSends({ type: 'response.output_audio_transcript.delta', item_id: 'a', delta: 'Quack' });
  assert.equal(status(), 'Speaking');
  // Audio is still playing: response.done must not end the speaking state.
  dc.openaiSends({ type: 'response.done', response: { output: [{ type: 'message', role: 'assistant' }] } });
  assert.equal(status(), 'Speaking');
  dc.openaiSends({ type: 'output_audio_buffer.stopped' });
  assert.equal(status(), 'Listening');
  assert.equal(state(), 'listening');
});

test('page: a voice turn without output_audio_buffer events returns to Listening on response.done', async () => {
  const { browser, dc } = await connectPage();
  const status = () => browser.document.getElementById('status-text').textContent;

  dc.openaiSends({ type: 'input_audio_buffer.speech_started' });
  assert.equal(status(), 'Hearing you…');
  dc.openaiSends({ type: 'input_audio_buffer.speech_stopped' });
  assert.equal(status(), 'Thinking…');
  dc.openaiSends({ type: 'response.output_audio_transcript.delta', item_id: 'a', delta: 'Quack' });
  assert.equal(status(), 'Speaking');
  dc.openaiSends({ type: 'response.done', response: { output: [{ type: 'message', role: 'assistant' }] } });
  assert.equal(status(), 'Listening');
});

test('page: a tool turn shows the tool, then thinking on the backend result', async () => {
  const { browser, ws, dc } = await connectPage();
  const status = () => browser.document.getElementById('status-text').textContent;
  const state = () => browser.document.getElementById('status-indicator').dataset.state;

  dc.openaiSends({
    type: 'response.function_call_arguments.done',
    name: 'web_search', call_id: 'call_1', arguments: '{"query":"ducks"}',
  });
  assert.equal(status(), 'Using Web Search…');
  assert.equal(state(), 'tool');

  // The backend relays the tool result to OpenAI; main.js sees the same event.
  await ws.serverSends({
    type: 'conversation.item.create',
    item: { type: 'function_call_output', call_id: 'call_1', output: 'ducks quack' },
  });
  assert.equal(status(), 'Thinking…');
  assert.equal(state(), 'thinking');

  dc.openaiSends({ type: 'response.output_audio_transcript.delta', item_id: 'b', delta: 'Ducks quack.' });
  assert.equal(status(), 'Speaking');
  assert.equal(state(), 'speaking');
});

test('page: mute and PTT re-evaluate the idle state', async () => {
  const { browser, dc } = await connectPage();
  const status = () => browser.document.getElementById('status-text').textContent;
  const state = () => browser.document.getElementById('status-indicator').dataset.state;

  browser.evaluate('toggleMute')();
  assert.equal(status(), 'Microphone muted');
  assert.equal(state(), 'muted');
  browser.evaluate('toggleMute')();
  assert.equal(status(), 'Listening');
  assert.equal(state(), 'listening');

  browser.evaluate('togglePushToTalk')();
  assert.equal(status(), 'Hold to talk');
  assert.equal(state(), 'ptt-idle');
  browser.evaluate('startTalking')();
  assert.equal(status(), 'Listening');
  assert.equal(state(), 'listening');
  dc.openaiSends({ type: 'input_audio_buffer.speech_started' });
  assert.equal(status(), 'Hearing you…');
  assert.equal(state(), 'hearing');
  dc.openaiSends({ type: 'input_audio_buffer.speech_stopped' });
  assert.equal(status(), 'Thinking…');
  dc.openaiSends({ type: 'response.cancelled' });
  // Still holding the talk button, so the idle state is plain listening.
  assert.equal(status(), 'Listening');
  browser.evaluate('stopTalking')();
  assert.equal(status(), 'Hold to talk');
  assert.equal(state(), 'ptt-idle');
});

test('page: muting mid-conversation does not clobber a non-idle state', async () => {
  const { browser, dc } = await connectPage();
  const status = () => browser.document.getElementById('status-text').textContent;

  dc.openaiSends({ type: 'output_audio_buffer.started' });
  assert.equal(status(), 'Speaking');
  browser.evaluate('toggleMute')();
  assert.equal(status(), 'Speaking');
  dc.openaiSends({ type: 'output_audio_buffer.stopped' });
  // The idle state computed on the way out reflects the muted mic.
  assert.equal(status(), 'Microphone muted');
});

test('page: the indicator data-state and document.title follow the conversation', async () => {
  const browser = makeBrowser();
  browser.document.title = 'DUCK-E - Voice Assistant';
  const { dc } = await connectPage(browser);
  const state = () => browser.document.getElementById('status-indicator').dataset.state;
  const inlineState = () => browser.document.getElementById('status-indicator-inline').dataset.state;

  assert.equal(state(), 'listening');
  assert.equal(inlineState(), 'listening');
  assert.equal(browser.document.title, 'DUCK-E — Listening');

  dc.openaiSends({ type: 'input_audio_buffer.speech_started' });
  assert.equal(state(), 'hearing');
  assert.equal(inlineState(), 'hearing');
  assert.equal(browser.document.title, 'DUCK-E — Hearing you…');

  dc.openaiSends({ type: 'response.cancelled' });
  assert.equal(state(), 'listening');

  // Disconnecting drops the state marker and restores the original title.
  await browser.evaluate('toggleConnection')();
  assert.equal(browser.document.getElementById('status-text').textContent, 'Ready to Connect');
  assert.equal(state(), undefined);
  assert.equal(inlineState(), undefined);
  assert.equal(browser.document.title, 'DUCK-E - Voice Assistant');
});
