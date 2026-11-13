// Zero-dependency browser stand-in for DUCK-E's static scripts.
//
// Loads app/website_files/static/{ducke.js,main.js} into a node:vm context with
// a fake DOM, WebSocket, RTCPeerConnection, fetch and getUserMedia, so tests
// drive the real code paths (connect, relay, transcript, UI state) instead of
// grepping the source. No npm install: only Node built-ins.
//
// Run:  node --test "tests/frontend/*.test.mjs"
//
// Things to know when writing a test:
// - Top-level `const`/`let` in main.js are script-scoped, not properties of the
//   context object. Reach them with browser.evaluate('name'), e.g.
//   browser.evaluate('handleWebRTCMessage')({ message: {...} }).
// - Objects created inside the vm belong to another realm, so
//   assert.deepStrictEqual against a literal fails on prototype identity.
//   Compare fields, or use browser.snapshot('expr') which JSON-round-trips.
// - ws.sent / dc.sent hold parsed objects from this realm; deepEqual works.
// - Elements are created on demand: document.getElementById(id) always returns
//   an element, so a missing id in chat.html is NOT caught here. Keep the
//   markup assertions in the pytest files for that.
import vm from 'node:vm';
import fs from 'node:fs';
import path from 'node:path';

export const STATIC_DIR = process.env.DUCKE_STATIC_DIR
  || path.resolve(import.meta.dirname, '../../app/website_files/static');

export function makeEl(id) {
  const classes = new Set();
  return {
    id, tagName: '', textContent: '', innerHTML: '', value: '', title: '',
    style: {}, dataset: {}, attributes: {}, disabled: false, checked: false, hidden: false,
    children: [], listeners: {}, scrollTop: 0, scrollHeight: 0, offsetWidth: 0,
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      toggle: (c) => (classes.has(c) ? (classes.delete(c), false) : (classes.add(c), true)),
      contains: (c) => classes.has(c),
      get list() { return [...classes]; },
    },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return name in this.attributes ? this.attributes[name] : null; },
    removeAttribute(name) { delete this.attributes[name]; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    // test helper: dispatch a DOM event to this element's listeners
    fire(type, ev = {}) {
      const event = { type, target: this, preventDefault() { this.defaultPrevented = true; }, ...ev };
      (this.listeners[type] || []).forEach((fn) => fn(event));
      return event;
    },
    appendChild(child) { this.children.push(child); return child; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    play() { return Promise.resolve(); },
    focus() {},
    blur() {},
  };
}

