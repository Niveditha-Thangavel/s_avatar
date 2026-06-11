import { Avatar3D } from './src/avatar3d.js';
import { BehaviorManager } from './src/behavior.js';
import { LipSyncManager } from './src/lipsync.js';
import { TTSClient } from './src/tts_client.js';
import { STTManager } from './src/stt.js';

// ── Server config ─────────────────────────────────────────────────────────────
const getWsBase = () => {
  if (import.meta.env.VITE_SERVER_URL) {
    return import.meta.env.VITE_SERVER_URL;
  }
  const host = import.meta.env.VITE_SERVER_HOST || (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1' ? window.location.hostname : 'localhost');
  const port = import.meta.env.VITE_SERVER_PORT || '8765';
  return `ws://${host}:${port}`;
};

const getHttpBase = () => {
  if (import.meta.env.VITE_HTTP_URL) {
    return import.meta.env.VITE_HTTP_URL;
  }
  const host = import.meta.env.VITE_SERVER_HOST || (typeof window !== 'undefined' && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1' ? window.location.hostname : 'localhost');
  const port = import.meta.env.VITE_SERVER_PORT || '8765';
  return `http://${host}:${port}`;
};

const WS_BASE   = getWsBase();
const HTTP_BASE = getHttpBase();

// ── Globals ───────────────────────────────────────────────────────────────────
let avatar   = null;
let behavior = null;
let lipsync  = null;
let tts      = null;
let stt      = null;

let lastFrameTime = performance.now();
let frameCount = 0;
let fpsTimer   = 0;

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  injectConsoleHUD();

  // 3D scene + animation
  avatar   = new Avatar3D('canvas-container', '/avatar_head.glb');
  behavior = new BehaviorManager();
  lipsync  = new LipSyncManager();

  // ── TTS client ────────────────────────────────────────────────────────────
  tts = new TTSClient(`${WS_BASE}/ws/tts`);

  tts.onChunk = ({ audio, sampleRate, text }) => {
    // Pass the sentence text as the "phoneme string" — lipsync will use
    // the raw text characters to approximate mouth shapes. This gives
    // visible lip movement even without real IPA phonemes from the server.
    lipsync.queueAudioChunk(audio, sampleRate, text);
    updateStatusBadge('speaking');
  };

  tts.onStatusChange = (status) => {
    console.log('[TTS status]', status);
    updateStatusBadge(status);
    if (status === 'generating') {
      updateProgressUI('🔊 Generating speech…', true);
    }
    if (status === 'complete') {
      updateProgressUI('▶️ Playing…', false);
      // Force-flush if fewer than bufferThreshold chunks arrived
      lipsync.flushBuffer();
    }
  };

  tts.onError = (msg) => {
    console.error('[TTS]', msg);
    updateStatusBadge('error');
  };

  // ── STT manager ───────────────────────────────────────────────────────────
  stt = new STTManager(`${WS_BASE}/ws/stt`);

  stt.onTranscript = (text) => {
    console.log('[STT] Heard:', text);
    const ta = document.getElementById('text-input');
    if (ta) ta.value = `🎤 You: ${text}`;
    updateProgressUI('💬 Got transcript — generating reply…', true);
  };

  stt.onReply = (text) => {
    console.log('[STT] Reply:', text);
    const ta = document.getElementById('text-input');
    if (ta) ta.value = `🤖 Avatar: ${text}`;
    updateProgressUI('🔊 Generating speech…', true);
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

// ── Core speak function ───────────────────────────────────────────────────────
/**
 * Synthesise text via the server TTS and play it through the lip-sync engine.
 * All audio scheduling and viseme morph-target updates happen inside
 * lipsync.queueAudioChunk() → lipsync.update() → avatar.render().
 */
async function _speakText(text) {
  if (!text?.trim()) return;

  // Stop any currently playing audio and reset the lipsync state.
  // init() is idempotent — safe to call even if already initialised.
  lipsync.stop();
  lipsync.init();
  lipsync.resume();

  const instruct = document.getElementById('instruct-input')?.value?.trim() || null;
  const speed    = parseFloat(document.getElementById('speed-range')?.value   || '1.0');
  const numStep  = parseInt(document.getElementById('quality-select')?.value  || '16', 10);

  updateStatusBadge('generating');
  updateProgressUI('🔊 Generating speech…', true);

  try {
    await tts.speak({ text, instruct, speed, numStep });
  } catch (err) {
    console.error('[Speak]', err);
    updateStatusBadge('error');
    updateProgressUI('❌ TTS error', false);
  }
}

// ── Event listeners ───────────────────────────────────────────────────────────
function setupEventListeners() {

  // Speak button — typed text goes through server chat layer first
  document.getElementById('btn-speak')?.addEventListener('click', async () => {
    const text = document.getElementById('text-input')?.value?.trim();
    if (!text) return;

    updateStatusBadge('thinking');
    updateProgressUI('🤔 Generating reply…', true);
    try {
      const res   = await fetch(`${HTTP_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      const { reply } = await res.json();
      if (reply) {
        document.getElementById('text-input').value = `🤖 Avatar: ${reply}`;
        _speakText(reply);
      }
    } catch (err) {
      console.error('[Chat]', err);
      updateStatusBadge('error');
      updateProgressUI('❌ Server error', false);
    }
  });

  // Stop button
  document.getElementById('btn-stop')?.addEventListener('click', () => {
    tts.stop();
    lipsync.stop();
    updateStatusBadge('idle');
  });

  // Mic toggle
  const btnMic = document.getElementById('btn-mic');
  if (btnMic) {
    btnMic.addEventListener('click', async () => {
      if (stt.isListening) {
        // User pressed Stop → send audio to Whisper, wait for reply
        btnMic.classList.remove('active');
        btnMic.innerHTML = '<span class="btn-icon">⏳</span> Processing…';
        btnMic.disabled  = true;
        stt.stop();   // sends { type:"stop" }, keeps socket open for reply
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

  // Volume from the currently playing TTS audio drives head-bobbing
  const volume = lipsync.getVolume();
  behavior.update(dt, volume);
  lipsync.update(dt);
  updateHUDPhoneme();

  if (avatar) avatar.render(dt, behavior, lipsync);
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

// ── Progress UI ───────────────────────────────────────────────────────────────
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

function updateHUDPhoneme() {
  const el = document.getElementById('hud-phoneme');
  if (!el) return;
  if (!lipsync?.isPlaying || !lipsync?.audioCtx) { el.innerText = '-'; return; }
  const t      = lipsync.audioCtx.currentTime;
  const active = lipsync.phonemeTimeline.find(ev => t >= ev.startTime && t <= ev.endTime);
  el.innerText = active ? '●' : '-';
}

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
    const key = s.replace('-','').replace(/([a-z])([XYZ])/, (_,a,b) => a + b.toLowerCase())
                  .replace(/^([lr][afh])([xyz])$/, (_, bone, ax) =>
                    bone[0] + bone[1].toUpperCase() + ax.toUpperCase());
    // Map "la-x" → "laX" style key
    const camelKey = s.replace(/-([a-z])/g, (_, c) => c.toUpperCase())
                       .replace(/^([a-z]{2})([a-z])$/, (_, b, ax) => b + ax.toUpperCase());
    const el    = document.getElementById(`cal-${s}`);
    const valEl = document.getElementById(`cal-${s}-val`);
    if (!el) return;
    el.addEventListener('input', () => {
      const v = parseFloat(el.value);
      // find matching key in defaults
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
