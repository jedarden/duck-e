let webRTC; // Global variable for our WebRTC instance.
let connectionStatus = false; // false means not connected
let isMuted = false; // Microphone mute state
let isPushToTalk = false; // Push-to-talk mode
let transcriptMessages = []; // Store transcript messages
let streamingResponse = null; // Track current streaming response { index, content, type }

// Configure marked for safe rendering
if (typeof marked !== 'undefined') {
  marked.setOptions({
    breaks: true,  // Convert \n to <br>
    gfm: true,     // GitHub Flavored Markdown
    sanitize: false // We trust the content from our backend
  });
}

// Load mute state from localStorage
const loadMuteState = () => {
  const saved = localStorage.getItem('duck-e-muted');
  return saved === 'true';
};

// Save mute state to localStorage
const saveMuteState = (muted) => {
  localStorage.setItem('duck-e-muted', muted.toString());
};

// Load push-to-talk state from localStorage
const loadPushToTalkState = () => {
  const saved = localStorage.getItem('duck-e-ptt');
  return saved === 'true';
};

// Save push-to-talk state to localStorage
const savePushToTalkState = (enabled) => {
  localStorage.setItem('duck-e-ptt', enabled.toString());
};

// Update mute button UI (both main and inline)
const updateMuteUI = (muted) => {
  // Main controls
  const muteBtn = document.getElementById('mute-btn');
  const muteIcon = document.getElementById('mute-icon');
  const muteText = document.getElementById('mute-text');
  // Inline controls
  const muteBtnInline = document.getElementById('mute-btn-inline');
  const muteIconInline = document.getElementById('mute-icon-inline');
  const muteTextInline = document.getElementById('mute-text-inline');

  const icon = muted ? '🔇' : '🔊';
  const text = muted ? 'Unmute' : 'Mute';

  if (muteBtn) {
    if (muted) {
      muteBtn.classList.add('muted');
    } else {
      muteBtn.classList.remove('muted');
    }
  }
  if (muteIcon) muteIcon.textContent = icon;
  if (muteText) muteText.textContent = text;

  // Update inline controls
  if (muteBtnInline) {
    if (muted) {
      muteBtnInline.classList.add('muted');
    } else {
      muteBtnInline.classList.remove('muted');
    }
  }
  if (muteIconInline) muteIconInline.textContent = icon;
  if (muteTextInline) muteTextInline.textContent = text;
};

// Update push-to-talk button UI (both main and inline)
const updatePTTUI = (enabled) => {
  console.log('updatePTTUI called with enabled:', enabled);
  // Main controls
  const pttToggle = document.getElementById('ptt-toggle');
  const pttBtn = document.getElementById('ptt-btn');
  const pttHint = document.querySelector('.ptt-hint');
  // Inline controls
  const pttToggleInline = document.getElementById('ptt-toggle-inline');
  const pttBtnInline = document.getElementById('ptt-btn-inline');

  if (pttToggle) pttToggle.checked = enabled;
  if (pttToggleInline) pttToggleInline.checked = enabled;

  // Main PTT button
  if (pttBtn) {
    if (enabled) {
      pttBtn.classList.remove('hidden');
      pttBtn.style.display = 'flex';
    } else {
      pttBtn.classList.add('hidden');
      pttBtn.style.display = 'none';
    }
  }

  // Inline PTT button
  if (pttBtnInline) {
    if (enabled) {
      pttBtnInline.classList.remove('hidden');
      pttBtnInline.style.display = 'flex';
    } else {
      pttBtnInline.classList.add('hidden');
      pttBtnInline.style.display = 'none';
    }
  }

  // Show/hide the hint
  if (pttHint) {
    pttHint.style.display = enabled ? 'block' : 'none';
  }
};

// Set microphone enabled state
const setMicrophoneEnabled = (enabled) => {
  console.log('setMicrophoneEnabled called:', enabled, 'webRTC:', !!webRTC);
  if (webRTC) {
    console.log('webRTC.microphone:', webRTC.microphone);
    if (webRTC.microphone) {
      webRTC.microphone.enabled = enabled;
      console.log(`Microphone ${enabled ? 'enabled' : 'disabled'}`);
    } else {
      console.warn('webRTC.microphone not available');
    }
  }
};

