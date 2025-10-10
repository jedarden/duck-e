let webRTC; // Global variable for our WebRTC instance.
let connectionStatus = false; // false means not connected
let toolCallLog = []; // Array to store tool calls

// Function to update the UI elements based on connection status.
const updateUI = (status) => {
  const statusIndicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  const buttonText = document.getElementById("button-text");
  const toggleButton = document.getElementById("toggle-connection");

  // Remove all status classes
  statusIndicator.classList.remove("connecting", "connected", "disconnected");
  toggleButton.classList.remove("connected");

  if (status === "connecting") {
    statusIndicator.classList.add("connecting");
    statusText.textContent = "Connecting to DUCK-E...";
    buttonText.innerHTML = '<span class="spinner"></span>';
    toggleButton.disabled = true;
  } else if (status === "connected") {
    statusIndicator.classList.add("connected");
    statusText.textContent = "Connected - DUCK-E is listening";
    buttonText.textContent = "Disconnect";
    toggleButton.classList.add("connected");
    toggleButton.disabled = false;
  } else if (status === "disconnected") {
    statusIndicator.classList.add("disconnected");
    statusText.textContent = "Ready to Connect";
    buttonText.textContent = "Connect";
    toggleButton.disabled = false;
  }
};

// Format timestamp for display
const formatTime = (date) => {
  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
};

// Truncate long strings for display
const truncateString = (str, maxLength = 200) => {
  if (!str) return '';
  if (str.length <= maxLength) return str;
  return str.substring(0, maxLength) + '...';
};

// Add tool call to the log
const addToolCall = (toolName, params, response) => {
  const timestamp = new Date();

  toolCallLog.push({
    toolName,
    params,
    response,
    timestamp
  });

  // Update UI
  updateToolLogDisplay();
};

// Update the tool log display
const updateToolLogDisplay = () => {
  const toolCount = document.getElementById('tool-count');
  const toolLogEmpty = document.getElementById('tool-log-empty');
  const toolLogEntries = document.getElementById('tool-log-entries');
  const clearButton = document.getElementById('clear-log');

  // Update count badge
  toolCount.textContent = toolCallLog.length;

  if (toolCallLog.length === 0) {
    toolLogEmpty.style.display = 'block';
    toolLogEntries.style.display = 'none';
    clearButton.style.display = 'none';
  } else {
    toolLogEmpty.style.display = 'none';
    toolLogEntries.style.display = 'block';
    clearButton.style.display = 'block';

    // Render tool call entries (most recent first)
    toolLogEntries.innerHTML = toolCallLog
      .slice()
      .reverse()
      .map((call, index) => {
        const paramsStr = typeof call.params === 'object'
          ? JSON.stringify(call.params, null, 2)
          : String(call.params);

        const responseStr = typeof call.response === 'object'
          ? JSON.stringify(call.response, null, 2)
          : truncateString(String(call.response), 500);

        return `
          <div class="tool-entry">
            <div class="tool-entry-header">
              <span class="tool-name">${call.toolName}</span>
              <span class="tool-timestamp">${formatTime(call.timestamp)}</span>
            </div>
            <div>
              <div class="tool-label">Parameters</div>
              <div class="tool-params">${paramsStr}</div>
            </div>
            <div>
              <div class="tool-label">Response</div>
              <div class="tool-response">${responseStr}</div>
            </div>
          </div>
        `;
      })
      .join('');
  }
};

// Clear tool log
const clearToolLog = () => {
  toolCallLog = [];
  updateToolLogDisplay();
};

// Toggle tool log visibility
const toggleToolLog = () => {
  const content = document.getElementById('tool-log-content');
  const toggle = document.getElementById('tool-log-toggle');

  if (content.classList.contains('expanded')) {
    content.classList.remove('expanded');
    toggle.classList.remove('expanded');
  } else {
    content.classList.add('expanded');
    toggle.classList.add('expanded');
  }
};

// Setup WebRTC event handlers to capture tool calls
const setupToolCallListeners = (webRTCInstance) => {
  // Initialize pending tool calls storage
  if (!window.pendingToolCalls) {
    window.pendingToolCalls = {};
  }

  // Hook into the WebRTC message handler
  if (webRTCInstance && webRTCInstance.pc) {
    // Monitor data channel messages
    const originalOnDataChannel = webRTCInstance.pc.ondatachannel;

    webRTCInstance.pc.ondatachannel = function(event) {
      const dataChannel = event.channel;

      // Save original onmessage handler
      const originalOnMessage = dataChannel.onmessage;

      // Intercept data channel messages
      dataChannel.onmessage = function(msgEvent) {
        try {
          const message = JSON.parse(msgEvent.data);

          // Check for function call completion
          if (message.type === 'response.function_call_arguments.done') {
            const toolName = message.name || 'unknown';
            const params = message.arguments ? JSON.parse(message.arguments) : {};
            const callId = message.call_id || message.item_id;

            console.log('Tool call detected:', toolName, params);

            // Store pending call
            window.pendingToolCalls[callId] = {
              toolName,
              params,
              timestamp: new Date()
            };
          }

          // Check for function call output (response)
          if (message.type === 'conversation.item.create' && message.item?.type === 'function_call_output') {
            const callId = message.item.call_id;
            const response = message.item.output || 'No response';

            console.log('Tool response received:', callId, response);

            if (window.pendingToolCalls[callId]) {
              const { toolName, params } = window.pendingToolCalls[callId];
              addToolCall(toolName, params, response);
              delete window.pendingToolCalls[callId];
            }
          }
        } catch (e) {
          // Not JSON or not relevant
          console.debug('Non-JSON or irrelevant message:', e);
        }

        // Call original handler
        if (originalOnMessage) {
          return originalOnMessage.call(this, msgEvent);
        }
      };

      // Call original ondatachannel handler
      if (originalOnDataChannel) {
        return originalOnDataChannel.call(this, event);
      }
    };
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

      await webRTC.connect();

      // Setup tool call monitoring
      setupToolCallListeners(webRTC);

      updateUI("connected");
      connectionStatus = true;
    } catch (error) {
      console.error("Connection error:", error);
      updateUI("disconnected");
      connectionStatus = false;

      // Show error notification (you could enhance this with a toast/notification)
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
  // Attach the toggle function to the button click.
  const toggleButton = document.getElementById("toggle-connection");
  toggleButton.addEventListener("click", toggleConnection);

  // Setup tool log toggle
  const toolLogHeader = document.getElementById("tool-log-header");
  toolLogHeader.addEventListener("click", toggleToolLog);

  // Setup clear log button
  const clearButton = document.getElementById("clear-log");
  clearButton.addEventListener("click", (e) => {
    e.stopPropagation(); // Prevent toggle
    clearToolLog();
  });

  // Initialize UI
  updateUI("disconnected");
  updateToolLogDisplay();
});
