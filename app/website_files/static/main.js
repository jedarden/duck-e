let webRTC; // Global variable for our WebRTC instance.
let connectionStatus = false; // false means not connected
let isMuted = false; // Microphone mute state
let isPushToTalk = false; // Push-to-talk mode
let transcriptMessages = []; // Store transcript messages
let streamingResponse = null; // Track current streaming response { index, content, type }
let currentTurnUserTranscript = ''; // Last user utterance for memory extraction
let currentTurnAssistantTranscript = ''; // Current assistant response for memory extraction

// Cost tracking state (gpt-realtime-1.5)
let sessionCost = {
  startTime: null,
  totalInputTextTokens: 0,
  totalInputAudioTokens: 0,
  totalOutputTextTokens: 0,
  totalOutputAudioTokens: 0,
  totalCachedTokens: 0,
};

// Pricing constants for gpt-realtime-1.5 (per token)
const PRICING = {
  textInput:   4.00  / 1_000_000,
  textOutput:  16.00 / 1_000_000,
  audioInput:  32.00 / 1_000_000,
  audioOutput: 64.00 / 1_000_000,
  cachedInput: 0.40  / 1_000_000,
};

const updateCostFromResponse = (usage) => {
  if (!sessionCost.startTime) sessionCost.startTime = Date.now();

  const details = usage.input_token_details || {};
  const outDetails = usage.output_token_details || {};

  sessionCost.totalInputTextTokens    += details.text_tokens    || 0;
  sessionCost.totalInputAudioTokens   += details.audio_tokens   || 0;
  sessionCost.totalCachedTokens       += details.cached_tokens  || 0;
  sessionCost.totalOutputTextTokens   += outDetails.text_tokens  || 0;
  sessionCost.totalOutputAudioTokens  += outDetails.audio_tokens || 0;

  const totalCost =
    (sessionCost.totalInputTextTokens   * PRICING.textInput)   +
    (sessionCost.totalInputAudioTokens  * PRICING.audioInput)  +
    (sessionCost.totalOutputTextTokens  * PRICING.textOutput)  +
    (sessionCost.totalOutputAudioTokens * PRICING.audioOutput) +
    (sessionCost.totalCachedTokens      * PRICING.cachedInput);

  const elapsedMs = Date.now() - sessionCost.startTime;
  const elapsedHours = elapsedMs / 3_600_000;
  const hourlyRate = elapsedHours > 0 ? totalCost / elapsedHours : 0;

  const costs = {
    audioInput:  sessionCost.totalInputAudioTokens  * PRICING.audioInput,
    audioOutput: sessionCost.totalOutputAudioTokens * PRICING.audioOutput,
    textInput:   sessionCost.totalInputTextTokens   * PRICING.textInput,
    textOutput:  sessionCost.totalOutputTextTokens  * PRICING.textOutput,
    cached:      sessionCost.totalCachedTokens      * PRICING.cachedInput,
  };

  updateCostDisplay(totalCost, hourlyRate, costs, elapsedMs);
};

let costPanelListenerAttached = false;

const updateCostDisplay = (totalCost, hourlyRate, costs, elapsedMs) => {
  const el = document.getElementById('cost-display');
  const panel = document.getElementById('cost-panel');
  if (!el || !panel) return;
  el.style.display = 'block';
  panel.style.display = 'block';

  const fmt = (v) => '$' + v.toFixed(2);
  const mins = Math.floor(elapsedMs / 60000);
  const secs = Math.floor((elapsedMs % 60000) / 1000);
  const duration = `${mins}m ${secs.toString().padStart(2, '0')}s`;

  el.innerHTML = `<button class="cost-toggle-btn">Est. ~${fmt(hourlyRate)}/hr</button>`;

  if (!costPanelListenerAttached) {
    el.addEventListener('click', () => panel.classList.toggle('open'));
    costPanelListenerAttached = true;
  }

  panel.innerHTML = `
    <div class="cost-breakdown">
      <div class="cost-summary-row">
        <div class="cost-item"><span>Duration</span><span>${duration}</span></div>
        <div class="cost-item"><span>Session Total</span><span>${fmt(totalCost)}</span></div>
      </div>
      <hr class="cost-divider">
      <div class="cost-grid">
        <div class="cost-item"><span>Audio In</span><span class="cost-val">${fmt(costs.audioInput)}</span></div>
        <div class="cost-item"><span>Audio Out</span><span class="cost-val">${fmt(costs.audioOutput)}</span></div>
        <div class="cost-item"><span>Text In</span><span class="cost-val">${fmt(costs.textInput)}</span></div>
        <div class="cost-item"><span>Text Out</span><span class="cost-val">${fmt(costs.textOutput)}</span></div>
        <div class="cost-item"><span>Cached</span><span class="cost-val">${fmt(costs.cached)}</span></div>
      </div>
    </div>
  `;

  // Brief flash to indicate update
  el.classList.remove('cost-flash');
  void el.offsetWidth; // reflow to restart animation
  el.classList.add('cost-flash');
};

