import { Avatar3D }      from './src/avatar3d.js';
import { BehaviorManager } from './src/behavior.js';
import { S2SManager }      from './src/stt.js';

// ── Server config ──────────────────────────────────────────────────────────────
const WS_BASE = import.meta.env.VITE_SERVER_URL
  || window.location.origin.replace(/^http/, 'ws');

const HTTP_BASE = import.meta.env.VITE_HTTP_URL
  || window.location.origin;

// ── Globals ───────────────────────────────────────────────────────────────────
let avatar   = null;
let behavior = null;
let s2s      = null;

// Audio playback
let audioContext       = null;
let nextPlayTime       = 0;        // AudioContext scheduled play cursor
let isAudioPlaying     = false;
let audioStartTime     = 0;        // time of first chunk start (for animation sync)

// Pending audio chunks while waiting for blendshape matrix from server
const MATRIX_TIMEOUT_MS = 500;     // max ms to wait for matrix before forcing playback
let _pendingChunks     = [];       // buffered until blendshape_matrix arrives or timeout fires
let _pendingTimer      = null;     // setTimeout handle for the flush timeout

let lastFrameTime = performance.now();
let frameCount = 0;
let fpsTimer   = 0;

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  injectConsoleHUD();

  avatar   = new Avatar3D('canvas-container', '/avatar_head.glb');
  behavior = new BehaviorManager();

  s2s = new S2SManager(`${WS_BASE}/ws/s2s`);

  // Real-time transcript display
  s2s.onTranscript = (text, lang) => {
    console.log(`[S2S] Transcript (${lang}):`, text);
    const ta = document.getElementById('text-input');
    if (ta) ta.value = `🎤 You: ${text}`;
    updateProgressUI('💬 Heard — generating response…', true);
  };

  // New sentence starting
  s2s.onTTSStart = (seq, text) => {
    console.log(`[S2S] TTS start seq=${seq}:`, text);
    updateProgressUI('🔊 Receiving audio…', true);
  };

  // PCM chunk arrived — buffer until blendshape matrix or timeout
  s2s.onAudioChunk = (seq, pcmBytes, sampleRate) => {
    ensureAudioContext();

    _pendingChunks.push({ pcmBytes, sampleRate });

    // Start flush timeout on the first chunk of this sentence
    if (!_pendingTimer) {
      _pendingTimer = setTimeout(() => {
        _flushPendingAudio('timeout');
      }, MATRIX_TIMEOUT_MS);
    }
  };

  // Server sends blendshape matrix after TTS streaming completes — flush & play
  s2s.onBlendshapeMatrix = (seq, matrix) => {
    console.log(`[S2S] Blendshape matrix seq=${seq}, frames=${matrix?.length}`);
    if (matrix && matrix.length > 0) {
      avatar.setAnimationMatrix(matrix);
    }
    _flushPendingAudio('matrix');
  };

  // TTS fully done for this sentence — force-flush pending, then schedule idle
  s2s.onTTSEnd = (seq) => {
    console.log(`[S2S] TTS end seq=${seq}`);
    _flushPendingAudio('tts_end');
    _scheduleIdle();
  };

  s2s.onStatusChange = (status) => {
    updateMicBadge(status);
    const labels = {
      listening:  '🎤 Listening…',
      processing: '⏳ Processing…',
      speaking:   '🔊 Speaking…',
      idle:       '',
    };
    if (labels[status] !== undefined) updateProgressUI(labels[status], !!labels[status]);
    if (status === 'idle') {
      const btn = document.getElementById('btn-mic');
      if (btn) {
        btn.classList.remove('active', 'processing');
        btn.disabled = false;
        btn.innerHTML = '<span class="btn-icon">🎤</span> Start Listening';
      }
    }
  };

  s2s.onError = (msg) => {
    console.error('[S2S]', msg);
    updateStatusBadge('error');
    updateProgressUI(`❌ ${msg}`, false);
  };

  setupEventListeners();
  setupAvatarLoadingEvents();
  setupCalibrationSliders();
  requestAnimationFrame(renderLoop);
});

