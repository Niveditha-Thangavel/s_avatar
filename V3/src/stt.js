/**
 * S2SManager — Speech-to-Speech pipeline client
 *
 * Connects to the server's /ws/s2s WebSocket endpoint and manages the full
 * pipeline for a session:
 *
 *   Client mic (PCM) → /ws/s2s → Vexyl STT → IndicTrans2 Translation → OmniVoice TTS → Client speaker
 *
 * Protocol:
 *   Client → Server:
 *     { type: "start", lang: "hi-IN", session_id: "..." }  — initiate session
 *     [binary Int16 PCM @ 16kHz mono]                       — audio stream
 *     { type: "stop" }                                      — end recording
 *     { type: "cancel" }                                    — abort session
 *
 *   Server → Client:
 *     { type: "transcript", text, lang }                    — real-time STT
 *     { type: "tts_start",  seq, text, lang }               — sentence starting
 *     { type: "audio_chunk", seq, sample_rate, byte_length }— audio metadata
 *     [binary PCM bytes]                                     — raw audio data
 *     { type: "tts_end",   seq }                            — sentence done
 *     { type: "pipeline_status", ... }                      — heartbeat
 *     { type: "error", message }                            — error
 */
export class S2SManager {
  /**
   * @param {string} wsUrl  Full WebSocket URL, e.g. "ws://localhost:8765/ws/s2s"
   */
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.ws = null;

    this.audioCtx = null;
    this.micStream = null;
    this.workletNode = null;
    this.isListening = false;

    // Registered callbacks — set before calling start()
    this.onTranscript       = null;   // (text: string, lang: string) => void
    this.onTtsStart         = null;   // (seq: number, text: string)  => void
    this.onAudioChunk       = null;   // (seq: number, pcmBytes: ArrayBuffer, sampleRate: number) => void
    this.onTtsEnd           = null;   // (seq: number) => void
    this.onBlendshapeMatrix = null;   // (seq: number, matrix: Array) => void
    this.onStatusChange     = null;   // (status: string) => void
    this.onError            = null;   // (message: string) => void

    this._targetSampleRate = 16_000;
    this._sessionId = '';
    this._pendingMeta = null;   // metadata for next binary audio frame
  }

  // ── Public API ──────────────────────────────────────────────────────────────

  /**
   * Open the S2S WebSocket, request mic access, and start streaming.
   *
   * @param {string} lang       BCP-47 language code, e.g. "hi-IN"
   * @param {string} sessionId  Optional session ID; auto-generated if omitted
   */
  async start(lang = 'hi-IN', sessionId = '') {
    if (this.isListening) return;

    this._sessionId = sessionId || `s2s_${Date.now()}`;
    this._pendingMeta = null;

    // Open WebSocket
    this.ws = new WebSocket(this.wsUrl);
    this.ws.binaryType = 'arraybuffer';

    await new Promise((resolve, reject) => {
      this.ws.onopen  = () => resolve();
      this.ws.onerror = () => reject(new Error('S2S WebSocket failed to connect'));
    });

    this.ws.onmessage = (e) => this._handleMessage(e);
    this.ws.onerror   = ()  => this._emit('error', 'S2S WebSocket error');
    this.ws.onclose   = ()  => {
      this._teardownAudio();
      this._emit('statusChange', 'idle');
    };

    // Send start handshake
    this.ws.send(JSON.stringify({
      type:       'start',
      lang,
      session_id: this._sessionId,
    }));

    // Microphone
    try {
      this.micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate:        { ideal: this._targetSampleRate },
          channelCount:      1,
          echoCancellation:  true,
          noiseSuppression:  true,
          autoGainControl:   true,
        },
        video: false,
      });
    } catch (err) {
      this.ws.close();
      throw new Error(`Microphone access denied: ${err.message}`);
    }

    // AudioContext + AudioWorklet PCM processor
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
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(e.data);  // stream Int16 PCM to server
      }
    };
    source.connect(this.workletNode);

    this.isListening = true;
    this._emit('statusChange', 'listening');
  }

  /** Stop recording and signal the server to finish transcription. */
  stop() {
    if (!this.isListening) return;
    this.isListening = false;
    this._teardownAudio();
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'stop' }));
    }
    this._emit('statusChange', 'processing');
  }

  /** Cancel the session immediately. */
  cancel() {
    this.isListening = false;
    this._teardownAudio();
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'cancel' }));
      this.ws.close();
    }
    this._emit('statusChange', 'idle');
  }

  // ── Internal ────────────────────────────────────────────────────────────────

  _handleMessage(event) {
    // Binary frame: raw PCM audio belonging to the last audio_chunk metadata
    if (event.data instanceof ArrayBuffer) {
      if (this._pendingMeta) {
        const { seq, sample_rate } = this._pendingMeta;
        this._pendingMeta = null;
        this._emit('audioChunk', seq, event.data, sample_rate);
      }
      return;
    }

    // Text frame: JSON control messages
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    switch (msg.type) {
      case 'transcript':
        this._emit('transcript', msg.text, msg.lang);
        break;

      case 'tts_start':
        this._emit('ttsStart', msg.seq, msg.text);
        this._emit('statusChange', 'speaking');
        break;

      case 'audio_chunk':
        // Store metadata; the next binary frame is the actual PCM bytes
        this._pendingMeta = { seq: msg.seq, sample_rate: msg.sample_rate };
        break;

      case 'tts_end':
        this._emit('ttsEnd', msg.seq);
        break;

      case 'blendshape_matrix':
        this._emit('blendshapeMatrix', msg.seq, msg.matrix);
        break;

      case 'pipeline_status':
        // Heartbeat — no action needed
        break;

      case 'error':
        this._emit('error', msg.message || 'Unknown server error');
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

  _emit(event, ...args) {
    const key = `on${event.charAt(0).toUpperCase()}${event.slice(1)}`;
    if (typeof this[key] === 'function') {
      this[key](...args);
    } else {
      // Fallback for uppercase TTS
      const upperKey = key.replace('Tts', 'TTS');
      if (typeof this[upperKey] === 'function') {
        this[upperKey](...args);
      }
    }
  }
}
