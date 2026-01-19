let webRTC; // Global variable for our WebRTC instance.
let connectionStatus = false; // false means not connected
let isMuted = false; // Audio mute state

// Load mute state from localStorage
const loadMuteState = () => {
  const saved = localStorage.getItem('duck-e-muted');
  return saved === 'true';
};

// Save mute state to localStorage
const saveMuteState = (muted) => {
  localStorage.setItem('duck-e-muted', muted.toString());
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

// Toggle mute state
const toggleMute = async () => {
  if (!webRTC || !webRTC.audioContext) {
    console.warn('No active audio context to mute');
    return;
  }

  try {
    if (isMuted) {
      // Unmute - resume audio context
      await webRTC.audioContext.resume();
      isMuted = false;
    } else {
      // Mute - suspend audio context
      await webRTC.audioContext.suspend();
      isMuted = true;
    }

    updateMuteUI(isMuted);
    saveMuteState(isMuted);
    console.log(`Audio ${isMuted ? 'muted' : 'unmuted'}`);
  } catch (error) {
    console.error('Error toggling mute:', error);
  }
};

// Function to update the UI elements based on connection status.
const updateUI = (status) => {
  const statusIndicator = document.getElementById("status-indicator");
  const statusText = document.getElementById("status-text");
  const buttonText = document.getElementById("button-text");
  const toggleButton = document.getElementById("toggle-connection");
  const muteBtn = document.getElementById("mute-btn");

  // Remove all status classes
  statusIndicator.classList.remove("connecting", "connected", "disconnected");
  toggleButton.classList.remove("connected");

  if (status === "connecting") {
    statusIndicator.classList.add("connecting");
    statusText.textContent = "Connecting to DUCK-E...";
    buttonText.innerHTML = '<span class="spinner"></span>';
    toggleButton.disabled = true;
    muteBtn.disabled = true;
  } else if (status === "connected") {
    statusIndicator.classList.add("connected");
    statusText.textContent = "Connected - DUCK-E is listening";
    buttonText.textContent = "Disconnect";
    toggleButton.classList.add("connected");
    toggleButton.disabled = false;
    muteBtn.disabled = false;
  } else if (status === "disconnected") {
    statusIndicator.classList.add("disconnected");
    statusText.textContent = "Ready to Connect";
    buttonText.textContent = "Connect";
    toggleButton.disabled = false;
    muteBtn.disabled = true;
    // Reset mute state on disconnect
    isMuted = false;
    updateMuteUI(false);
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

      updateUI("connected");
      connectionStatus = true;

      // Apply saved mute state after connection
      if (isMuted && webRTC.audioContext) {
        await webRTC.audioContext.suspend();
        console.log('Applied saved mute state');
      }
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

  // Attach mute button listener
  const muteBtn = document.getElementById("mute-btn");
  muteBtn.addEventListener("click", toggleMute);

  // Load saved mute preference (will be applied when connected)
  isMuted = loadMuteState();
  updateMuteUI(isMuted);

  // Initialize UI
  updateUI("disconnected");
});
