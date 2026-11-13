import test from 'node:test';
import assert from 'node:assert/strict';
import { connectPage, loadPage, makeBrowser } from './harness.mjs';

const submit = (browser, value) => {
  const input = browser.document.getElementById('text-input');
  input.value = value;
  browser.document.getElementById('text-form').fire('submit');
};

const userMessages = (browser) => browser.snapshot('transcriptMessages')
  .filter((message) => message.role === 'user');

test('page: submits a typed message and clears the input', async () => {
  const { browser, dc } = await connectPage();

  submit(browser, 'what is 2+2');

  assert.equal(dc.sent.at(-2).type, 'conversation.item.create');
  assert.equal(dc.sent.at(-2).item.role, 'user');
  assert.equal(dc.sent.at(-2).item.content[0].type, 'input_text');
  assert.equal(dc.sent.at(-2).item.content[0].text, 'what is 2+2');
  assert.match(dc.sent.at(-2).item.id, /^msg_[a-z0-9]{24}$/);
  assert.equal(dc.sent.at(-1).type, 'response.create');
  assert.equal(userMessages(browser).length, 1);
  assert.equal(userMessages(browser)[0].content, 'what is 2+2');
  assert.equal(browser.document.getElementById('text-input').value, '');
});

test('page: a conversation item echo does not duplicate the typed message', async () => {
  const { browser, dc } = await connectPage();

  submit(browser, 'what is 2+2');
  const id = dc.sent.at(-2).item.id;
  dc.openaiSends({
    type: 'conversation.item.added',
    item: { id, type: 'message', role: 'user', content: [{ type: 'input_text', text: 'what is 2+2' }] },
  });

  assert.equal(userMessages(browser).length, 1);
  assert.equal(userMessages(browser)[0].itemId, id);
});

test('page: whitespace-only input sends nothing', async () => {
  const { browser, dc } = await connectPage();

  submit(browser, '   \n\t ');

  assert.equal(dc.sent.length, 0);
  assert.equal(userMessages(browser).length, 0);
});

test('page: disconnected text submission is harmless and sends no socket traffic', () => {
  const browser = loadPage();

  assert.doesNotThrow(() => submit(browser, 'hello'));
  assert.equal(browser.sockets.length, 0);
});

test('page: an active response is cancelled before a typed turn starts', async () => {
  const { browser, dc } = await connectPage();
  dc.openaiSends({ type: 'response.created' });

  submit(browser, 'continue with text');

  assert.deepEqual(dc.sent.map((message) => message.type), [
    'response.cancel',
    'output_audio_buffer.clear',
    'conversation.item.create',
    'response.create',
  ]);
});

test('page: typed HTML is escaped for display but sent raw', async () => {
  const { browser, dc } = await connectPage();
  const raw = '<img src=x onerror=alert(1)>';

  submit(browser, raw);

  const message = userMessages(browser)[0];
  assert.match(message.content, /&lt;img/);
  assert.doesNotMatch(message.content, /<img/);
  assert.equal(dc.sent.at(-2).item.content[0].text, raw);
});

test('page: PTT ignores Space in text inputs but responds to Space on the body', async () => {
  const browser = makeBrowser();
  browser.store.set('duck-e-ptt', 'true');
  const connected = await connectPage(browser);

  assert.equal(connected.browser.microphone.enabled, false);
  const inputEvent = browser.document.fire('keydown', {
    code: 'Space',
    target: { tagName: 'INPUT' },
  });
  assert.equal(connected.browser.microphone.enabled, false);
  assert.equal(inputEvent.defaultPrevented, undefined);

  const bodyEvent = browser.document.fire('keydown', {
    code: 'Space',
    target: { tagName: 'BODY' },
  });
  assert.equal(connected.browser.microphone.enabled, true);
  assert.equal(bodyEvent.defaultPrevented, true);
});

test('page: a typed turn is included in the memory turn-done message', async () => {
  const { browser, dc, ws } = await connectPage();
  submit(browser, 'remember this typed turn');
  dc.openaiSends({
    type: 'response.output_audio_transcript.done',
    item_id: 'assistant_1',
    transcript: 'I will remember it.',
  });
  dc.openaiSends({ type: 'response.done', response: { output: [] } });

  assert.deepEqual(ws.sent.at(-1), {
    type: 'ducke.turn_done',
    user_text: 'remember this typed turn',
    assistant_text: 'I will remember it.',
  });
});
