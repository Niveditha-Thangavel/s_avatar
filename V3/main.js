import { Avatar3D } from './src/avatar3d.js';
import { BehaviorManager } from './src/behavior.js';
import { STTManager } from './src/stt.js';

// ── Server config ─────────────────────────────────────────────────────────────
const getWsBase = () => {
  if (import.meta.env.VITE_SERVER_URL) {
    return import.meta.env.VITE_SERVER_URL;
  }
  const isLocal = typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');
  const host = isLocal ? 'localhost' : (import.meta.env.VITE_SERVER_HOST || 'localhost');
  const port = import.meta.env.VITE_SERVER_PORT || '8765';
  return `ws://${host}:${port}`;
};

const getHttpBase = () => {
  if (import.meta.env.VITE_HTTP_URL) {
    return import.meta.env.VITE_HTTP_URL;
  }
  const isLocal = typeof window !== 'undefined' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1');
  const host = isLocal ? 'localhost' : (import.meta.env.VITE_SERVER_HOST || 'localhost');
  const port = import.meta.env.VITE_SERVER_PORT || '8765';
  return `http://${host}:${port}`;
};

const WS_BASE   = getWsBase();
const HTTP_BASE = getHttpBase();

// Helper to resume/initialize AudioContext on user gesture
const ensureAudioContext = () => {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === 'suspended') {
    console.log('[Audio] Resuming AudioContext synchronously on user gesture...');
    audioContext.resume();
  }
  return audioContext;
};

// ── Globals ───────────────────────────────────────────────────────────────────
let avatar   = null;
let behavior = null;
let stt      = null;

// Audio playback for server-generated audio
let audioContext = null;
let activeAudioSource = null;
let audioStartTime = 0;
let isAudioPlaying = false;

let lastFrameTime = performance.now();
let frameCount = 0;
let fpsTimer   = 0;

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  injectConsoleHUD();

  // 3D scene + animation
  avatar   = new Avatar3D('canvas-container', '/avatar_head.glb');
  behavior = new BehaviorManager();

  // ── STT manager ───────────────────────────────────────────────────────────
  stt = new STTManager(`${WS_BASE}/ws/stt`);

  stt.onTranscript = (text) => {
    console.log('[STT] Heard:', text);
    const ta = document.getElementById('text-input');
    if (ta) ta.value = `🎤 You: ${text}`;
    updateProgressUI('💬 Got transcript — generating reply…', true);
  };

  stt.onReply = async (reply) => {
    console.log('[STT] Reply:', reply);
    const text = reply?.native_text || reply?.reply || (typeof reply === 'string' ? reply : '');

    const ta = document.getElementById('text-input');
    if (ta) ta.value = `🤖 Avatar: ${text}`;
    updateProgressUI('🔊 Generating speech…', true);
    _applyEmotion(reply?.emotion);
    _speakText(text);
  };

  stt.onStatusChange = (status) => {
    updateMicBadge(status);
    const labels = {
      listening:    '🎤 Listening…',
      processing:   '⏳ Processing audio…',
      transcribing: '📝 Transcribing…',
      thinking:     '🤔 Thinking…',
      stopped:      '',
      idle:         '',
    };
    if (labels[status] !== undefined) {
      updateProgressUI(labels[status], !!labels[status]);
    }
    if (status === 'idle' || status === 'stopped') {
      const btn = document.getElementById('btn-mic');
      if (btn) {
        btn.classList.remove('active', 'processing');
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🎤</span> Start Listening';
      }
    }
    if (['processing', 'transcribing', 'thinking'].includes(status)) {
      const btn = document.getElementById('btn-mic');
      if (btn) {
        btn.innerHTML = `<span class="btn-icon">⏳</span> ${status}…`;
        btn.disabled = true;
      }
    }
  };

  stt.onError = (msg) => console.error('[STT]', msg);

  // Bind UI + start render loop
  setupEventListeners();
  setupAvatarLoadingEvents();
  setupCalibrationSliders();
  requestAnimationFrame(renderLoop);
});