// Toggle mute state
const toggleMute = () => {
  console.log('toggleMute clicked, webRTC:', !!webRTC, 'connectionStatus:', connectionStatus);

  // Allow toggling state even without connection (will apply on connect)
  isMuted = !isMuted;
  updateMuteUI(isMuted);
  saveMuteState(isMuted);
  console.log(`Mute state toggled to: ${isMuted}`);

  // Apply immediately if connected
  if (webRTC && webRTC.microphone) {
    setMicrophoneEnabled(!isMuted);
  }
};

// Toggle push-to-talk mode
const togglePushToTalk = () => {
  console.log('togglePushToTalk clicked, current state:', isPushToTalk);

  isPushToTalk = !isPushToTalk;
  updatePTTUI(isPushToTalk);
  savePushToTalkState(isPushToTalk);
  console.log(`Push-to-talk mode toggled to: ${isPushToTalk}`);

  // Apply immediately if connected
  if (webRTC && webRTC.microphone) {
    if (isPushToTalk) {
      // Entering PTT mode - disable mic by default
      setMicrophoneEnabled(false);
    } else {
      // Leaving PTT mode - restore based on mute state
      setMicrophoneEnabled(!isMuted);
    }
  }
};

// Push-to-talk button handlers
const startTalking = () => {
  if (!isPushToTalk || !webRTC || !webRTC.microphone) return;

  const pttBtn = document.getElementById('ptt-btn');
  const pttBtnInline = document.getElementById('ptt-btn-inline');
  if (pttBtn) pttBtn.classList.add('active');
  if (pttBtnInline) pttBtnInline.classList.add('active');
  setMicrophoneEnabled(true);
  console.log('PTT: Started talking');
};

const stopTalking = () => {
  if (!isPushToTalk || !webRTC || !webRTC.microphone) return;

  const pttBtn = document.getElementById('ptt-btn');
  const pttBtnInline = document.getElementById('ptt-btn-inline');
  if (pttBtn) pttBtn.classList.remove('active');
  if (pttBtnInline) pttBtnInline.classList.remove('active');
  setMicrophoneEnabled(false);
  console.log('PTT: Stopped talking');
};

// Update layout based on chat history (desktop: show inline controls when has content)
const updateLayoutMode = (hasHistory) => {
  const mainContent = document.querySelector('.main-content');
  if (mainContent) {
    if (hasHistory) {
      mainContent.classList.add('has-history');
    } else {
      mainContent.classList.remove('has-history');
    }
  }
};

// Transcript display functions
const showTranscript = () => {
  const card = document.getElementById('transcript-card');
  if (card) card.classList.add('visible');
  updateLayoutMode(transcriptMessages.length > 0);
};

const hideTranscript = () => {
  const card = document.getElementById('transcript-card');
  if (card) card.classList.remove('visible');
  updateLayoutMode(false);
};

const clearTranscript = () => {
  transcriptMessages = [];
  renderTranscript();
  updateLayoutMode(false);
};

const addTranscriptMessage = (role, content) => {
  transcriptMessages.push({ role, content, timestamp: Date.now() });
  renderTranscript();
  showTranscript();
};

// Start a new streaming response (returns index)
const startStreamingResponse = (role) => {
  const index = transcriptMessages.length;
  transcriptMessages.push({ role, content: '', timestamp: Date.now(), streaming: true });
  streamingResponse = { index, content: '', role };
  renderTranscript();
  showTranscript();
  return index;
};

// Append to streaming response
const appendToStreamingResponse = (delta) => {
  if (!streamingResponse) return;
  streamingResponse.content += delta;
  transcriptMessages[streamingResponse.index].content = streamingResponse.content;
  renderTranscript(true); // Pass true to indicate streaming update
};

// Finalize streaming response
const finalizeStreamingResponse = (finalContent) => {
  if (!streamingResponse) return;
  // Use final content if provided, otherwise keep accumulated content
  if (finalContent) {
    transcriptMessages[streamingResponse.index].content = finalContent;
  }
  transcriptMessages[streamingResponse.index].streaming = false;
  streamingResponse = null;
  renderTranscript();
};

