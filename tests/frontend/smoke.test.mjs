// Seed behaviour tests for the browser scripts. They pin what works today and
// show the three ways to drive the page: the bridge alone (connectBridge), the
// page without a session (loadPage), and the full connect flow (connectPage).
import test from 'node:test';
import assert from 'node:assert/strict';
import { connectBridge, connectPage, loadPage } from './harness.mjs';

test('bridge: a completed tool call from OpenAI is relayed to the backend socket', async () => {
  const { ws, dc } = await connectBridge();
  dc.openaiSends({
    type: 'response.function_call_arguments.done',
    name: 'web_search', call_id: 'call_1', arguments: '{"query":"ducks"}',
  });
  assert.deepEqual(ws.sent.at(-1), {
    type: 'response.function_call_arguments.done',
    name: 'web_search', call_id: 'call_1', arguments: '{"query":"ducks"}',
  });
});

test('bridge: a tool result from the backend goes to OpenAI followed by response.create', async () => {
  const { ws, dc } = await connectBridge();
  await ws.serverSends({
    type: 'conversation.item.create',
    item: { type: 'function_call_output', call_id: 'call_1', output: 'sunny' },
  });
  assert.deepEqual(dc.sent.map((m) => m.type), ['conversation.item.create', 'response.create']);
  assert.equal(dc.sent[0].item.output, 'sunny');
});

test('bridge: the microphone track is enabled once the data channel opens', async () => {
  const { browser } = await connectBridge();
  assert.equal(browser.microphone.enabled, true);
});

test('page: loads disconnected and shows "Ready to Connect"', () => {
  const browser = loadPage();
  assert.equal(browser.document.getElementById('status-text').textContent, 'Ready to Connect');
  assert.equal(browser.document.getElementById('button-text').textContent, 'Connect');
});

test('page: a Realtime API error event is added to the transcript as a system message', () => {
  const browser = loadPage();
  browser.evaluate('handleWebRTCMessage')({ message: { type: 'error', error: { message: 'boom' } } });
  const last = browser.snapshot('transcriptMessages').at(-1);
  assert.equal(last.role, 'system');
  assert.match(last.content, /boom/);
});

test('page: toggleConnection() reaches the connected state', async () => {
  const { browser, ws } = await connectPage();
  assert.equal(ws.url.startsWith('ws://test.invalid/session'), true);
  assert.equal(browser.document.getElementById('status-text').textContent, 'Connected - DUCK-E is listening');
  assert.equal(browser.document.getElementById('button-text').textContent, 'Disconnect');
  assert.equal(browser.evaluate('connectionStatus'), true);
});

test('page: assistant audio transcript deltas stream into one transcript message', async () => {
  const { browser, dc } = await connectPage();
  dc.openaiSends({ type: 'response.output_audio_transcript.delta', item_id: 'item_a', delta: 'Quack' });
  dc.openaiSends({ type: 'response.output_audio_transcript.delta', item_id: 'item_a', delta: ' quack' });
  dc.openaiSends({ type: 'response.output_audio_transcript.done', item_id: 'item_a', transcript: 'Quack quack.' });
  const assistant = browser.snapshot('transcriptMessages').filter((m) => m.role === 'assistant');
  assert.equal(assistant.length, 1);
  assert.equal(assistant[0].content, 'Quack quack.');
  assert.equal(assistant[0].streaming, false);
});
