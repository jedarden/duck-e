let webRTC; // Global variable for our WebRTC instance.
let connectionStatus = false; // false means not connected
let isMuted = false; // Microphone mute state
let isPushToTalk = false; // Push-to-talk mode
let transcriptMessages = []; // Store transcript messages

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

// Update mute button UI
const updateMuteUI = (muted) => {
  const muteBtn = document.getElementById('mute-btn');
  const muteIcon = document.getElementById('mute-icon');
  const muteText = document.getElementById('mute-text');

  if (muted) {
    muteBtn.classList.add('muted');
    muteIcon.textContent = '🔇';
    muteText.textContent = 'Unmute';
  } else {
    muteBtn.classList.remove('muted');
    muteIcon.textContent = '🔊';
    muteText.textContent = 'Mute';
  }
};

// Update push-to-talk button UI
const updatePTTUI = (enabled) => {
  console.log('updatePTTUI called with enabled:', enabled);
  const pttToggle = document.getElementById('ptt-toggle');
  const pttBtn = document.getElementById('ptt-btn');
  const pttHint = document.querySelector('.ptt-hint');

  console.log('pttToggle element:', pttToggle);
  console.log('pttBtn element:', pttBtn);

  if (pttToggle) {
    pttToggle.checked = enabled;
  }

  if (pttBtn) {
    if (enabled) {
      pttBtn.classList.remove('hidden');
      pttBtn.style.display = 'flex'; // Force display
      console.log('PTT button shown');
    } else {
      pttBtn.classList.add('hidden');
      pttBtn.style.display = 'none';
      console.log('PTT button hidden');
    }
  } else {
    console.error('ptt-btn element not found!');
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
  if (pttBtn) {
    pttBtn.classList.add('active');
  }
  setMicrophoneEnabled(true);
  console.log('PTT: Started talking');
};

const stopTalking = () => {
  if (!isPushToTalk || !webRTC || !webRTC.microphone) return;

  const pttBtn = document.getElementById('ptt-btn');
  if (pttBtn) {
    pttBtn.classList.remove('active');
  }
  setMicrophoneEnabled(false);
  console.log('PTT: Stopped talking');
};

// Transcript display functions
const showTranscript = () => {
  const card = document.getElementById('transcript-card');
  if (card) card.classList.add('visible');
};

const hideTranscript = () => {
  const card = document.getElementById('transcript-card');
  if (card) card.classList.remove('visible');
};

const clearTranscript = () => {
  transcriptMessages = [];
  renderTranscript();
};

const addTranscriptMessage = (role, content) => {
  transcriptMessages.push({ role, content, timestamp: Date.now() });
  renderTranscript();
  showTranscript();
};

const renderTranscript = () => {
  const container = document.getElementById('transcript-content');
  if (!container) return;

  if (transcriptMessages.length === 0) {
    container.innerHTML = '<div class="transcript-empty">Conversation transcript will appear here...</div>';
    return;
  }

  const html = transcriptMessages.map(msg => {
    const roleClass = msg.role === 'user' ? 'user' : 'assistant';
    const roleLabel = msg.role === 'user' ? 'You' : 'DUCK-E';

    // Parse markdown if marked is available
    let contentHtml = msg.content;
    if (typeof marked !== 'undefined') {
      try {
        contentHtml = marked.parse(msg.content);
      } catch (e) {
        console.warn('Markdown parsing failed:', e);
        contentHtml = msg.content.replace(/\n/g, '<br>');
      }
    } else {
      contentHtml = msg.content.replace(/\n/g, '<br>');
    }

    return `
      <div class="transcript-message ${roleClass}">
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
    const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;

    // Handle different message types from OpenAI Realtime API
    if (data.type === 'response.audio_transcript.done' ||
        data.type === 'conversation.item.input_audio_transcription.completed') {
      // User's speech transcription
      if (data.transcript) {
        addTranscriptMessage('user', data.transcript);
      }
    } else if (data.type === 'response.text.done' ||
               data.type === 'response.audio_transcript.delta') {
      // Assistant's response
      if (data.text || data.delta) {
        const text = data.text || data.delta;
        // For delta messages, we might want to accumulate them
        // For now, just add complete messages
        if (data.type === 'response.text.done' && text) {
          addTranscriptMessage('assistant', text);
        }
      }
    } else if (data.type === 'transcript') {
      // Custom transcript message from backend
      addTranscriptMessage(data.role || 'assistant', data.content);
    }
  } catch (e) {
    // Not a JSON message or parsing error, ignore
  }
};

// Function to update the UI elements based on connection status.
const updateUI = (status) => {
  const statusIndicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  const buttonText = document.getElementById("button-text");
  const toggleButton = document.getElementById("toggle-connection");
  const muteBtn = document.getElementById("mute-btn");
  const pttControls = document.getElementById("ptt-controls");

  // Remove all status classes
  statusIndicator.classList.remove("connecting", "connected", "disconnected");
  toggleButton.classList.remove("connected");

  if (status === "connecting") {
    statusIndicator.classList.add("connecting");
    statusText.textContent = "Connecting to DUCK-E...";
    buttonText.innerHTML = '<span class="spinner"></span>';
    toggleButton.disabled = true;
    muteBtn.disabled = true;
    if (pttControls) pttControls.classList.add('disabled');
  } else if (status === "connected") {
    statusIndicator.classList.add("connected");
    statusText.textContent = "Connected - DUCK-E is listening";
    buttonText.textContent = "Disconnect";
    toggleButton.classList.add("connected");
    toggleButton.disabled = false;
    muteBtn.disabled = false;
    if (pttControls) pttControls.classList.remove('disabled');
  } else if (status === "disconnected") {
    statusIndicator.classList.add("disconnected");
    statusText.textContent = "Ready to Connect";
    buttonText.textContent = "Connect";
    toggleButton.disabled = false;
    muteBtn.disabled = false; // Allow setting mute preference before connecting
    if (pttControls) pttControls.classList.add('disabled');
    // Keep mute state (don't reset on disconnect)
    // Keep transcript visible but don't clear it
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

document.addEventListener("DOMContentLoaded", () => {
  console.log('DOMContentLoaded fired - attaching event listeners');

  // Attach the toggle function to the button click.
  const toggleButton = document.getElementById("toggle-connection");
  toggleButton.addEventListener("click", toggleConnection);
  console.log('Attached toggleConnection to button');

  // Attach mute button listener
  const muteBtn = document.getElementById("mute-btn");
  if (muteBtn) {
    muteBtn.addEventListener("click", toggleMute);
    console.log('Attached toggleMute to mute button');
  } else {
    console.error('mute-btn not found!');
  }

  // Attach push-to-talk toggle listener
  const pttToggle = document.getElementById("ptt-toggle");
  if (pttToggle) {
    pttToggle.addEventListener("change", togglePushToTalk);
    console.log('Attached togglePushToTalk to ptt-toggle');
  } else {
    console.warn('ptt-toggle not found');
  }

  // Attach push-to-talk button listeners (mouse and touch)
  const pttBtn = document.getElementById("ptt-btn");
  if (pttBtn) {
    // Mouse events
    pttBtn.addEventListener("mousedown", startTalking);
    pttBtn.addEventListener("mouseup", stopTalking);
    pttBtn.addEventListener("mouseleave", stopTalking);

    // Touch events for mobile
    pttBtn.addEventListener("touchstart", (e) => {
      e.preventDefault();
      startTalking();
    });
    pttBtn.addEventListener("touchend", (e) => {
      e.preventDefault();
      stopTalking();
    });
    pttBtn.addEventListener("touchcancel", stopTalking);
  }

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
