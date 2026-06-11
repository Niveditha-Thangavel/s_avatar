/**
 * STTManager – real-time Speech-to-Text via WebSocket
 *
 * Flow:
 *  1. start()  → open WS, request mic, stream PCM frames to server
 *  2. stop()   → send { type:"stop" }  (keeps socket OPEN)
 *              → server runs Whisper on the full buffer
 *              → server sends transcript + reply back
 *              → onTranscript / onReply fire
 *              → socket closes only after "stopped" status arrives
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
    this.onTranscript  = null;   // (text: string) what the user said
    this.onReply       = null;   // (text: string) what the avatar should say
    this.onStatusChange = null;  // (status: string)
    this.onError       = null;   // (msg: string)

    this._targetSampleRate = 16_000;
  }

  // ── Public API ────────────────────────────────────────────────────────────

  async start(language = null) {
    if (this.isListening) return;

    // 1. Open WebSocket — keep it open until server sends "stopped"
    this.ws = new WebSocket(this.wsUrl);
    this.ws.binaryType = 'arraybuffer';

    await new Promise((resolve, reject) => {
      this.ws.onopen  = () => resolve();
      this.ws.onerror = () => reject(new Error('STT WebSocket failed to connect'));
    });

    this.ws.onmessage = (e) => this._handleServerMessage(e);
    this.ws.onerror   = ()  => this._emit('error', 'STT WebSocket error');
    this.ws.onclose   = ()  => {
      // Socket closed — clean up audio resources if still running
      this._teardownAudio();
      this._emit('statusChange', 'idle');
    };

    // 2. Send language config
    this.ws.send(JSON.stringify({ type: 'config', language }));

    // 3. Microphone
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

    // 4. AudioContext + AudioWorklet (inline, no extra file needed)
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

    // 5. mic → worklet → WebSocket
    const source = this.audioCtx.createMediaStreamSource(this.micStream);
    this.workletNode = new AudioWorkletNode(this.audioCtx, 'pcm-processor');
    this.workletNode.port.onmessage = (e) => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(e.data);
      }
    };
    source.connect(this.workletNode);
    // intentionally NOT connected to destination — no mic feedback

    this.isListening = true;
    this._emit('statusChange', 'listening');
  }

  /**
   * Stop recording and signal the server to transcribe.
   * DOES NOT close the socket — the socket stays open until the server
   * finishes Whisper + chat and sends back { type:"status", data:"stopped" }.
   */
  stop() {
    if (!this.isListening) return;
    this.isListening = false;

    // Stop the mic stream so the user sees the recording indicator go off
    this._teardownAudio();

    // Tell the server to flush the buffer and run Whisper.
    // Keep the socket open so the reply can come back.
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'stop' }));
      // Socket will be closed by _handleServerMessage once "stopped" arrives
    }

    this._emit('statusChange', 'processing');
  }

  /**
   * Discard everything without transcribing.
   */
  cancel() {
    this.isListening = false;
    this._teardownAudio();
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'cancel' }));
      this.ws.close();
    }
    this._emit('statusChange', 'idle');
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
        this._emit('reply', msg.text);
        break;

      case 'status':
        this._emit('statusChange', msg.data);
        // Server sends "stopped" as the final state after transcript + reply
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
