// The browser tells the backend its IANA timezone (as the "tz" query
// parameter on the session WebSocket) so the model can be told the user's
// local date and time. The parameter must ride along with the OAuth token
// when one is present, and must never break the connection URL.
import test from 'node:test';
import assert from 'node:assert/strict';
import { connectPage, INIT_MESSAGE, makeBrowser } from './harness.mjs';

test('page: the session WebSocket URL carries a non-empty tz parameter', async () => {
  const { ws } = await connectPage();

  const tz = new URL(ws.url).searchParams.get('tz');
  assert.equal(typeof tz, 'string', `tz must be a string, got ${tz}`);
  assert.notEqual(tz, '', 'tz must not be empty');
});

// Like connectPage(), but with OAuth enabled and state seeded into
// localStorage first so the token parameter is present alongside tz. The
// keys come from main.js's own STORAGE_KEYS, read back through the vm
// context; loadOAuthState() only runs when OAUTH_CONFIGURED is true.
async function connectPageWithToken() {
  const browser = makeBrowser();
  browser.context.OAUTH_CONFIGURED = true;
  browser.load('ducke.js');
  browser.load('main.js');
  const { TOKEN, USER_INFO } = browser.evaluate('STORAGE_KEYS');
  browser.store.set(TOKEN, 'fake.jwt.token');
  browser.store.set(USER_INFO, JSON.stringify({ name: 'Dev Duck' }));
  browser.document.fire('DOMContentLoaded');
  const pending = browser.evaluate('toggleConnection')();
  await new Promise((resolve) => setTimeout(resolve, 0));
  const ws = browser.sockets.at(-1);
  await ws.serverSends(INIT_MESSAGE);
  const dc = browser.peers.at(-1).channels[0];
  dc.open();
  await pending;
  return { browser, ws, dc };
}

test('page: the tz parameter coexists with the token parameter', async () => {
  const { ws } = await connectPageWithToken();

  const params = new URL(ws.url).searchParams;
  assert.equal(params.get('token'), 'fake.jwt.token');
  const tz = params.get('tz');
  assert.equal(typeof tz, 'string');
  assert.notEqual(tz, '');
});

test('page: the session URL still points at the /session endpoint', async () => {
  const { ws } = await connectPage();

  const url = new URL(ws.url);
  assert.equal(url.pathname, '/session');
});
