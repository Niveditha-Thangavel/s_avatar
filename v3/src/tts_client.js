/**
 * TTSClient – streams speech from the server's /ws/tts WebSocket.
 *
 * Protocol (client → server):
 *   { type: "speak", text, voice, instruct, speed, numStep }
 *   { type: "stop" }
 *
 * Protocol (server → client):
 *   Text frame: { type: "status", data: "generating"|"complete"|"stopped"|"error", [message] }
 *   Text frame: { type: "chunk",  text, sampleRate, byteLength }  ← immediately followed by:
 *   Binary frame: raw Float32-LE PCM audio bytes
 *
 * When a chunk arrives the PCM bytes are handed to LipSyncManager via
 * the onChunk callback so audio scheduling and viseme alignment work
 * exactly as before.
 */
export class TTSClient {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;
    this._pendingChunkHeader = null; // stores the JSON header until binary arrives

    // Callbacks
    this.onChunk = null;        // ({ audio: Float32Array, sampleRate: number, text: string }) => void
    this.onStatusChange = null; // (status: string) => void
    this.onError = null;        // (msg: string) => void

    this._connected = false;
  }

  /**
   * Lazily open the WebSocket (called on first speak).
   */
  async connect() {
    if (this._connected) return;

    this.ws = new WebSocket(this.wsUrl);
    this.ws.binaryType = 'arraybuffer';

    await new Promise((resolve, reject) => {
      this.ws.onopen = () => { this._connected = true; resolve(); };
      this.ws.onerror = () => reject(new Error('TTS WebSocket failed to connect'));
    });

    this.ws.onmessage = (event) => this._handleMessage(event);
    this.ws.onerror = () => this._emit('error', 'TTS WebSocket error');
    this.ws.onclose = () => {
      this._connected = false;
    };
  }

  /**
   * Send a speak request. Awaits connection if needed.
   */
  async speak({ text, voice = null, instruct = null, speed = 1.0, numStep = 16 }) {
    await this.connect();
    this.ws.send(
      JSON.stringify({ type: 'speak', text, voice, instruct, speed, numStep })
    );
    this._emit('statusChange', 'generating');
  }

  /**
   * Send stop signal to the server.
   */
  stop() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'stop' }));
    }
  }

  // ── Private ─────────────────────────────────────────────────────────────

  _handleMessage(event) {
    // Binary frame → PCM audio data for the last chunk header
    if (event.data instanceof ArrayBuffer) {
      if (!this._pendingChunkHeader) return;

      const header = this._pendingChunkHeader;
      this._pendingChunkHeader = null;

      const audio = new Float32Array(event.data);
      this._emit('chunk', { audio, sampleRate: header.sampleRate, text: header.text });
      return;
    }

    // Text frame → JSON control / header
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }

    switch (msg.type) {
      case 'chunk':
        // Store header; next message will be binary PCM
        this._pendingChunkHeader = { text: msg.text, sampleRate: msg.sampleRate };
        break;

      case 'status':
        this._emit('statusChange', msg.data);
        break;

      case 'error':
        this._emit('error', msg.message);
        break;
    }
  }

  _emit(event, data) {
    const cb = this[`on${event.charAt(0).toUpperCase()}${event.slice(1)}`];
    if (typeof cb === 'function') cb(data);
  }
}
