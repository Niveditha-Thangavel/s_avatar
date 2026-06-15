/**
 * STTManager – real-time Speech-to-Text via WebSocket
 * Also accumulates recorded PCM for the unified /api/v1/chat endpoint.
 *
 * Flow (legacy):
 *  1. start()  → open WS, request mic, stream PCM frames to server
 *  2. stop()   → send { type:"stop" }  → server transcribes → reply via WS
 *
 * Flow (unified):
 *  1. start()  → record mic, accumulate PCM locally
 *  2. stop()   → stop mic, return a WAV Blob for POST /api/v1/chat
 *  3. getRecordingBlob() → returns Promise<Blob> of WAV audio
 */
export class STTManager {
  constructor(wsUrl) {
    this.wsUrl  = wsUrl;
    this.ws     = null;
    this.audioCtx    = null;
    this.micStream   = null;
    this.workletNode = null;
    this.isListening = false;

    // Callbacks — set these before calling start()
    this.onTranscript  = null;
    this.onReply       = null;
    this.onStatusChange = null;
    this.onError       = null;

    this._targetSampleRate = 16_000;

    // Accumulated PCM chunks (Int16Array buffers) for unified API
    this._recordedChunks = [];
  }

  // ── Public API ────────────────────────────────────────────────────────────

  async start(language = null) {
    if (this.isListening) return;

    this._recordedChunks = [];

    // Open WebSocket for legacy STT pipeline (transcript display + reply)
    this.ws = new WebSocket(this.wsUrl);
    this.ws.binaryType = 'arraybuffer';

    await new Promise((resolve, reject) => {
      this.ws.onopen  = () => resolve();
      this.ws.onerror = () => reject(new Error('STT WebSocket failed to connect'));
    });

    this.ws.onmessage = (e) => this._handleServerMessage(e);
    this.ws.onerror   = ()  => this._emit('error', 'STT WebSocket error');
    this.ws.onclose   = ()  => {
      this._teardownAudio();
      this._emit('statusChange', 'idle');
    };

    this.ws.send(JSON.stringify({ type: 'config', language }));

    // Microphone
    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: { ideal: this._targetSampleRate },
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
    } catch (err) {
      this.ws.close();
      throw new Error(`Microphone access denied: ${err.message}`);
    }

    // AudioContext + AudioWorklet
    this.audioCtx = new AudioContext({ sampleRate: this._targetSampleRate });

    const workletCode = `
      class PCMProcessor extends AudioWorkletProcessor {
        process(inputs) {
          const ch = inputs[0]?.[0];
          if (!ch) return true;
          const int16 = new Int16Array(ch.length);
          for (let i = 0; i < ch.length; i++) {
            const s = Math.max(-1, Math.min(1, ch[i]));
            int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
          }
          this.port.postMessage(int16.buffer, [int16.buffer]);
          return true;
        }
      }
      registerProcessor('pcm-processor', PCMProcessor);
    `;
    const blob = new Blob([workletCode], { type: 'application/javascript' });
    const url  = URL.createObjectURL(blob);
    await this.audioCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    const source = this.audioCtx.createMediaStreamSource(this.micStream);
    this.workletNode = new AudioWorkletNode(this.audioCtx, 'pcm-processor');
    this.workletNode.port.onmessage = (e) => {
      // Accumulate for unified API
      this._recordedChunks.push(new Int16Array(e.data));

      // Also stream to server for legacy WS pipeline
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(e.data);
      }
    };
    source.connect(this.workletNode);

    this.isListening = true;
    this._emit('statusChange', 'listening');
  }

  stop() {
    if (!this.isListening) return;
    this.isListening = false;

    this._teardownAudio();

    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'stop' }));
    }

    this._emit('statusChange', 'processing');
  }

  cancel() {
    this.isListening = false;
    this._teardownAudio();
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'cancel' }));
      this.ws.close();
    }
    this._recordedChunks = [];
    this._emit('statusChange', 'idle');
  }

  /**
   * Get accumulated recording as a WAV Blob for the unified /api/v1/chat endpoint.
   * @returns {Promise<Blob>} WAV blob (16-bit PCM, 16kHz mono)
   */
  async getRecordingBlob() {
    if (this._recordedChunks.length === 0) {
      throw new Error('No audio recorded');
    }

    // Concatenate all Int16 chunks
    const totalLen = this._recordedChunks.reduce((s, c) => s + c.length, 0);
    const allPCM = new Int16Array(totalLen);
    let offset = 0;
    for (const chunk of this._recordedChunks) {
      allPCM.set(chunk, offset);
      offset += chunk.length;
    }

    const sr = this._targetSampleRate;
    const numChannels = 1;
    const bitsPerSample = 16;
    const byteRate = sr * numChannels * bitsPerSample / 8;
    const blockAlign = numChannels * bitsPerSample / 8;
    const dataSize = allPCM.byteLength;
    const headerSize = 44;
    const totalSize = headerSize + dataSize;

    const buffer = new ArrayBuffer(totalSize);
    const view = new DataView(buffer);

    // WAV header
    const writeStr = (off, str) => {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(off + i, str.charCodeAt(i));
      }
    };
    writeStr(0, 'RIFF');
    view.setUint32(4, totalSize - 8, true);
    writeStr(8, 'WAVE');
    writeStr(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sr, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);
    writeStr(36, 'data');
    view.setUint32(40, dataSize, true);

    // PCM data
    new Int16Array(buffer, headerSize, allPCM.length).set(allPCM);

    return new Blob([buffer], { type: 'audio/wav' });
  }

  // ── Private ───────────────────────────────────────────────────────────────

  _handleServerMessage(event) {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    switch (msg.type) {
      case 'transcript':
        this._emit('transcript', msg.text);
        break;

      case 'reply':
        this._emit('reply', msg);
        break;

      case 'status':
        this._emit('statusChange', msg.data);
        if (msg.data === 'stopped' || msg.data === 'cancelled') {
          this.ws?.close();
        }
        break;

      case 'error':
        this._emit('error', msg.message);
        this.ws?.close();
        break;
    }
  }

  _teardownAudio() {
    if (this.micStream) {
      this.micStream.getTracks().forEach((t) => t.stop());
      this.micStream = null;
    }
    if (this.audioCtx && this.audioCtx.state !== 'closed') {
      this.audioCtx.close();
      this.audioCtx = null;
    }
    this.workletNode = null;
  }

  _emit(event, data) {
    const key = `on${event.charAt(0).toUpperCase()}${event.slice(1)}`;
    if (typeof this[key] === 'function') this[key](data);
  }
}