// Performance timeline tracking
let perfTimeline = null;

const perfReset = () => {
  perfTimeline = {
    speechStarted: null,
    speechStopped: null,
    transcriptionDone: null,
    responseCreated: null,
    // tool calls: map from call_id -> { dispatched, resultReceived }
    toolCalls: {},
    responseDone: null,
  };
};

const perfTs = () => performance.now();

const perfLogSummary = () => {
  if (!perfTimeline) return;
  const p = perfTimeline;
  const fmt = (ms) => ms != null ? (ms / 1000).toFixed(2) + 's' : '?';
  const delta = (a, b) => (a != null && b != null) ? b - a : null;

  const speechDur = delta(p.speechStarted, p.speechStopped);
  const transcriptionDur = delta(p.speechStopped, p.transcriptionDone);
  const thinkingDur = delta(p.transcriptionDone ?? p.speechStopped, p.responseCreated);

  const toolSummaries = Object.entries(p.toolCalls).map(([id, tc]) => {
    const dur = delta(tc.dispatched, tc.resultReceived);
    return `tool_call(${tc.name ?? id}): ${fmt(dur)}`;
  });

  const responseDur = (() => {
    const afterTools = Object.values(p.toolCalls).map(tc => tc.resultReceived).filter(Boolean);
    const start = afterTools.length > 0 ? Math.max(...afterTools) : p.responseCreated;
    return delta(start, p.responseDone);
  })();

  const total = delta(p.speechStarted ?? p.responseCreated, p.responseDone);

  const parts = [
    `speech: ${fmt(speechDur)}`,
    `transcription: ${fmt(transcriptionDur)}`,
    `thinking: ${fmt(thinkingDur)}`,
    ...toolSummaries,
    `response: ${fmt(responseDur)}`,
    `total: ${fmt(total)}`,
  ];
  console.log(`[DUCK-E Perf] ${parts.join(', ')}`);
};

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
  // Only show and switch layout when there's actual content (not empty placeholders)
  const hasVisibleContent = transcriptMessages.some(msg => msg.content || msg.streaming);
  if (card && hasVisibleContent) card.classList.add('visible');
  updateLayoutMode(hasVisibleContent);
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

// Find message by itemId
const findMessageByItemId = (itemId) => {
  return transcriptMessages.findIndex(msg => msg.itemId === itemId);
};

// Create placeholder for conversation item (called when item is created - correct order)
const createMessagePlaceholder = (itemId, role) => {
  // Check if already exists
  if (findMessageByItemId(itemId) !== -1) return;
  transcriptMessages.push({
    itemId,
    role,
    content: '',
    timestamp: Date.now(),
    pending: true  // Mark as waiting for transcription
  });
  renderTranscript();
  showTranscript();
};

// Update message content by itemId
const updateMessageByItemId = (itemId, content, options = {}) => {
  const index = findMessageByItemId(itemId);
  if (index !== -1) {
    transcriptMessages[index].content = content;
    transcriptMessages[index].pending = false;
    if (options.streaming !== undefined) {
      transcriptMessages[index].streaming = options.streaming;
    }
    renderTranscript();
    showTranscript();
    return index;
  }
  return -1;
};

// Add a tool call message to the transcript
const addToolCallMessage = (name, args, callId) => {
  transcriptMessages.push({
    role: 'tool',
    type: 'tool_call',
    toolName: name,
    toolArgs: args,
    callId: callId,
    status: 'pending',
    result: null,
    timestamp: Date.now()
  });
  renderTranscript();
  showTranscript();
};