const renderTranscript = (isStreamingUpdate = false) => {
  const container = document.getElementById('transcript-content');
  if (!container) return;

  if (transcriptMessages.length === 0) {
    container.innerHTML = '<div class="transcript-empty">Conversation transcript will appear here...</div>';
    return;
  }

  const html = transcriptMessages.map((msg, idx) => {
    const roleClass = msg.role === 'user' ? 'user' : 'assistant';
    const roleLabel = msg.role === 'user' ? 'You' : 'DUCK-E';
    const streamingClass = msg.streaming ? ' streaming' : '';

    // Parse markdown if marked is available (skip for empty streaming)
    let contentHtml = msg.content || '';
    if (contentHtml && typeof marked !== 'undefined') {
      try {
        contentHtml = marked.parse(msg.content);
      } catch (e) {
        console.warn('Markdown parsing failed:', e);
        contentHtml = msg.content.replace(/\n/g, '<br>');
      }
    } else if (contentHtml) {
      contentHtml = msg.content.replace(/\n/g, '<br>');
    }

    // Show cursor for streaming messages
    if (msg.streaming) {
      contentHtml += '<span class="streaming-cursor">▋</span>';
    }

    return `
      <div class="transcript-message ${roleClass}${streamingClass}" data-idx="${idx}">
        <div class="transcript-label">${roleLabel}</div>
        <div class="transcript-text">${contentHtml}</div>
      </div>
    `;
  }).join('');

  container.innerHTML = html;

  // Auto-scroll to bottom
  container.scrollTop = container.scrollHeight;
};

// Handle incoming messages from WebRTC for transcript
const handleWebRTCMessage = (event) => {
  try {
    // event now has { data: string, message: object } from our modified ag2client
    const data = event.message || (typeof event.data === 'string' ? JSON.parse(event.data) : event.data);

    // Log message types for debugging (can be removed later)
    if (data.type) {
      console.log('WebRTC message type:', data.type);
    }

    // Handle different message types from OpenAI Realtime API
    // User's speech transcription completed
    if (data.type === 'conversation.item.input_audio_transcription.completed') {
      if (data.transcript) {
        console.log('User transcript:', data.transcript);
        addTranscriptMessage('user', data.transcript);
      }
    }
    // Assistant's audio response transcript DELTA (streaming)
    else if (data.type === 'response.audio_transcript.delta') {
      if (data.delta) {
        // Start streaming if not already
        if (!streamingResponse || streamingResponse.type !== 'audio_transcript') {
          startStreamingResponse('assistant');
          if (streamingResponse) streamingResponse.type = 'audio_transcript';
        }
        appendToStreamingResponse(data.delta);
      }
    }
    // Assistant's audio response transcript completed
    else if (data.type === 'response.audio_transcript.done') {
      if (streamingResponse && streamingResponse.type === 'audio_transcript') {
        // Finalize with the complete transcript
        finalizeStreamingResponse(data.transcript);
      } else if (data.transcript) {
        // No streaming was happening, add as complete message
        console.log('Assistant transcript:', data.transcript);
        addTranscriptMessage('assistant', data.transcript);
      }
    }
    // Assistant's text response DELTA (streaming)
    else if (data.type === 'response.text.delta') {
      if (data.delta) {
        // Start streaming if not already
        if (!streamingResponse || streamingResponse.type !== 'text') {
          startStreamingResponse('assistant');
          if (streamingResponse) streamingResponse.type = 'text';
        }
        appendToStreamingResponse(data.delta);
      }
    }
    // Assistant's text response completed (for non-audio responses)
    else if (data.type === 'response.text.done') {
      if (streamingResponse && streamingResponse.type === 'text') {
        // Finalize with the complete text
        finalizeStreamingResponse(data.text);
      } else if (data.text) {
        // No streaming was happening, add as complete message
        console.log('Assistant text:', data.text);
        addTranscriptMessage('assistant', data.text);
      }
    }
    // Response cancelled or interrupted - finalize any streaming
    else if (data.type === 'response.cancelled' || data.type === 'response.done') {
      if (streamingResponse) {
        finalizeStreamingResponse();
      }
    }
    // Custom transcript message from backend
    else if (data.type === 'transcript') {
      addTranscriptMessage(data.role || 'assistant', data.content);
    }
  } catch (e) {
    console.error('Error handling WebRTC message:', e);
  }
};