// ── Apply emotion to avatar + behavior ───────────────────────────────────────
function _applyEmotion(emotion) {
  if (!emotion) return;
  const valid = ['neutral', 'happy', 'sad', 'angry', 'surprised'];
  const e = valid.includes(emotion) ? emotion : 'neutral';
  if (behavior) behavior.currentEmotion = e;
  if (avatar)   avatar.setEmotion(e);
  // Update the dropdown to reflect current emotion
  const sel = document.getElementById('emotion-select');
  if (sel) sel.value = e;
  console.log(`[Emotion] Applied: ${e}`);
}
/**
 * Speak text via the server's /api/v1/chat endpoint.
 * Receives audio_url + animation_matrix, plays audio and drives blendshapes.
 */
async function _speakText(text) {
  if (!text?.trim()) return;

  _stopAudio();

  updateStatusBadge('generating');
  updateProgressUI('🔊 Generating speech…', true);

  try {
    const instruct = document.getElementById('instruct-input')?.value?.trim() || null;
    const speed    = parseFloat(document.getElementById('speed-range')?.value   || '1.0');
    const numStep  = parseInt(document.getElementById('quality-select')?.value  || '16', 10);

    await _speakViaLegacyTTS(text, instruct, speed, numStep);
  } catch (err) {
    console.error('[Speak]', err);
    updateStatusBadge('error');
    updateProgressUI('❌ TTS error', false);
  }
}

/**
 * Legacy TTS path: used for typed text input. Streams via /ws/tts WebSocket.
 * No animation matrix available — avatar stays in idle procedural motion.
 */
async function _speakViaLegacyTTS(text, instruct, speed, numStep) {
  return new Promise((resolve, reject) => {
    console.log('[TTS-WS] Connecting to:', `${WS_BASE}/ws/tts`);
    const ws = new WebSocket(`${WS_BASE}/ws/tts`);
    ws.binaryType = 'arraybuffer';

    const audioChunks = [];
    let animationMatrix = null;

    ws.onopen = () => {
      console.log('[TTS-WS] Connection opened, sending speak request for text:', text.substring(0, 40) + '...');
      ws.send(JSON.stringify({
        type: 'speak',
        text,
        instruct,
        speed,
        numStep,
      }));
    };

    ws.onmessage = (event) => {
      const isBinary = event.data instanceof ArrayBuffer;
      console.log('[TTS-WS] Message received. Binary:', isBinary, 'Type:', typeof event.data);
      if (isBinary) {
        console.log('[TTS-WS] Push binary chunk of size:', event.data.byteLength);
        audioChunks.push(new Float32Array(event.data));
        return;
      }

      try {
        const msg = JSON.parse(event.data);
        console.log('[TTS-WS] Received JSON type:', msg.type, 'data:', msg.data || '');
        if (msg.type === 'chunk') {
          // next message will be binary
        } else if (msg.type === 'status' && msg.data === 'complete') {
          animationMatrix = msg.animation_matrix || null;
          console.log('[TTS-WS] Complete message received. Matrix length:', animationMatrix?.length || 0);
          ws.close();
        } else if (msg.type === 'status' && msg.data === 'error') {
          reject(new Error(msg.message || 'TTS error'));
        }
      } catch (err) {
        console.error('[TTS-WS] JSON parsing error:', err);
      }
    };

    ws.onclose = () => {
      console.log('[TTS-WS] Connection closed. Total chunks:', audioChunks.length);
      if (audioChunks.length === 0) {
        console.warn('[TTS-WS] No audio chunks received!');
        resolve();
        return;
      }

      // Play the accumulated audio
      const totalLen = audioChunks.reduce((s, c) => s + c.length, 0);
      const combined = new Float32Array(totalLen);
      let offset = 0;
      for (const chunk of audioChunks) {
        combined.set(chunk, offset);
        offset += chunk.length;
      }

      console.log('[TTS-WS] Playing combined audio buffer of size:', combined.length);
      _playAudioBuffer(combined, 24000, animationMatrix);
      updateStatusBadge('speaking');
      updateProgressUI('▶️ Playing…', false);
      resolve();
    };

    ws.onerror = (err) => {
      console.error('[TTS-WS] WebSocket error:', err);
      reject(new Error('WebSocket error'));
    };
  });
}

/**
 * Play audio through Web Audio API and drive avatar from animation matrix.
 * IMPORTANT: audioStartTime and setAnimationMatrix must be set atomically —
 * both use the AudioContext clock so they are perfectly aligned.
 */
