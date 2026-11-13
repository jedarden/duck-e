// Server notices: when the DUCK-E backend refuses or ends a session
// (connection limit, hourly cap, circuit breaker, duration limit, budget,
// init error) the user must see the reason in the transcript and the status
// line instead of a silent drop back to "Ready to Connect".
//
// Backend message shapes verified against app/main.py handle_media_stream:
//   connection_limit_exceeded { reason, message, retry_after_seconds }
//   service_unavailable       { error|message, reason, reset_time }
//   error                     { error: "<plain string>" }
//   session_timeout           { error, duration_seconds }
//   budget_exceeded           { error, session_cost }
import test from 'node:test';
import assert from 'node:assert/strict';
import { connectPage, loadPage, INIT_MESSAGE } from './harness.mjs';

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// The five backend refusal/end message types and the text each must surface.
const NOTICE_CASES = [
  ['connection_limit_exceeded', {
    type: 'connection_limit_exceeded',
    reason: 'concurrent_limit',
    message: 'Maximum concurrent sessions reached',
    retry_after_seconds: 90,
  }, 'Maximum concurrent sessions reached'],
  ['service_unavailable', {
    type: 'service_unavailable',
    error: 'Hourly cost limit reached',
    reason: 'hourly_cost_cap',
    reset_time: '2026-11-12T13:00:00',
  }, 'Hourly cost limit reached'],
  ['error', {
    type: 'error',
    error: 'Failed to initialize session: upstream unavailable',
  }, 'Failed to initialize session: upstream unavailable'],
  ['session_timeout', {
    type: 'session_timeout',
    error: 'Session exceeded maximum duration of 30 minutes',
    duration_seconds: 1800,
  }, 'Session exceeded maximum duration of 30 minutes'],
  ['budget_exceeded', {
    type: 'budget_exceeded',
    error: 'Session budget limit of $5.00 exceeded',
    session_cost: 5.02,
  }, 'Session budget limit of $5.00 exceeded'],
];

test('each backend notice reaches the transcript and the status line, never OpenAI', async () => {
  for (const [noticeType, payload, expectedText] of NOTICE_CASES) {
    const { browser, ws, dc } = await connectPage();

    await ws.serverSends(payload);

    // Never relayed to the OpenAI data channel.
    assert.equal(
      dc.sent.filter((m) => m.type === noticeType).length, 0,
      `${noticeType} must not be forwarded to the data channel`,
    );

    // Shown in the transcript as a system message with the server's text.
    const last = browser.snapshot('transcriptMessages').at(-1);
    assert.equal(last.role, 'system');
    assert.match(last.content, new RegExp(expectedText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));

    // After the backend closes the socket the reason stays in the status
    // line instead of dropping back to "Ready to Connect".
    ws.serverCloses(1008, 'refused');
    const statusText = browser.document.getElementById('status-text').textContent;
    assert.match(statusText, new RegExp(expectedText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.notEqual(statusText, 'Ready to Connect');
    assert.equal(browser.document.getElementById('status-text-inline').textContent, 'Ended');
  }
});

test('a refusal before ducke.init settles toggleConnection() with no alert and shows the retry time', async () => {
  const browser = loadPage();
  const pending = browser.evaluate('toggleConnection')();
  await tick();
  const ws = browser.sockets.at(-1);

  await ws.serverSends({
    type: 'connection_limit_exceeded',
    reason: 'rate_limit',
    message: 'WebSocket connection rate limit exceeded',
    retry_after_seconds: 42,
  });
  ws.serverCloses(1008, 'WebSocket connection limit exceeded');

  // The connect promise must settle (it used to await forever).
  let timer;
  const outcome = await Promise.race([
    pending.then(() => 'settled', () => 'settled'),
    new Promise((resolve) => { timer = setTimeout(() => resolve('timeout'), 500); }),
  ]);
  clearTimeout(timer);
  assert.equal(outcome, 'settled');

  // The refusal is explained by the server, so no microphone alert.
  assert.deepEqual(browser.alerts, []);

  // Retry time is visible in the status line.
  assert.match(browser.document.getElementById('status-text').textContent, /Try again in 42 seconds/);
});

test('notice text is HTML-escaped before it is stored', async () => {
  const { browser, ws } = await connectPage();
  await ws.serverSends({ type: 'error', error: '<img src=x onerror=alert(1)>' });

  const last = browser.snapshot('transcriptMessages').at(-1);
  assert.equal(last.role, 'system');
  assert.match(last.content, /&lt;img/);
  assert.equal(last.content.includes('<img'), false);
  // No execution of the injected handler either.
  assert.deepEqual(browser.alerts, []);
});

test('a function_call_output from the backend is still relayed to OpenAI with response.create', async () => {
  const { ws, dc } = await connectPage();
  await ws.serverSends({
    type: 'conversation.item.create',
    item: { type: 'function_call_output', call_id: 'call_1', output: 'sunny' },
  });
  assert.deepEqual(dc.sent.map((m) => m.type), ['conversation.item.create', 'response.create']);
  assert.equal(dc.sent[0].item.output, 'sunny');
});

test('a new connection after a notice shows the normal connecting and connected states', async () => {
  const browser = loadPage();

  // First attempt: refused by the server.
  const refused = browser.evaluate('toggleConnection')();
  await tick();
  const deadSocket = browser.sockets.at(-1);
  await deadSocket.serverSends({ type: 'service_unavailable', error: 'System under high load' });
  deadSocket.serverCloses(1013, 'Service temporarily unavailable');
  await refused.then(() => {}, () => {});
  assert.match(browser.document.getElementById('status-text').textContent, /System under high load/);

  // Second attempt: the notice is cleared and the normal states return.
  const retry = browser.evaluate('toggleConnection')();
  assert.equal(browser.document.getElementById('status-text').textContent, 'Connecting to DUCK-E...');
  await tick();
  const ws = browser.sockets.at(-1);
  await ws.serverSends(INIT_MESSAGE);
  const dc = browser.peers.at(-1).channels[0];
  dc.open();
  await retry;
  // The status line shows the live conversation state; idle and unmuted it
  // reads "Listening" (see conversation_state.test.mjs).
  assert.equal(browser.document.getElementById('status-text').textContent, 'Listening');
});