// ── Audio context helper ──────────────────────────────────────────────────────
function ensureAudioContext() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === 'suspended') audioContext.resume();
  return audioContext;
}

function _stopAudio() {
  isAudioPlaying = false;
  nextPlayTime   = 0;
  audioStartTime = 0;
  _pendingChunks = [];
  if (_pendingTimer) { clearTimeout(_pendingTimer); _pendingTimer = null; }
  avatar?.clearAnimation();
}

function _flushPendingAudio(reason) {
  if (_pendingTimer) { clearTimeout(_pendingTimer); _pendingTimer = null; }
  if (_pendingChunks.length === 0) return;

  console.log(`[S2S] Flushing ${_pendingChunks.length} chunks (reason=${reason})`);

  // Mark start time before the first chunk so animation elapsed is correct
  if (!isAudioPlaying) {
    audioStartTime = nextPlayTime < audioContext.currentTime
      ? audioContext.currentTime
      : nextPlayTime;
    isAudioPlaying = true;
    updateStatusBadge('speaking');
  }

  for (const chunk of _pendingChunks) {
    _playAudioChunk(chunk.pcmBytes, chunk.sampleRate);
  }
  _pendingChunks = [];
}

function _playAudioChunk(pcmBytes, sampleRate) {
  const float32 = new Float32Array(pcmBytes);
  const buf = audioContext.createBuffer(1, float32.length, sampleRate);
  buf.getChannelData(0).set(float32);

  const source = audioContext.createBufferSource();
  source.buffer = buf;
  source.connect(audioContext.destination);

  if (nextPlayTime < audioContext.currentTime) {
    nextPlayTime = audioContext.currentTime;
  }

  source.start(nextPlayTime);
  nextPlayTime += buf.duration;
}

function _scheduleIdle() {
  const remaining = Math.max(0, nextPlayTime - (audioContext?.currentTime ?? 0));
  setTimeout(() => {
    isAudioPlaying = false;
    updateStatusBadge('idle');
    updateProgressUI('', false);
  }, remaining * 1000 + 200);
}

// ── Apply emotion ─────────────────────────────────────────────────────────────
function _applyEmotion(emotion) {
  if (!emotion) return;
  const valid = ['neutral', 'happy', 'sad', 'angry', 'surprised', 'fearful'];
  const e = valid.includes(emotion) ? emotion : 'happy';
  if (behavior) behavior.currentEmotion = e;
  if (avatar)   avatar.setEmotion(e);
  const sel = document.getElementById('emotion-select');
  if (sel) sel.value = e;
  const hud = document.getElementById('hud-emotion');
  if (hud) hud.innerText = e;
  console.log(`[Emotion] Applied: ${e}`);
}

