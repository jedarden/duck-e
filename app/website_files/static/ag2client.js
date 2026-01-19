"use strict";
var ag2client = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/index.ts
  var index_exports = {};
  __export(index_exports, {
    WebRTC: () => WebRTC,
    WebsocketAudio: () => WebsocketAudio
  });

  // src/websocketAudio.ts
  var WebsocketAudio = class {
    onDisconnect;
    webSocketUrl;
    socket;
    // audio out
    outAudioContext;
    sourceNode;
    bufferQueue;
    // Queue to store audio buffers
    isPlaying;
    // Flag to check if audio is playing
    // audio in
    inAudioContext;
    processorNode;
    stream;
    bufferSize;
    // Define the buffer size for capturing chunks
    constructor(webSocketUrl) {
      this.onDisconnect = () => {
        console.log("WebSocket disconnected.");
      };
      this.webSocketUrl = webSocketUrl;
      this.socket = null;
      this.outAudioContext = null;
      this.sourceNode = null;
      this.bufferQueue = [];
      this.isPlaying = false;
      this.inAudioContext = null;
      this.processorNode = null;
      this.stream = null;
      this.bufferSize = 8192;
    }
    // Initialize WebSocket and start receiving audio data
    async start() {
      try {
        this.socket = new WebSocket(this.webSocketUrl);
        this.socket.onopen = () => {
          console.log("WebSocket connected.");
          const sessionStarted = {
            event: "start",
            start: {
              streamSid: crypto.randomUUID()
            }
          };
          this.socket?.send(JSON.stringify(sessionStarted));
          console.log("sent session start");
        };
        this.socket.onclose = () => {
          this.onDisconnect();
        };
        this.socket.onmessage = async (event) => {
          console.log("Received web socket message");
          const message = JSON.parse(event.data);
          if (message.event == "media") {
            const bufferString = atob(message.media.payload);
            const byteArray = new Uint8Array(bufferString.length);
            for (let i = 0; i < bufferString.length; i++) {
              byteArray[i] = bufferString.charCodeAt(i);
            }
            this.queuePcmData(byteArray.buffer);
            if (!this.isPlaying) {
              this.playFromQueue();
            }
          }
        };
        this.outAudioContext = new (window.AudioContext || window.webkitAudioContext)();
        console.log("Audio player initialized.");
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { sampleRate: 24e3 }
        });
        this.stream = stream;
        this.inAudioContext = new AudioContext({ sampleRate: 24e3 });
        const sourceNode = this.inAudioContext.createMediaStreamSource(stream);
        this.processorNode = this.inAudioContext.createScriptProcessor(
          this.bufferSize,
          1,
          1
        );
        this.processorNode.onaudioprocess = (event) => {
          const inputBuffer = event.inputBuffer;
          const audioData = this.extractPcm16Data(inputBuffer);
          const byteArray = new Uint8Array(audioData);
          const bufferString = String.fromCharCode(...byteArray);
          const audioBase64String = btoa(bufferString);
          if (this.socket?.readyState === WebSocket.OPEN) {
            const audioMessage = {
              event: "media",
              media: {
                timestamp: Date.now(),
                payload: audioBase64String
              }
            };
            this.socket.send(JSON.stringify(audioMessage));
          }
        };
        sourceNode.connect(this.processorNode);
        this.processorNode.connect(this.inAudioContext.destination);
        console.log("Audio capture started.");
      } catch (err) {
        console.error("Error initializing audio player:", err);
      }
    }
    // Stop receiving and playing audio
    stop() {
      this.stop_out();
      this.stop_in();
    }
    stop_out() {
      if (this.socket) {
        this.socket.close();
      }
      if (this.outAudioContext) {
        this.outAudioContext.close();
      }
      console.log("Audio player stopped.");
    }
    stop_in() {
      if (this.processorNode) {
        this.processorNode.disconnect();
      }
      if (this.inAudioContext) {
        this.inAudioContext.close();
      }
      if (this.socket) {
        this.socket.close();
      }
      if (this.stream) {
        this.stream.getTracks().forEach((track) => track.stop());
      }
      console.log("Audio capture stopped.");
    }
    // Queue PCM data for later playback
    queuePcmData(pcmData) {
      this.bufferQueue.push(pcmData);
    }
    // Play audio from the queue
    async playFromQueue() {
      if (this.bufferQueue.length === 0) {
        this.isPlaying = false;
        return;
      }
      this.isPlaying = true;
      const pcmData = this.bufferQueue.shift();
      const audioBuffer = await this.decodePcm16Data(pcmData);
      const source = this.outAudioContext?.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(this.outAudioContext?.destination);
      source.onended = () => {
        this.playFromQueue();
      };
      source.start();
    }
    // Decode PCM 16-bit data into AudioBuffer
    async decodePcm16Data(pcmData) {
      const audioData = new Float32Array(pcmData.byteLength / 2);
      const dataView = new DataView(pcmData);
      for (let i = 0; i < audioData.length; i++) {
        const pcm16 = dataView.getInt16(i * 2, true);
        audioData[i] = pcm16 / 32768;
      }
      const audioBuffer = this.outAudioContext?.createBuffer(
        1,
        audioData.length,
        24e3
      );
      audioBuffer.getChannelData(0).set(audioData);
      return audioBuffer;
    }
    // Convert audio buffer to PCM 16-bit data
    extractPcm16Data(buffer) {
      const sampleRate = buffer.sampleRate;
      const length = buffer.length;
      const pcmData = new Int16Array(length);
      for (let i = 0; i < length; i++) {
        const channelData = buffer.getChannelData(0);
        if (channelData) {
          const cdi = channelData[i];
          if (cdi) {
            pcmData[i] = Math.max(-32768, Math.min(32767, cdi * 32767));
          }
        }
      }
      const pcmBuffer = new ArrayBuffer(pcmData.length * 2);
      const pcmView = new DataView(pcmBuffer);
      for (let i = 0; i < pcmData.length; i++) {
        const pcmData_i = pcmData[i];
        if (pcmData_i) {
          pcmView.setInt16(i * 2, pcmData_i, true);
        }
      }
      return pcmBuffer;
    }
  };

  // src/webRTC.ts
  var WebRTC = class {
    ag2SocketUrl;
    microphone;
    ws;
    pc;
    onDisconnect;
    onMessage;
    constructor(ag2SocketUrl, microphone) {
      this.ag2SocketUrl = ag2SocketUrl;
      this.microphone = microphone;
      this.ws = null;
      this.pc = null;
      this.onDisconnect = () => {
        console.log("WebRTC disconnected");
      };
      this.onMessage = null;
    }
    async close() {
      if (this.microphone) {
        this.microphone?.stop();
        this.microphone = void 0;
      }
      if (this.ws) {
        const ws = this.ws;
        this.ws = null;
        ws.close();
      }
      if (this.pc) {
        const pc = this.pc;
        this.pc = null;
        pc.close();
      }
    }
    async connect() {
      let dc = null;
      const quedMessages = [];
      let resolve, reject;
      let completed = new Promise((_resolve, _reject) => {
        resolve = _resolve;
        reject = _reject;
      });
      this.pc = new RTCPeerConnection();
      async function openRTC(init_message, webRTC, pc, ws, mic, resolve2, reject2) {
        const data = init_message.config;
        const EPHEMERAL_KEY = data.client_secret.value;
        const audioEl = document.createElement("audio");
        audioEl.autoplay = true;
        pc.ontrack = (e) => {
          const audioTrack = e.streams[0];
          if (audioTrack) {
            audioEl.srcObject = audioTrack;
          }
        };
        mic.enabled = false;
        pc.addTrack(mic);
        pc.onconnectionstatechange = (e) => {
          if (pc.connectionState === "disconnected") {
            webRTC.close();
            webRTC.onDisconnect();
          }
        };
        const _dc = pc.createDataChannel("oai-events");
        _dc.addEventListener("message", (e) => {
          let message;
          try {
            message = JSON.parse(e.data);
          } catch (error) {
            console.error("Error parsing message", e.data, error);
            return;
          }
          // Forward all messages to onMessage callback for transcript handling
          if (webRTC.onMessage) {
            try {
              webRTC.onMessage({ data: e.data, message: message });
            } catch (callbackError) {
              console.error("Error in onMessage callback", callbackError);
            }
          }
          if (message.type && message.type.includes("function")) {
            console.log("WebRTC function message", message);
            try {
              ws.send(e.data);
            } catch (error) {
              console.error(
                "Error sending function message to AG2 backend",
                error
              );
              webRTC.close();
            }
          }
        });
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        const baseUrl = "https://api.openai.com/v1/realtime";
        const model = data.model;
        const sdpResponse = await fetch(`${baseUrl}?model=${model}`, {
          method: "POST",
          body: offer.sdp,
          headers: {
            Authorization: `Bearer ${EPHEMERAL_KEY}`,
            "Content-Type": "application/sdp"
          }
        });
        const answer = {
          type: "answer",
          sdp: await sdpResponse.text()
        };
        await pc.setRemoteDescription(answer);
        console.log("Connected to OpenAI WebRTC");
        _dc.onopen = (e) => {
          console.log("Data connection opened.");
          for (const init_chunk of init_message.init) {
            _dc.send(JSON.stringify(init_chunk));
          }
          console.log("Sent init chunks to OpenAI WebRTC");
          // Enable input audio transcription so user speech appears in transcript
          const transcriptionConfig = {
            type: "session.update",
            session: {
              input_audio_transcription: {
                model: "whisper-1"
              }
            }
          };
          _dc.send(JSON.stringify(transcriptionConfig));
          console.log("Enabled input audio transcription");
          for (const qmsg of quedMessages) {
            _dc.send(qmsg);
          }
          console.log("Sent queued messages to OpenAI WebRTC");
          mic.enabled = true;
          dc = _dc;
          resolve2();
        };
      }
      if (!this.microphone) {
        const ms = await navigator.mediaDevices.getUserMedia({
          audio: true
        });
        const microphone = ms.getTracks()[0];
        if (!microphone) {
          throw new Error("No microphone found");
        }
        this.microphone = microphone;
        microphone.enabled = false;
      }
      this.ws = new WebSocket(this.ag2SocketUrl);
      this.ws.onopen = (event) => {
        console.log("web socket opened");
      };
      this.ws.onclose = (event) => {
        this.close();
        this.onDisconnect();
      };
      this.ws.onmessage = async (event) => {
        try {
          const message = JSON.parse(event.data);
          console.info("Received Message from AG2 backend", message);
          const type = message.type;
          if (type === "ag2.init") {
            await openRTC(
              message,
              this,
              this.pc,
              this.ws,
              this.microphone,
              resolve,
              reject
            );
            return;
          }
          const messageJSON = JSON.stringify(message);
          if (dc) {
            dc.send(messageJSON);
          } else {
            console.log("DC not ready yet, queueing", message);
            quedMessages.push(messageJSON);
          }
        } catch (error) {
          console.error("Error processing websocket message", event.data, error);
        }
      };
      await completed;
      console.log("WebRTC fully operational");
    }
  };
  return __toCommonJS(index_exports);
})();