// Function to update the UI elements based on connection status.
const updateUI = (status) => {
  // Main controls
  const statusIndicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  const buttonText = document.getElementById("button-text");
  const toggleButton = document.getElementById("toggle-connection");
  const muteBtn = document.getElementById("mute-btn");
  const pttControls = document.getElementById("ptt-controls");
  // Inline controls
  const statusIndicatorInline = document.getElementById("status-indicator-inline");
  const statusTextInline = document.getElementById("status-text-inline");
  const buttonTextInline = document.getElementById("button-text-inline");
  const toggleButtonInline = document.getElementById("toggle-connection-inline");
  const muteBtnInline = document.getElementById("mute-btn-inline");
  const pttControlsInline = document.getElementById("ptt-controls-inline");

  // Remove all status classes from both
  statusIndicator.classList.remove("connecting", "connected", "disconnected");
  toggleButton.classList.remove("connected");
  if (statusIndicatorInline) statusIndicatorInline.classList.remove("connecting", "connected", "disconnected");
  if (toggleButtonInline) toggleButtonInline.classList.remove("connected");

  if (status === "connecting") {
    statusIndicator.classList.add("connecting");
    statusText.textContent = "Connecting to DUCK-E...";
    buttonText.innerHTML = '<span class="spinner"></span>';
    toggleButton.disabled = true;
    muteBtn.disabled = true;
    // Inline
    if (statusIndicatorInline) statusIndicatorInline.classList.add("connecting");
    if (statusTextInline) statusTextInline.textContent = "Connecting...";
    if (buttonTextInline) buttonTextInline.innerHTML = '<span class="spinner"></span>';
    if (toggleButtonInline) toggleButtonInline.disabled = true;
    if (muteBtnInline) muteBtnInline.disabled = true;
    // Hide PTT controls while connecting
    if (pttControls) pttControls.style.display = 'none';
    if (pttControlsInline) pttControlsInline.style.display = 'none';
  } else if (status === "connected") {
    statusIndicator.classList.add("connected");
    statusText.textContent = "Connected - DUCK-E is listening";
    buttonText.textContent = "Disconnect";
    toggleButton.classList.add("connected");
    toggleButton.disabled = false;
    muteBtn.disabled = false;
    // Inline
    if (statusIndicatorInline) statusIndicatorInline.classList.add("connected");
    if (statusTextInline) statusTextInline.textContent = "Connected";
    if (buttonTextInline) buttonTextInline.textContent = "Disconnect";
    if (toggleButtonInline) {
      toggleButtonInline.classList.add("connected");
      toggleButtonInline.disabled = false;
    }
    if (muteBtnInline) muteBtnInline.disabled = false;
    // Show PTT controls when connected
    if (pttControls) {
      pttControls.style.display = 'flex';
      pttControls.classList.remove('disabled');
    }
    if (pttControlsInline) pttControlsInline.style.display = 'flex';
    // Apply saved PTT state now that we're connected
    updatePTTUI(isPushToTalk);
  } else if (status === "disconnected") {
    statusIndicator.classList.add("disconnected");
    statusText.textContent = "Ready to Connect";
    buttonText.textContent = "Connect";
    toggleButton.disabled = false;
    muteBtn.disabled = false;
    // Inline
    if (statusIndicatorInline) statusIndicatorInline.classList.add("disconnected");
    if (statusTextInline) statusTextInline.textContent = "Ready";
    if (buttonTextInline) buttonTextInline.textContent = "Connect";
    if (toggleButtonInline) toggleButtonInline.disabled = false;
    if (muteBtnInline) muteBtnInline.disabled = false;
    // Hide PTT controls when disconnected
    if (pttControls) pttControls.style.display = 'none';
    if (pttControlsInline) pttControlsInline.style.display = 'none';
  }
};