export function makeBrowser({ search = '' } = {}) {
  const els = new Map();
  const docListeners = {};
  const sockets = [];
  const peers = [];
  const store = new Map();
  const alerts = [];

  const document = {
    title: 'DUCK-E',
    body: makeEl('body'),
    activeElement: null,
    getElementById(id) { if (!els.has(id)) els.set(id, makeEl(id)); return els.get(id); },
    querySelector(sel) { return this.getElementById(`qs:${sel}`); },
    querySelectorAll() { return []; },
    createElement(tag) { const el = makeEl(`new:${tag}`); el.tagName = tag.toUpperCase(); return el; },
    addEventListener(type, fn) { (docListeners[type] ||= []).push(fn); },
    // test helper: dispatch a document-level event (DOMContentLoaded, keydown, ...)
    fire(type, ev = {}) {
      const event = { type, target: document.body, preventDefault() { this.defaultPrevented = true; }, ...ev };
      (docListeners[type] || []).forEach((fn) => fn(event));
      return event;
    },
  };

  class FakeWebSocket {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    constructor(url) { this.url = url; this.readyState = 1; this.sent = []; sockets.push(this); }
    send(data) { this.sent.push(JSON.parse(data)); }
    close() { this.readyState = 3; if (this.onclose) this.onclose({ code: 1000 }); }
    // test helper: deliver a DUCK-E backend -> browser message
    async serverSends(obj) { await this.onmessage({ data: JSON.stringify(obj) }); }
    // test helper: the backend closes the socket
    serverCloses(code = 1008, reason = '') { this.readyState = 3; if (this.onclose) this.onclose({ code, reason }); }
  }

  class FakeDataChannel {
    constructor(label) { this.label = label; this.readyState = 'connecting'; this.sent = []; this.listeners = {}; }
    send(data) { this.sent.push(JSON.parse(data)); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    // test helper: open the channel the way the browser would
    open() { this.readyState = 'open'; if (this.onopen) this.onopen({}); }
    // test helper: deliver an OpenAI -> browser event on the data channel
    openaiSends(obj) { (this.listeners.message || []).forEach((fn) => fn({ data: JSON.stringify(obj) })); }
  }

  class FakePeerConnection {
    constructor() { this.connectionState = 'new'; this.channels = []; this.tracks = []; peers.push(this); }
    addTrack(track) { this.tracks.push(track); }
    createDataChannel(label) { const dc = new FakeDataChannel(label); this.channels.push(dc); return dc; }
    async createOffer() { return { type: 'offer', sdp: 'fake-offer' }; }
    async setLocalDescription() {}
    async setRemoteDescription() {}
    close() { this.connectionState = 'closed'; }
    // test helper: simulate a connection state change
    setState(state) { this.connectionState = state; if (this.onconnectionstatechange) this.onconnectionstatechange({}); }
  }

  const microphone = { kind: 'audio', enabled: true, stopped: false, stop() { this.stopped = true; } };
  const fetchCalls = [];

  const context = vm.createContext({
    console: { log() {}, info() {}, warn() {}, error() {} },
    document,
    navigator: {
      mediaDevices: { getUserMedia: async () => ({ getTracks: () => [microphone] }) },
      permissions: { query: async () => ({ state: 'prompt' }) },
    },
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => { store.set(k, String(v)); },
      removeItem: (k) => { store.delete(k); },
    },
    WebSocket: FakeWebSocket,
    RTCPeerConnection: FakePeerConnection,
    fetch: async (url, init) => {
      fetchCalls.push({ url: String(url), init });
      return { ok: true, status: 200, text: async () => 'fake-answer', json: async () => ({}) };
    },
    performance: { now: () => Date.now() },
    setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams,
    alert: (msg) => { alerts.push(String(msg)); },
    // Globals chat.html defines inline before loading the scripts.
    socketUrl: 'ws://test.invalid/session',
    APP_VERSION: 'test',
    APP_MODEL: 'gpt-realtime-2',
    OAUTH_CONFIGURED: false,
    location: { protocol: 'http:', host: 'test.invalid', search, pathname: '/', href: `http://test.invalid/${search}` },
    history: { replaceState() {} },
  });
  context.window = context;

  const load = (file) => vm.runInContext(
    fs.readFileSync(path.join(STATIC_DIR, file), 'utf8'), context, { filename: file },
  );
  const evaluate = (code) => vm.runInContext(code, context);
  const snapshot = (code) => JSON.parse(JSON.stringify(evaluate(code)));

  return {
    context, document, els, sockets, peers, alerts, store, microphone, fetchCalls,
    load, evaluate, snapshot,
  };
}

export const INIT_MESSAGE = {
  type: 'ducke.init',
  config: { client_secret: { value: 'ek_fake' }, model: 'gpt-realtime-2' },
  init: [],
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

// ducke.js only: bring a ducke.WebRTC instance to "data channel open".
// Returns the instance, its fake backend socket, its fake OpenAI data channel
// and the list of messages handed to onMessage.
export async function connectBridge(browser = makeBrowser()) {
  browser.load('ducke.js');
  const rtc = browser.evaluate('new ducke.WebRTC("ws://test.invalid/session")');
  const messages = [];
  rtc.onMessage = (ev) => messages.push(ev.message);
  const connected = rtc.connect();
  await tick();
  const ws = browser.sockets.at(-1);
  await ws.serverSends(INIT_MESSAGE);
  const pc = browser.peers.at(-1);
  const dc = pc.channels[0];
  dc.open();
  await connected;
  return { browser, rtc, ws, pc, dc, messages };
}

// ducke.js + main.js: load the page scripts, fire DOMContentLoaded and click
// through the real toggleConnection() until the UI reports "connected".
export async function connectPage(browser = makeBrowser()) {
  browser.load('ducke.js');
  browser.load('main.js');
  browser.document.fire('DOMContentLoaded');
  const pending = browser.evaluate('toggleConnection')();
  await tick();
  const ws = browser.sockets.at(-1);
  await ws.serverSends(INIT_MESSAGE);
  const pc = browser.peers.at(-1);
  const dc = pc.channels[0];
  dc.open();
  await pending;
  return { browser, ws, pc, dc };
}

// Load the page scripts without connecting.
export function loadPage(browser = makeBrowser()) {
  browser.load('ducke.js');
  browser.load('main.js');
  browser.document.fire('DOMContentLoaded');
  return browser;
}