// ── Debug: typed text → /chat ─────────────────────────────────────────────────
async function _sendChatText(text) {
  if (!text.trim()) return;
  updateStatusBadge('generating');
  updateProgressUI('💬 Sending to /chat…', true);
  try {
    const res = await fetch(`${HTTP_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const ta = document.getElementById('text-input');
    if (ta) ta.value = JSON.stringify(data, null, 2);
    updateStatusBadge('idle');
    updateProgressUI('', false);
  } catch (err) {
    console.error('[Chat]', err);
    updateStatusBadge('error');
    updateProgressUI(`❌ ${err.message}`, false);
  }
}

// ── Event listeners ───────────────────────────────────────────────────────────
function setupEventListeners() {

  document.getElementById('btn-speak')?.addEventListener('click', async () => {
    ensureAudioContext();
    const text = document.getElementById('text-input')?.value?.trim();
    if (!text) return;
    await _sendChatText(text);
  });

  document.getElementById('btn-stop')?.addEventListener('click', () => {
    if (s2s.isListening) s2s.cancel();
    _stopAudio();
    updateStatusBadge('idle');
    updateProgressUI('', false);
  });

  const btnMic = document.getElementById('btn-mic');
  if (btnMic) {
    btnMic.addEventListener('click', async () => {
      ensureAudioContext();
      if (s2s.isListening) {
        s2s.stop();
        btnMic.classList.remove('active');
        btnMic.innerHTML = '<span class="btn-icon">⏳</span> Processing…';
        btnMic.disabled  = true;
      } else {
        try {
          const lang = document.getElementById('lang-select')?.value || 'hi-IN';
          await s2s.start(lang);
          btnMic.classList.add('active');
          btnMic.innerHTML = '<span class="btn-icon">🔴</span> Stop';
        } catch (err) {
          console.error('[Mic]', err.message);
          alert(err.message);
        }
      }
    });
  }

  document.getElementById('emotion-select')?.addEventListener('change', (e) => {
    _applyEmotion(e.target.value);
  });

  document.getElementById('image-upload')?.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (!file) return;
    if (!/\.(glb|gltf)$/i.test(file.name)) { alert('Please select a .glb or .gltf file.'); return; }
    avatar.loadGLBModel(URL.createObjectURL(file));
  });

  document.getElementById('btn-reset-avatar')?.addEventListener('click', () => {
    avatar.loadGLBModel('/avatar_head.glb');
  });
}

// ── Avatar load events ────────────────────────────────────────────────────────
function setupAvatarLoadingEvents() {
  window.addEventListener('avatar-loading-progress', (e) => {
    showLoader();
    document.getElementById('progress-bar-fill').style.width = `${e.detail}%`;
    document.getElementById('progress-text').innerText = `Loading 3D Model: ${e.detail.toFixed(1)}%`;
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

  // Pass AudioContext elapsed time so avatar3d._updateFromMatrix stays in sync
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
  if (['idle', 'complete', 'stopped'].includes(status)) { hideLoader(); updateProgressUI('', false); }
}

function updateProgressUI(message, showSpinner) {
  const bar  = document.getElementById('progress-bar');
  const text = document.getElementById('progress-label');
  if (!bar || !text) return;
  if (!message) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';
  text.innerText = message;
  const fill = document.getElementById('progress-bar-fill');
  if (fill) {
    if (showSpinner) fill.classList.add('indeterminate');
    else { fill.classList.remove('indeterminate'); fill.style.width = '100%'; }
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
    position: 'fixed', bottom: '10px', left: '10px', width: '350px', height: '150px',
    backgroundColor: 'rgba(10,10,15,0.85)', color: '#00e676', fontFamily: 'monospace',
    fontSize: '11px', padding: '8px', overflowY: 'auto', zIndex: '10000',
    border: '1px solid #00e676', borderRadius: '6px', pointerEvents: 'none',
  });
  document.body.appendChild(hud);
  ['log', 'warn', 'error'].forEach((lvl) => {
    const orig = console[lvl];
    console[lvl] = (...args) => {
      orig(...args);
      const line = document.createElement('div');
      line.style.marginBottom = '4px';
      line.style.color = lvl === 'error' ? '#ff1744' : lvl === 'warn' ? '#ffea00' : '#00e676';
      line.innerText = `[${lvl.toUpperCase()}] ${args.map(a => typeof a === 'object' ? JSON.stringify(a) : a).join(' ')}`;
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
      const matchKey = Object.keys(defaults).find(k => k.toLowerCase() === camelKey.toLowerCase());
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
      const matchKey = Object.keys(defaults).find(k => k.toLowerCase() === s.replace('-','').toLowerCase());
      if (el && matchKey) {
        el.value = defaults[matchKey];
        if (valEl) valEl.innerText = defaults[matchKey].toFixed(2);
      }
    });
    updateCode();
  });

  updateCode();
}