function _playAudioBuffer(audioData, sampleRate, animationMatrix) {
  _stopAudio();
  console.log('[Audio] _playAudioBuffer called. Data type:', audioData.constructor.name, 'Sample rate:', sampleRate, 'Matrix length:', animationMatrix?.length || 0);

  ensureAudioContext();

  let buffer;
  if (audioData instanceof AudioBuffer) {
    buffer = audioData;
  } else {
    buffer = audioContext.createBuffer(1, audioData.length, sampleRate);
    buffer.getChannelData(0).set(audioData);
  }

  const source = audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(audioContext.destination);

  // Capture the exact AudioContext start time BEFORE source.start()
  // then prime the animation matrix immediately — zero offset between audio and blendshapes.
  audioStartTime = audioContext.currentTime;
  isAudioPlaying = true;

  if (animationMatrix && animationMatrix.length > 0) {
    avatar.setAnimationMatrix(animationMatrix);
  }

  source.start(0);
  activeAudioSource = source;

  source.onended = () => {
    activeAudioSource = null;
    isAudioPlaying = false;
    avatar.clearAnimation();
    updateStatusBadge('idle');
    updateProgressUI('', false);
  };
}

function _stopAudio() {
  if (activeAudioSource) {
    try { activeAudioSource.stop(); } catch {}
    activeAudioSource = null;
  }
  isAudioPlaying = false;
  avatar.clearAnimation();
}

/**
 * Send recorded audio to /api/v1/chat for unified processing.
 */
async function _sendAudioToUnifiedAPI(audioBlob) {
  updateProgressUI('📤 Sending audio…', true);

  try {
    const formData = new FormData();
    formData.append('file', audioBlob, 'audio.wav');

    const res = await fetch(`${HTTP_BASE}/api/v1/chat`, {
      method: 'POST',
      body: formData,
    });

    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`API error ${res.status}: ${errText}`);
    }

    const data = await res.json();
    const { audio_url, animation_matrix, emotion } = data;

    // Apply emotion to avatar body + suit color
    _applyEmotion(emotion);

    updateProgressUI('🔊 Playing response…', true);

    // Fetch the audio from the returned URL
    const audioRes = await fetch(audio_url);
    const audioArrayBuffer = await audioRes.arrayBuffer();

    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }

    // Decode WAV container natively (handles sample rates and formats automatically)
    const decodedBuffer = await audioContext.decodeAudioData(audioArrayBuffer);

    _playAudioBuffer(decodedBuffer, decodedBuffer.sampleRate, animation_matrix);
    updateStatusBadge('speaking');
  } catch (err) {
    console.error('[UnifiedAPI]', err);
    updateStatusBadge('error');
    updateProgressUI(`❌ Error: ${err.message}`, false);
  }
}

