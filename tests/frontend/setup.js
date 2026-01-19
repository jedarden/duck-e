/**
 * Vitest setup file for DUCK-E frontend tests.
 *
 * This file runs before each test file and sets up the testing environment.
 */

import { vi } from 'vitest';

// Mock WebRTC and audio APIs that don't exist in jsdom
global.MediaStream = vi.fn().mockImplementation(() => ({
  getTracks: () => [],
  getAudioTracks: () => [],
  getVideoTracks: () => [],
}));

global.MediaRecorder = vi.fn().mockImplementation(() => ({
  start: vi.fn(),
  stop: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  ondataavailable: null,
  onstop: null,
  state: 'inactive',
}));

// Mock AudioContext
global.AudioContext = vi.fn().mockImplementation(() => ({
  state: 'running',
  suspend: vi.fn().mockResolvedValue(undefined),
  resume: vi.fn().mockResolvedValue(undefined),
  close: vi.fn().mockResolvedValue(undefined),
  createMediaStreamSource: vi.fn().mockReturnValue({
    connect: vi.fn(),
    disconnect: vi.fn(),
  }),
  createGain: vi.fn().mockReturnValue({
    connect: vi.fn(),
    gain: { value: 1 },
  }),
  destination: {},
}));

// Mock getUserMedia
global.navigator.mediaDevices = {
  getUserMedia: vi.fn().mockResolvedValue(new MediaStream()),
  enumerateDevices: vi.fn().mockResolvedValue([]),
};

// Mock WebSocket
global.WebSocket = vi.fn().mockImplementation((url) => ({
  url,
  readyState: 1, // OPEN
  send: vi.fn(),
  close: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  onopen: null,
  onclose: null,
  onmessage: null,
  onerror: null,
  CONNECTING: 0,
  OPEN: 1,
  CLOSING: 2,
  CLOSED: 3,
}));

// Mock localStorage
const localStorageMock = {
  store: {},
  getItem: vi.fn((key) => localStorageMock.store[key] || null),
  setItem: vi.fn((key, value) => {
    localStorageMock.store[key] = String(value);
  }),
  removeItem: vi.fn((key) => {
    delete localStorageMock.store[key];
  }),
  clear: vi.fn(() => {
    localStorageMock.store = {};
  }),
};
global.localStorage = localStorageMock;

// Reset mocks before each test
beforeEach(() => {
  vi.clearAllMocks();
  localStorageMock.clear();
  document.body.innerHTML = '';
});

// Mock ag2client (the WebRTC library DUCK-E uses)
global.ag2client = {
  WebRTC: vi.fn().mockImplementation((socketUrl) => ({
    socketUrl,
    connect: vi.fn().mockResolvedValue(undefined),
    close: vi.fn(),
    send: vi.fn(),
    onDisconnect: null,
    onMessage: null,
    audioContext: new AudioContext(),
  })),
};

// Helper to create DOM elements for testing
global.createTestDOM = () => {
  document.body.innerHTML = `
    <div id="connection-status">
      <span id="status-text">Ready to Connect</span>
    </div>
    <button id="connect-button">
      <span class="button-text">Connect</span>
    </button>
    <div id="transcript-container">
      <div id="transcript-content"></div>
    </div>
    <button id="toggle-audio">
      <span id="audio-icon">🔊</span>
    </button>
  `;
};

console.log('DUCK-E frontend test setup complete');