const toggleConnection = async () => {
  if (!connectionStatus) {
    // User is attempting to connect.
    updateUI("connecting");

    try {
      webRTC = new ag2client.WebRTC(socketUrl);

      // Set up the disconnect callback.
      webRTC.onDisconnect = () => {
        updateUI("disconnected");
        connectionStatus = false;
      };

      // Set up message handler for transcript
      webRTC.onMessage = handleWebRTCMessage;

      await webRTC.connect();

      updateUI("connected");
      connectionStatus = true;

      // Apply saved states after connection
      if (isPushToTalk) {
        // In PTT mode, mic is off by default
        setMicrophoneEnabled(false);
      } else if (isMuted) {
        // Apply saved mute state
        setMicrophoneEnabled(false);
      }

      console.log('Connection established, mic state applied');
    } catch (error) {
      console.error("Connection error:", error);
      updateUI("disconnected");
      connectionStatus = false;

      // Show error notification
      alert("Failed to connect to DUCK-E. Please check your microphone permissions and try again.");
    }
  } else {
    // User is attempting to disconnect.
    if (webRTC && typeof webRTC.close === "function") {
      webRTC.close();
    }
    updateUI("disconnected");
    connectionStatus = false;
  }
};

// Helper to attach PTT button events (mouse and touch)
const attachPTTButtonEvents = (btn) => {
  if (!btn) return;
  btn.addEventListener("mousedown", startTalking);
  btn.addEventListener("mouseup", stopTalking);
  btn.addEventListener("mouseleave", stopTalking);
  btn.addEventListener("touchstart", (e) => { e.preventDefault(); startTalking(); });
  btn.addEventListener("touchend", (e) => { e.preventDefault(); stopTalking(); });
  btn.addEventListener("touchcancel", stopTalking);
};

document.addEventListener("DOMContentLoaded", () => {
  // Log version to console
  if (typeof APP_VERSION !== 'undefined') {
    console.log(`%cDUCK-E v${APP_VERSION}`, 'color: #4F46E5; font-weight: bold; font-size: 14px;');
    console.log(`%cDigitally Unified Conversational Knowledge Engine`, 'color: #94A3B8; font-size: 11px;');
  }

  console.log('DOMContentLoaded fired - attaching event listeners');

  // Main controls
  const toggleButton = document.getElementById("toggle-connection");
  toggleButton.addEventListener("click", toggleConnection);

  const muteBtn = document.getElementById("mute-btn");
  if (muteBtn) muteBtn.addEventListener("click", toggleMute);

  const pttToggle = document.getElementById("ptt-toggle");
  if (pttToggle) pttToggle.addEventListener("change", togglePushToTalk);

  const pttBtn = document.getElementById("ptt-btn");
  attachPTTButtonEvents(pttBtn);

  // Inline controls (desktop - below transcript)
  const toggleButtonInline = document.getElementById("toggle-connection-inline");
  if (toggleButtonInline) toggleButtonInline.addEventListener("click", toggleConnection);

  const muteBtnInline = document.getElementById("mute-btn-inline");
  if (muteBtnInline) muteBtnInline.addEventListener("click", toggleMute);

  const pttToggleInline = document.getElementById("ptt-toggle-inline");
  if (pttToggleInline) pttToggleInline.addEventListener("change", togglePushToTalk);

  const pttBtnInline = document.getElementById("ptt-btn-inline");
  attachPTTButtonEvents(pttBtnInline);

  // Keyboard shortcut for PTT (spacebar when in PTT mode)
  document.addEventListener("keydown", (e) => {
    if (isPushToTalk && e.code === "Space" && !e.repeat && connectionStatus) {
      e.preventDefault();
      startTalking();
    }
  });

  document.addEventListener("keyup", (e) => {
    if (isPushToTalk && e.code === "Space" && connectionStatus) {
      e.preventDefault();
      stopTalking();
    }
  });

  // Attach clear transcript button listener
  const clearBtn = document.getElementById("clear-transcript");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      clearTranscript();
      hideTranscript();
    });
  }

  // Load saved preferences
  isMuted = loadMuteState();
  isPushToTalk = loadPushToTalkState();

  updateMuteUI(isMuted);
  updatePTTUI(isPushToTalk);

  // Initialize UI
  updateUI("disconnected");
});