// ── Event listeners ───────────────────────────────────────────────────────────
function setupEventListeners() {

  // Speak button — typed text
  document.getElementById('btn-speak')?.addEventListener('click', async () => {
    ensureAudioContext();
    const text = document.getElementById('text-input')?.value?.trim();
    if (!text) return;

    updateStatusBadge('generating');
    updateProgressUI('🔊 Generating speech…', true);

    try {
      const res = await fetch(`${HTTP_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const reply = await res.json();
      if (reply && (reply.native_text || reply.reply)) {
        const native = reply.native_text || reply.reply || text;
        document.getElementById('text-input').value = `🤖 Avatar: ${native}`;
        _applyEmotion(reply.emotion);
        _speakText(native);
        return;
      }
    } catch (_err) {
      console.warn('[Chat] Brain unavailable, speaking typed text directly');
    }

    _speakText(text);
  });

  // Stop button
  document.getElementById('btn-stop')?.addEventListener('click', () => {
    _stopAudio();
    updateStatusBadge('idle');
  });

  // Mic toggle
  const btnMic = document.getElementById('btn-mic');
  if (btnMic) {
    btnMic.addEventListener('click', async () => {
      ensureAudioContext();
      if (stt.isListening) {
        btnMic.classList.remove('active');
        btnMic.innerHTML = '<span class="btn-icon">⏳</span> Processing…';
        btnMic.disabled  = true;

        // Stop recording
        stt.stop();

        // Use unified /api/v1/chat endpoint exclusively.
        // Permanently suppress the legacy onReply → _speakViaLegacyTTS path —
        // the unified API already returns audio + animation matrix.
        // The typed text "Speak" button bypasses STT entirely, so it's safe.
        stt.onReply = (reply) => {
          console.log('[STT] Reply (suppressed):', reply);
          const text = reply?.native_text || reply?.reply || '';
          const ta = document.getElementById('text-input');
          if (ta) ta.value = `🤖 Avatar: ${text}`;
        };

        try {
          const blob = await stt.getRecordingBlob();
          updateProgressUI('📤 Sending to server…', true);
          await _sendAudioToUnifiedAPI(blob);
        } catch (err) {
          console.error('[Mic->Unified]', err);
        }
      } else {
        try {
          const lang = document.getElementById('lang-select')?.value || null;
          await stt.start(lang);
          btnMic.classList.add('active');
          btnMic.innerHTML = '<span class="btn-icon">🔴</span> Stop & Transcribe';
        } catch (err) {
          console.error('[Mic]', err.message);
          alert(err.message);
        }
      }
    });
  }

  // Speed slider
  const speedRange = document.getElementById('speed-range');
  const speedVal   = document.getElementById('speed-val');
  speedRange?.addEventListener('input', () => {
    if (speedVal) speedVal.innerText = speedRange.value;
  });

  // Emotion select
  document.getElementById('emotion-select')?.addEventListener('change', (e) => {
    if (behavior) behavior.currentEmotion = e.target.value;
    if (avatar)   avatar.setEmotion(e.target.value);
  });

  // Custom GLB upload
  document.getElementById('image-upload')?.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (!/\.(glb|gltf)$/i.test(file.name)) {
      alert('Please select a .glb or .gltf file.');
      return;
    }
    avatar.loadGLBModel(URL.createObjectURL(file));
  });

  // Reset avatar
  document.getElementById('btn-reset-avatar')?.addEventListener('click', () => {
    avatar.loadGLBModel('/avatar_head.glb');
  });
}

// ── Avatar load events ────────────────────────────────────────────────────────
function setupAvatarLoadingEvents() {
  window.addEventListener('avatar-loading-progress', (e) => {
    showLoader();
    document.getElementById('progress-bar-fill').style.width = `${e.detail}%`;
    document.getElementById('progress-text').innerText =
      `Loading 3D Model: ${e.detail.toFixed(1)}%`;
  });
  window.addEventListener('avatar-loaded', () => {
    hideLoader();
    updateStatusBadge('idle');
  });
}

// ── Render loop ───────────────────────────────────────────────────────────────
function renderLoop() {
  requestAnimationFrame(renderLoop);

  const now = performance.now();
  const dt  = (now - lastFrameTime) / 1000.0;
  lastFrameTime = now;

  frameCount++;
  fpsTimer += dt;
  if (fpsTimer >= 0.5) {
    document.getElementById('hud-fps').innerText = Math.round(frameCount / fpsTimer);
    frameCount = 0;
    fpsTimer   = 0;
  }

  behavior.update(dt);

  // Calculate elapsed time from the precise AudioContext clock if playing
  let elapsed = null;
  if (isAudioPlaying && audioContext) {
    elapsed = audioContext.currentTime - audioStartTime;
  }

  if (avatar) avatar.render(dt, behavior, elapsed);
}

// ── HUD & status ──────────────────────────────────────────────────────────────
function updateStatusBadge(status) {
  const badge = document.getElementById('hud-status');
  if (badge) { badge.className = `status-badge ${status}`; badge.innerText = status; }

  const busy = ['generating', 'speaking', 'thinking', 'transcribing'].includes(status);
  document.getElementById('btn-speak')?.toggleAttribute('disabled', busy);
  document.getElementById('btn-stop')?.toggleAttribute('disabled', !busy);

  if (['idle', 'complete', 'stopped'].includes(status)) {
    hideLoader();
    updateProgressUI('', false);
  }
}

function updateProgressUI(message, showSpinner) {
  const bar  = document.getElementById('progress-bar');
  const text = document.getElementById('progress-label');
  if (!bar || !text) return;

  if (!message) {
    bar.style.display = 'none';
    return;
  }

  bar.style.display = 'flex';
  text.innerText = message;

  const fill = document.getElementById('progress-bar-fill');
  if (fill) {
    if (showSpinner) {
      fill.classList.add('indeterminate');
    } else {
      fill.classList.remove('indeterminate');
      fill.style.width = '100%';
    }
  }
}

function updateMicBadge(status) {
  const badge = document.getElementById('hud-mic');
  if (badge) { badge.className = `status-badge ${status}`; badge.innerText = `mic: ${status}`; }
}

function showLoader() { document.getElementById('model-loader')?.classList.remove('hidden'); }
function hideLoader()  { document.getElementById('model-loader')?.classList.add('hidden'); }

// ── Console HUD ───────────────────────────────────────────────────────────────
function injectConsoleHUD() {
  const hud = document.createElement('div');
  Object.assign(hud.style, {
    position: 'fixed', bottom: '10px', left: '10px',
    width: '350px', height: '150px',
    backgroundColor: 'rgba(10,10,15,0.85)',
    color: '#00e676', fontFamily: 'monospace', fontSize: '11px',
    padding: '8px', overflowY: 'auto', zIndex: '10000',
    border: '1px solid #00e676', borderRadius: '6px',
    pointerEvents: 'none',
  });
  document.body.appendChild(hud);

  ['log', 'warn', 'error'].forEach((lvl) => {
    const orig = console[lvl];
    console[lvl] = (...args) => {
      orig(...args);
      const line = document.createElement('div');
      line.style.marginBottom = '4px';
      line.style.color = lvl === 'error' ? '#ff1744' : lvl === 'warn' ? '#ffea00' : '#00e676';
      line.innerText = `[${lvl.toUpperCase()}] ${args.map(a =>
        typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}`;
      hud.appendChild(line);
      hud.scrollTop = hud.scrollHeight;
    };
  });
}

// ── Calibration sliders ───────────────────────────────────────────────────────
function setupCalibrationSliders() {
  const defaults = {
    laX: -1.82, laY: -2.42, laZ:  3.14,
    raX: -1.82, raY:  2.62, raZ: -3.14,
    lfX:  1.10, lfY:  0.00, lfZ: -0.20,
    rfX:  1.12, rfY:  0.00, rfZ:  0.14,
    lhX: -0.10, lhY:  1.66, lhZ:  0.26,
    rhX: -0.18, rhY: -1.66, rhZ: -0.26,
  };
  window.avatarCalibration = { ...defaults };

  const sliders = [
    'la-x','la-y','la-z','ra-x','ra-y','ra-z',
    'lf-x','lf-y','lf-z','rf-x','rf-y','rf-z',
    'lh-x','lh-y','lh-z','rh-x','rh-y','rh-z',
  ];

  const updateCode = () => {
    const c = window.avatarCalibration;
    const out = document.getElementById('cal-code-output');
    if (!out) return;
    out.innerText = `const cal = {\n` +
      Object.entries(c).map(([k,v]) => `  ${k}: ${v.toFixed(2)}`).join(',\n') + '\n};';
  };

  sliders.forEach((s) => {
    const camelKey = s.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
                       .replace(/^([a-z]{2})([a-z])$/, (_, b, ax) => b + ax.toUpperCase());
    const el    = document.getElementById(`cal-${s}`);
    const valEl = document.getElementById(`cal-${s}-val`);
    if (!el) return;
    el.addEventListener('input', () => {
      const v = parseFloat(el.value);
      const matchKey = Object.keys(defaults).find(k =>
        k.toLowerCase() === camelKey.toLowerCase()
      );
      if (matchKey) {
        window.avatarCalibration[matchKey] = v;
        if (valEl) valEl.innerText = v.toFixed(2);
        updateCode();
      }
    });
  });

  document.getElementById('btn-reset-calibration')?.addEventListener('click', () => {
    window.avatarCalibration = { ...defaults };
    sliders.forEach((s) => {
      const el    = document.getElementById(`cal-${s}`);
      const valEl = document.getElementById(`cal-${s}-val`);
      const matchKey = Object.keys(defaults).find(k =>
        k.toLowerCase() === s.replace('-','').toLowerCase()
      );
      if (el && matchKey) {
        el.value = defaults[matchKey];
        if (valEl) valEl.innerText = defaults[matchKey].toFixed(2);
      }
    });
    updateCode();
  });

  updateCode();
}
