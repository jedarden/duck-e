// Mid-conversation voice change behaviour. session.update cannot change the
// voice once assistant audio exists (verified live 2026-11-12,
// docs/research/realtime-api-verified-2026-11-12.md), so the backend mints a
// new ephemeral key and sends ducke.reinit; the browser tears down the old
// RTCPeerConnection, connects a new one authenticated with the new key and
// replays the transcript as text items so the conversation survives. The new
// voice then confirms itself via per-response instructions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { connectPage } from './harness.mjs';

const REINIT = {
  type: 'ducke.reinit',
  config: { client_secret: { value: 'ek2' }, model: 'gpt-realtime-2' },
  voice: 'cedar',
  init: [],
};

// A connected page whose transcript holds one spoken user turn and one
// assistant reply, built the way the page builds them from Realtime events.
async function connectWithTurns() {
  const page = await connectPage();
  const { dc } = page;
  dc.openaiSends({ type: 'conversation.item.added', item: { id: 'item_u', role: 'user' } });
  dc.openaiSends({
    type: 'conversation.item.input_audio_transcription.completed',
    item_id: 'item_u',
    transcript: 'Remember the number seven.',
  });
  dc.openaiSends({
    type: 'response.output_audio_transcript.delta', item_id: 'item_a', delta: 'Seven it is.',
  });
  dc.openaiSends({
    type: 'response.output_audio_transcript.done', item_id: 'item_a', transcript: 'Seven it is.',
  });
  return page;
}

test('voice change: reinit replaces the peer, authenticates with the new key and replays the transcript', async () => {
  const { browser, ws, pc, dc } = await connectWithTurns();

  // A tool card and a system message join the transcript; neither may be
  // replayed to the new session.
  dc.openaiSends({
    type: 'response.function_call_arguments.done',
    name: 'get_current_weather', call_id: 'call_1', arguments: '{"location":"pond"}',
  });
  browser.evaluate('handleWebRTCMessage')({ message: { type: 'error', error: { message: 'noise' } } });

  await ws.serverSends(REINIT);

  // The old peer is closed and a second one was created.
  assert.equal(browser.peers.length, 2);
  assert.equal(pc.connectionState, 'closed');
  const pc2 = browser.peers[1];

  // The new WebRTC offer was posted with the new ephemeral key.
  assert.equal(browser.fetchCalls.length, 2);
  assert.equal(browser.fetchCalls[1].init.headers.Authorization, 'Bearer ek2');

  // Opening the new channel replays the conversation in order: user turn,
  // assistant turn, then the new-voice confirmation. Tool cards and system
  // messages are skipped.
  const dc2 = pc2.channels[0];
  dc2.open();
  assert.deepEqual(dc2.sent, [
    {
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'user',
        content: [{ type: 'input_text', text: 'Remember the number seven.' }],
      },
    },
    {
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'assistant',
        content: [{ type: 'output_text', text: 'Seven it is.' }],
      },
    },
    {
      type: 'response.create',
      response: { instructions: 'In one short sentence, confirm this is your new voice.' },
    },
  ]);

  // The transcript records the change.
  const systems = browser.snapshot('transcriptMessages').filter((m) => m.role === 'system');
  assert.equal(systems.at(-1).content, 'Voice changed to cedar');
});

test('voice change: a muted page is still muted after the reinit', async () => {
  const { browser, ws } = await connectWithTurns();
  browser.evaluate('toggleMute')();
  await ws.serverSends(REINIT);

  // openRTC re-enables the track when the new channel opens; the
  // ducke.voice_changed handler must re-apply the page's mute on top of that.
  browser.peers[1].channels[0].open();
  assert.equal(browser.microphone.enabled, false);
});

test('voice change: push-to-talk keeps the mic off after the reinit', async () => {
  const { browser, ws } = await connectWithTurns();
  browser.evaluate('togglePushToTalk')();
  await ws.serverSends(REINIT);

  // In PTT mode with the talk button not held, the fresh channel's mic must
  // end up disabled again, not open.
  browser.peers[1].channels[0].open();
  assert.equal(browser.microphone.enabled, false);
});

test('voice change: the replay is capped at the last 20 turns', async () => {
  const { browser, ws } = await connectWithTurns();
  const dc = browser.peers[0].channels[0];
  for (let i = 0; i < 25; i += 1) {
    const id = `item_u${i}`;
    dc.openaiSends({ type: 'conversation.item.added', item: { id, role: 'user' } });
    dc.openaiSends({
      type: 'conversation.item.input_audio_transcription.completed',
      item_id: id,
      transcript: `turn ${i}`,
    });
  }

  await ws.serverSends(REINIT);
  const dc2 = browser.peers[1].channels[0];
  dc2.open();

  // 27 qualifying turns existed (the original pair plus these 25); the replay
  // keeps the last 20, so turn 0..4 are dropped and "turn 5" leads.
  const items = dc2.sent.filter((m) => m.type === 'conversation.item.create');
  assert.equal(items.length, 20);
  assert.equal(items[0].item.content[0].text, 'turn 5');
  assert.equal(items.at(-1).item.content[0].text, 'turn 24');
});