// Legacy function for messages without itemId
const addTranscriptMessage = (role, content, itemId = null) => {
  if (itemId) {
    const index = findMessageByItemId(itemId);
    if (index !== -1) {
      // Update existing placeholder
      transcriptMessages[index].content = content;
      transcriptMessages[index].pending = false;
      renderTranscript();
      showTranscript();
      return;
    }
  }
  // Fallback: add new message
  transcriptMessages.push({ role, content, timestamp: Date.now(), itemId });
  renderTranscript();
  showTranscript();
};

// Start a new streaming response (returns index)
const startStreamingResponse = (role, itemId = null) => {
  let index;
  if (itemId) {
    index = findMessageByItemId(itemId);
    if (index !== -1) {
      // Use existing placeholder
      transcriptMessages[index].streaming = true;
      transcriptMessages[index].pending = false;
    } else {
      // Create new with itemId
      index = transcriptMessages.length;
      transcriptMessages.push({ role, content: '', timestamp: Date.now(), streaming: true, itemId });
    }
  } else {
    index = transcriptMessages.length;
    transcriptMessages.push({ role, content: '', timestamp: Date.now(), streaming: true });
  }
  streamingResponse = { index, content: '', role, itemId };
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

  // For streaming updates, only update the streaming message's text (much faster)
  if (isStreamingUpdate && streamingResponse) {
    const streamingEl = container.querySelector(`[data-idx="${streamingResponse.index}"] .transcript-text`);
    if (streamingEl) {
      const msg = transcriptMessages[streamingResponse.index];
      let contentHtml = msg.content || '';
      // Simple text for streaming - skip markdown parsing for performance
      contentHtml = contentHtml.replace(/\n/g, '<br>');
      contentHtml += '<span class="streaming-cursor">▋</span>';
      streamingEl.innerHTML = contentHtml;
      container.scrollTop = container.scrollHeight;
      return;
    }
  }

  // Full render for new messages or finalization
  // Filter out pending placeholders with no content (always include tool_call messages)
  const visibleMessages = transcriptMessages.filter(msg => msg.content || msg.streaming || msg.type === 'tool_call');

  if (visibleMessages.length === 0) {
    container.innerHTML = '<div class="transcript-empty">Conversation transcript will appear here...</div>';
    return;
  }

  const html = visibleMessages.map((msg, idx) => {
    // Tool call messages — collapsed by default, expand to see request + response
    if (msg.type === 'tool_call') {
      let argsFormatted = msg.toolArgs || '';
      try {
        argsFormatted = JSON.stringify(JSON.parse(msg.toolArgs), null, 2);
      } catch (e) { /* keep raw */ }

      const statusClass = msg.status === 'completed' ? 'completed' : 'pending';

      // Response section inside the expandable body
      let resultHtml = '';
      if (msg.result) {
        resultHtml = `
          <div class="tool-call-section">
            <div class="tool-call-section-label">Response</div>
            <pre class="tool-call-section-text">${msg.result}</pre>
          </div>`;
      } else if (msg.status === 'pending') {
        resultHtml = `
          <div class="tool-call-section">
            <span class="tool-call-loading">Waiting for result...</span>
          </div>`;
      }

      return `
        <div class="transcript-message tool-call" data-idx="${idx}">
          <details class="tool-call-details">
            <summary class="tool-call-summary">
              <span class="tool-call-icon">🔧</span>
              <span class="tool-call-name">${msg.toolName}</span>
              <span class="tool-call-status ${statusClass}">${msg.status}</span>
            </summary>
            <div class="tool-call-body">
              <div class="tool-call-section">
                <div class="tool-call-section-label">Request</div>
                <pre class="tool-call-section-text">${argsFormatted}</pre>
              </div>
              ${resultHtml}
            </div>
          </details>
        </div>
      `;
    }

    const roleClass = msg.role === 'user' ? 'user' : 'assistant';
    const roleLabel = msg.role === 'user' ? 'You' : 'DUCK-E';
    const streamingClass = msg.streaming ? ' streaming' : '';

    // Parse markdown if marked is available (skip for empty streaming)
    let contentHtml = msg.content || '';
    if (contentHtml && typeof marked !== 'undefined' && !msg.streaming) {
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

    // Perf telemetry hooks
    if (data.type === 'input_audio_buffer.speech_started') {
      perfReset();
      perfTimeline.speechStarted = perfTs();
      console.log(`[DUCK-E Perf] speech_started ts=${perfTimeline.speechStarted.toFixed(0)}ms`);
    } else if (data.type === 'input_audio_buffer.speech_stopped') {
      if (perfTimeline) {
        perfTimeline.speechStopped = perfTs();
        const delta = perfTimeline.speechStopped - (perfTimeline.speechStarted ?? perfTimeline.speechStopped);
        console.log(`[DUCK-E Perf] speech_stopped  speech_dur=${delta.toFixed(0)}ms`);
      }
    } else if (data.type === 'conversation.item.input_audio_transcription.completed') {
      if (perfTimeline) {
        perfTimeline.transcriptionDone = perfTs();
        const start = perfTimeline.speechStopped ?? perfTimeline.speechStarted;
        const delta = start != null ? perfTimeline.transcriptionDone - start : null;
        console.log(`[DUCK-E Perf] transcription_done  transcription_dur=${delta != null ? delta.toFixed(0) : '?'}ms`);
      }
    } else if (data.type === 'response.created') {
      if (!perfTimeline) perfReset();
      perfTimeline.responseCreated = perfTs();
      console.log(`[DUCK-E Perf] response.created ts=${perfTimeline.responseCreated.toFixed(0)}ms`);
    } else if (data.type === 'response.function_call_arguments.done') {
      if (perfTimeline) {
        const callId = data.call_id;
        perfTimeline.toolCalls[callId] = { name: data.name, dispatched: perfTs(), resultReceived: null };
        console.log(`[DUCK-E Perf] tool_dispatched  tool=${data.name} call_id=${callId}`);
      }
    } else if (data.type === 'response.output_item.done') {
      if (perfTimeline && data.item?.type === 'function_call') {
        const callId = data.item.call_id;
        if (perfTimeline.toolCalls[callId]) {
          perfTimeline.toolCalls[callId].resultReceived = perfTs();
          const dur = perfTimeline.toolCalls[callId].resultReceived - perfTimeline.toolCalls[callId].dispatched;
          console.log(`[DUCK-E Perf] tool_result_received  tool=${data.item.name} call_id=${callId} dur=${dur.toFixed(0)}ms`);
        }
      }
    } else if (data.type === 'response.done') {
      if (perfTimeline) {
        perfTimeline.responseDone = perfTs();
        perfLogSummary();
      }
    }

    // Log message types for debugging (can be removed later)
    if (data.type) {
      console.log('WebRTC message type:', data.type);
    }

    // Handle different message types from OpenAI Realtime API

    // Conversation item created - create placeholder in correct order
    if (data.type === 'conversation.item.created') {
      const item = data.item;
      if (item && item.id && item.role) {
        console.log('Conversation item created:', item.id, item.role);
        createMessagePlaceholder(item.id, item.role);
      }
    }
    // User's speech transcription completed - update placeholder by item_id
    else if (data.type === 'conversation.item.input_audio_transcription.completed') {
      if (data.transcript) {
        const itemId = data.item_id;
        console.log('User transcript:', data.transcript, 'itemId:', itemId);
        currentTurnUserTranscript = data.transcript; // Capture for memory extraction
        currentTurnAssistantTranscript = ''; // Reset assistant side for new turn
        if (itemId && findMessageByItemId(itemId) !== -1) {
          updateMessageByItemId(itemId, data.transcript);
        } else {
          // Fallback if no placeholder exists
          addTranscriptMessage('user', data.transcript, itemId);
        }
      }
    }
    // Assistant's audio response transcript DELTA (streaming)
    else if (data.type === 'response.audio_transcript.delta') {
      if (data.delta) {
        const itemId = data.item_id;
        // Start streaming if not already or if different item
        if (!streamingResponse || streamingResponse.type !== 'audio_transcript' || streamingResponse.itemId !== itemId) {
          startStreamingResponse('assistant', itemId);
          if (streamingResponse) streamingResponse.type = 'audio_transcript';
        }
        appendToStreamingResponse(data.delta);
      }
    }
    // Assistant's audio response transcript completed
    else if (data.type === 'response.audio_transcript.done') {
      const itemId = data.item_id;
      if (data.transcript) {
        currentTurnAssistantTranscript = data.transcript; // Capture for memory extraction
      }
      if (streamingResponse && streamingResponse.type === 'audio_transcript') {
        // Finalize with the complete transcript
        finalizeStreamingResponse(data.transcript);
      } else if (data.transcript) {
        // No streaming was happening, add as complete message
        console.log('Assistant transcript:', data.transcript);
        addTranscriptMessage('assistant', data.transcript, itemId);
      }
    }
    // Assistant's text response DELTA (streaming)
    else if (data.type === 'response.text.delta') {
      if (data.delta) {
        const itemId = data.item_id;
        // Start streaming if not already or if different item
        if (!streamingResponse || streamingResponse.type !== 'text' || streamingResponse.itemId !== itemId) {
          startStreamingResponse('assistant', itemId);
          if (streamingResponse) streamingResponse.type = 'text';
        }
        appendToStreamingResponse(data.delta);
      }
    }
    // Assistant's text response completed (for non-audio responses)
    else if (data.type === 'response.text.done') {
      const itemId = data.item_id;
      if (streamingResponse && streamingResponse.type === 'text') {
        // Finalize with the complete text
        finalizeStreamingResponse(data.text);
      } else if (data.text) {
        // No streaming was happening, add as complete message
        console.log('Assistant text:', data.text);
        addTranscriptMessage('assistant', data.text, itemId);
      }
    }
    // Function call arguments complete - display tool call card in transcript
    else if (data.type === 'response.function_call_arguments.done') {
      const name = data.name;
      const callId = data.call_id;
      const args = data.arguments;
      console.log('Tool call:', name, callId, args);
      addToolCallMessage(name, args, callId);
    }
    // Function call output from backend relay - populate tool result on the card
    else if (data.type === 'conversation.item.create' && data.item?.type === 'function_call_output') {
      const callId = data.item.call_id;
      const output = data.item.output;
      console.log('Tool result received for call_id:', callId, output);
      const idx = transcriptMessages.findIndex(
        msg => msg.type === 'tool_call' && msg.callId === callId
      );
      if (idx !== -1) {
        transcriptMessages[idx].result = output;
        renderTranscript();
      }
    }
    // Output item done - mark matching tool call as completed
    else if (data.type === 'response.output_item.done') {
      const item = data.item;
      if (item && item.type === 'function_call') {
        const idx = transcriptMessages.findIndex(
          msg => msg.type === 'tool_call' && msg.callId === item.call_id
        );
        if (idx !== -1) {
          transcriptMessages[idx].status = 'completed';
          renderTranscript();
        }
      }
    }
    // Response cancelled or interrupted - finalize any streaming
    else if (data.type === 'response.cancelled' || data.type === 'response.done') {
      if (streamingResponse) {
        finalizeStreamingResponse();
      }
      if (data.type === 'response.done') {
        // Accumulate token costs from response.done usage data
        if (data.response?.usage) {
          updateCostFromResponse(data.response.usage);
        }
        // Fire memory extraction if assistant spoke (skips tool-only turns)
        if (currentTurnUserTranscript && currentTurnAssistantTranscript &&
            webRTC?.ws?.readyState === WebSocket.OPEN) {
          webRTC.ws.send(JSON.stringify({
            type: 'ducke.turn_done',
            user_text: currentTurnUserTranscript,
            assistant_text: currentTurnAssistantTranscript,
          }));
          currentTurnAssistantTranscript = ''; // Reset for next response in same turn
        }
      }
    }
    // OpenAI Realtime API error — surface to transcript so it's visible
    else if (data.type === 'error') {
      const errMsg = data.error?.message || JSON.stringify(data.error) || 'Unknown error';
      console.error('[DUCK-E] Realtime API error:', data.error);
      addTranscriptMessage('system', `Error: ${errMsg}`);
    }
    // Custom transcript message from backend
    else if (data.type === 'transcript') {
      addTranscriptMessage(data.role || 'assistant', data.content);
    }
    // Voice changed — session reinitialised with new WebRTC peer
    else if (data.type === 'ducke.voice_changed') {
      const voice = data.voice || 'unknown';
      addTranscriptMessage('system', `Voice changed to ${voice}`);
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

// Expose transcript functions globally for Agentation annotation callbacks
window.addTranscriptMessage = addTranscriptMessage;
window.addToolCallMessage = addToolCallMessage;

// Send a structured annotation to the backend via WebSocket.
// Called by the Agentation onSubmit callback when the user submits feedback.
window.sendAnnotationToBackend = (annotation) => {
  if (webRTC && webRTC.ws && webRTC.ws.readyState === WebSocket.OPEN) {
    webRTC.ws.send(JSON.stringify({
      type: 'ducke.annotation',
      annotation: annotation,
    }));
  }
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
