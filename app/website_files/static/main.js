let webRTC; // Global variable for our WebRTC instance.
let connectionStatus = false; // false means not connected

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

  // Initialize UI
  updateUI("disconnected");
});
