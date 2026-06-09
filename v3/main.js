import { Avatar3D } from './src/avatar3d.js';
import { BehaviorManager } from './src/behavior.js';
import { LipSyncManager } from './src/lipsync.js';

// Global variables
let avatar = null;
let behavior = null;
let lipsync = null;
let ttsWorker = null;

// File download progress tracker for TTS
const fileProgressMap = new Map();

// Timing variables for FPS counter
let lastFrameTime = performance.now();
let frameCount = 0;
let fpsTimer = 0;

// Start the application
window.addEventListener('DOMContentLoaded', async () => {
  // Inject Console HUD
  const hud = document.createElement('div');
  hud.id = 'console-hud';
  hud.style.position = 'fixed';
  hud.style.bottom = '10px';
  hud.style.left = '10px';
  hud.style.width = '350px';
  hud.style.height = '150px';
  hud.style.backgroundColor = 'rgba(10, 10, 15, 0.85)';
  hud.style.color = '#00e676';
  hud.style.fontFamily = 'monospace';
  hud.style.fontSize = '11px';
  hud.style.padding = '8px';
  hud.style.overflowY = 'auto';
  hud.style.zIndex = '10000';
  hud.style.border = '1px solid #00e676';
  hud.style.borderRadius = '6px';
  hud.style.boxShadow = '0 4px 20px rgba(0,0,0,0.5)';
  hud.style.pointerEvents = 'none'; // click-through
  document.body.appendChild(hud);

  const logToHUD = (type, args) => {
    const msg = args.map(arg => typeof arg === 'object' ? JSON.stringify(arg) : arg).join(' ');
    const line = document.createElement('div');
    line.style.marginBottom = '4px';
    line.innerText = `[${type.toUpperCase()}] ${msg}`;
    if (type === 'error') line.style.color = '#ff1744';
    if (type === 'warn') line.style.color = '#ffea00';
    hud.appendChild(line);
    hud.scrollTop = hud.scrollHeight;
  };

  const origLog = console.log;
  const origWarn = console.warn;
  const origError = console.error;

  console.log = (...args) => { origLog(...args); logToHUD('log', args); };
  console.warn = (...args) => { origWarn(...args); logToHUD('warn', args); };
  console.error = (...args) => { origError(...args); logToHUD('error', args); };

  // 1. Register Service Worker for model caching
  registerServiceWorker();

  // 2. Instantiate Managers
  avatar = new Avatar3D('canvas-container', '/avatar_head.glb');
  behavior = new BehaviorManager();
  lipsync = new LipSyncManager();

  // 3. Initialize TTS Web Worker
  initTTSWorker();

  // 4. Bind UI Event Listeners
  setupEventListeners();

  // 5. Hook up custom events from Avatar3D for model loading states
  setupAvatarLoadingEvents();

  // 5b. Setup Posture Calibration sliders
  setupCalibrationSliders();

  // 6. Trigger first load of the TTS model (q4 is twice as fast and uses half the RAM)
  loadModel('q4', 'wasm');

  // 7. Start Render Loop
  requestAnimationFrame(renderLoop);
});

/**
 * Register the Service Worker to intercept and cache Hugging Face model requests
 */
async function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    try {
      const registrations = await navigator.serviceWorker.getRegistrations();
      for (const registration of registrations) {
        await registration.unregister();
        console.log('[App] Service Worker unregistered successfully');
      }
    } catch (error) {
      console.warn('[App] Service Worker unregistration failed:', error);
    }
  }
  if ('caches' in window) {
    try {
      await caches.delete('avatar-app-cache-v1');
      console.log('[App] Local app cache cleared');
    } catch (error) {
      console.warn('[App] Cache deletion failed:', error);
    }
  }
}

/**
 * Initialize Web Worker for Background TTS
 */
function initTTSWorker() {
  ttsWorker = new Worker(new URL('./src/tts.worker.js', import.meta.url), { type: 'module' });

  // Handle messages from the worker thread
  ttsWorker.onmessage = (event) => {
    const { type, data } = event.data;

    switch (type) {
      case 'status':
        updateStatusBadge(data);
        break;

      case 'progress':
        handleDownloadProgress(data);
        break;

      case 'error':
        hideLoader();
        updateStatusBadge('error');
        alert(data);
        break;

      case 'chunk':
        const samples = new Float32Array(data.audio);
        lipsync.queueAudioChunk(samples, data.samplingRate, data.phonemes);
        updateStatusBadge('speaking');
        break;
    }
  };
}

/**
 * Send load model request to worker
 */
function loadModel(dtype, device) {
  fileProgressMap.clear();
  document.getElementById('file-progress-list').innerHTML = '';
  document.getElementById('progress-bar-fill').style.width = '0%';
  document.getElementById('progress-text').innerText = 'Initializing ONNX Runtime...';
  showLoader();

  ttsWorker.postMessage({
    type: 'load',
    data: {
      modelId: 'onnx-community/Kokoro-82M-v1.0-ONNX',
      dtype: dtype,
      device: device
    }
  });
}

/**
 * Handle custom loading events triggered by the Avatar3D loader
 */
function setupAvatarLoadingEvents() {
  window.addEventListener('avatar-loading-progress', (event) => {
    showLoader();
    document.getElementById('progress-bar-fill').style.width = `${event.detail}%`;
    document.getElementById('progress-text').innerText = `Downloading 3D Head Model: ${event.detail.toFixed(1)}%`;
  });

  window.addEventListener('avatar-loaded', () => {
    hideLoader();
    updateStatusBadge('ready');
  });
}

/**
 * Event Listeners Binding
 */
function setupEventListeners() {
  // TTS triggers
  const btnSpeak = document.getElementById('btn-speak');
  const btnStop = document.getElementById('btn-stop');
  const textarea = document.getElementById('text-input');
  
  btnSpeak.addEventListener('click', () => {
    const text = textarea.value.trim();
    if (!text) return;

    // Resuming AudioContext requires a user-gesture triggers
    lipsync.init();
    lipsync.resume();

    // Reset current lip-sync play queues
    lipsync.stop();

    const voice = document.getElementById('voice-select').value;
    const speed = parseFloat(document.getElementById('speed-range').value);

    // Send generation command to worker
    ttsWorker.postMessage({
      type: 'generate',
      data: { text, voice, speed }
    });

    btnSpeak.disabled = true;
    btnStop.disabled = false;
  });

  btnStop.addEventListener('click', () => {
    lipsync.stop();
    btnSpeak.disabled = false;
    btnStop.disabled = true;
    updateStatusBadge('ready');
  });

  // Settings Sliders
  const speedRange = document.getElementById('speed-range');
  const speedVal = document.getElementById('speed-val');
  speedRange.addEventListener('input', () => {
    speedVal.innerText = speedRange.value;
  });

  const emotionSelect = document.getElementById('emotion-select');
  if (emotionSelect) {
    emotionSelect.addEventListener('change', () => {
      if (behavior) {
        behavior.currentEmotion = emotionSelect.value;
      }
    });
  }

  const volumeRange = document.getElementById('volume-range');
  const volumeVal = document.getElementById('volume-val');
  volumeRange.addEventListener('input', () => {
    volumeVal.innerText = volumeRange.value;
  });

  // Upload Custom 3D Model (GLB/GLTF)
  const imageUpload = document.getElementById('image-upload');
  imageUpload.addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (file) {
      if (!file.name.toLowerCase().endsWith('.glb') && !file.name.toLowerCase().endsWith('.gltf')) {
        alert('Please select a valid 3D model file in .glb or .gltf format.');
        return;
      }
      const url = URL.createObjectURL(file);
      avatar.loadGLBModel(url);
    }
  });

  // Reset Default Avatar Model
  document.getElementById('btn-reset-avatar').addEventListener('click', () => {
    avatar.loadGLBModel('/avatar_head.glb');
  });

  // Model Reload Apply settings
  document.getElementById('btn-apply-settings').addEventListener('click', () => {
    const dtype = document.getElementById('quant-select').value;
    const device = document.getElementById('device-select').value;
    loadModel(dtype, device);
  });
}

/**
 * Handle individual model asset download streams
 */
function handleDownloadProgress(progressData) {
  fileProgressMap.set(progressData.file, progressData);

  const progressList = document.getElementById('file-progress-list');
  progressList.innerHTML = '';

  let totalPercentSum = 0;
  let activeDownloads = 0;

  fileProgressMap.forEach((info) => {
    activeDownloads++;
    totalPercentSum += info.progress;

    const fileItem = document.createElement('div');
    fileItem.className = 'file-progress-item';
    
    const shortName = info.file.split('/').pop();
    
    fileItem.innerHTML = `
      <span class="file-name">${shortName}</span>
      <span class="file-percent">${info.progress.toFixed(1)}%</span>
    `;
    progressList.appendChild(fileItem);
  });

  const averageProgress = activeDownloads > 0 ? (totalPercentSum / activeDownloads) : 0;
  
  document.getElementById('progress-bar-fill').style.width = `${averageProgress}%`;
  document.getElementById('progress-text').innerText = `Downloading Model Assets: ${averageProgress.toFixed(1)}%`;
}

/**
 * Update HUD and badge elements
 */
function updateStatusBadge(status) {
  const badge = document.getElementById('hud-status');
  badge.className = `status-badge ${status}`;
  badge.innerText = status;

  const btnSpeak = document.getElementById('btn-speak');
  const btnStop = document.getElementById('btn-stop');

  if (status === 'ready' || status === 'complete') {
    btnSpeak.disabled = false;
    btnStop.disabled = true;
    hideLoader();
    if (status === 'complete' && lipsync) {
      lipsync.flushBuffer();
    }
  } else if (status === 'loading') {
    btnSpeak.disabled = true;
    btnStop.disabled = true;
  } else if (status === 'generating') {
    btnSpeak.disabled = true;
    btnStop.disabled = false;
  } else if (status === 'speaking') {
    btnSpeak.disabled = true;
    btnStop.disabled = false;
  }
}

function showLoader() {
  document.getElementById('model-loader').classList.remove('hidden');
}

function hideLoader() {
  document.getElementById('model-loader').classList.add('hidden');
}

/**
 * Animation Render Loop
 */
function renderLoop() {
  requestAnimationFrame(renderLoop);

  const now = performance.now();
  const dt = (now - lastFrameTime) / 1000.0;
  lastFrameTime = now;

  frameCount++;
  fpsTimer += dt;
  if (fpsTimer >= 0.5) {
    const fps = Math.round(frameCount / fpsTimer);
    document.getElementById('hud-fps').innerText = fps;
    frameCount = 0;
    fpsTimer = 0;
  }

  const volume = lipsync.getVolume();
  
  behavior.update(dt, volume);
  lipsync.update(dt);

  updateHUDPhoneme();

  if (avatar) {
    avatar.render(dt, behavior, lipsync);
  }
}

/**
 * Update the active phoneme HUD display
 */
function updateHUDPhoneme() {
  if (!lipsync || !lipsync.isPlaying || !lipsync.audioCtx) {
    document.getElementById('hud-phoneme').innerText = '-';
    return;
  }

  const playTime = lipsync.audioCtx.currentTime;
  const activeEvent = lipsync.phonemeTimeline.find(
    event => playTime >= event.startTime && playTime <= event.endTime
  );

  if (activeEvent) {
    let activeChar = '-';
    lipsync.phonemeTimeline.forEach((evt) => {
      if (playTime >= evt.startTime && playTime <= evt.endTime) {
        activeChar = Object.keys(lipsync.phonemeWeights).find(
          char => JSON.stringify(lipsync.mapPhonemeToVisemes(char)) === JSON.stringify(evt.targetVisemes)
        ) || '-';
      }
    });

    document.getElementById('hud-phoneme').innerText = activeChar.toUpperCase();
  } else {
    document.getElementById('hud-phoneme').innerText = '-';
  }
}

/**
 * Setup Posture Calibration Sliders
 */
function setupCalibrationSliders() {
  // Initial default values - setting user's new calibrated shoulder rotations
  const defaults = {
    laX: -1.82, laY: -2.42, laZ: 3.14,
    raX: -1.82, raY: 2.62, raZ: -3.14,
    lfX: 1.10, lfY: 0.00, lfZ: -0.20,
    rfX: 1.12, rfY: 0.00, rfZ: 0.14,
    lhX: -0.10, lhY: 1.66, lhZ: 0.26,
    rhX: -0.18, rhY: -1.66, rhZ: -0.26
  };

  // Set global object
  window.avatarCalibration = { ...defaults };

  const sliders = [
    { id: 'cal-la-x', key: 'laX' },
    { id: 'cal-la-y', key: 'laY' },
    { id: 'cal-la-z', key: 'laZ' },
    { id: 'cal-ra-x', key: 'raX' },
    { id: 'cal-ra-y', key: 'raY' },
    { id: 'cal-ra-z', key: 'raZ' },
    { id: 'cal-lf-x', key: 'lfX' },
    { id: 'cal-lf-y', key: 'lfY' },
    { id: 'cal-lf-z', key: 'lfZ' },
    { id: 'cal-rf-x', key: 'rfX' },
    { id: 'cal-rf-y', key: 'rfY' },
    { id: 'cal-rf-z', key: 'rfZ' },
    { id: 'cal-lh-x', key: 'lhX' },
    { id: 'cal-lh-y', key: 'lhY' },
    { id: 'cal-lh-z', key: 'lhZ' },
    { id: 'cal-rh-x', key: 'rhX' },
    { id: 'cal-rh-y', key: 'rhY' },
    { id: 'cal-rh-z', key: 'rhZ' }
  ];

  function updateCodeOutput() {
    const c = window.avatarCalibration;
    const snippet = `// Copy and paste this into src/avatar3d.js inside updateArmSways():

// Left Arm (upper)
this.leftArm.rotation.x = this.initialLeftArmRot.x + (${c.laX.toFixed(2)}) + swayX + gestureLeftX;
this.leftArm.rotation.y = this.initialLeftArmRot.y + (${c.laY.toFixed(2)}) + gestureLeftY;
this.leftArm.rotation.z = this.initialLeftArmRot.z + (${c.laZ.toFixed(2)}) + swayZ + gestureLeftZ;

// Right Arm (upper)
this.rightArm.rotation.x = this.initialRightArmRot.x + (${c.raX.toFixed(2)}) + swayX + gestureRightX;
this.rightArm.rotation.y = this.initialRightArmRot.y + (${c.raY.toFixed(2)}) + gestureRightY;
this.rightArm.rotation.z = this.initialRightArmRot.z + (${c.raZ.toFixed(2)}) + swayZ + gestureRightZ;

// Left Forearm
this.leftForeArm.rotation.x = this.initialLeftForeArmRot.x + (${c.lfX.toFixed(2)}) + gestureLeftX * 0.8;
this.leftForeArm.rotation.y = this.initialLeftForeArmRot.y + (${c.lfY.toFixed(2)}) + gestureLeftY * 0.8;
this.leftForeArm.rotation.z = this.initialLeftForeArmRot.z + (${c.lfZ.toFixed(2)}) + swayZ + gestureLeftZ * 0.8;

// Right Forearm
this.rightForeArm.rotation.x = this.initialRightForeArmRot.x + (${c.rfX.toFixed(2)}) + gestureRightX * 0.8;
this.rightForeArm.rotation.y = this.initialRightForeArmRot.y + (${c.rfY.toFixed(2)}) + gestureRightY * 0.8;
this.rightForeArm.rotation.z = this.initialRightForeArmRot.z + (${c.rfZ.toFixed(2)}) + swayZ + gestureRightZ * 0.8;

// Left Hand (Wrist)
if (this.leftHand && this.initialLeftHandRot) {
  this.leftHand.rotation.x = this.initialLeftHandRot.x + (${c.lhX.toFixed(2)});
  this.leftHand.rotation.y = this.initialLeftHandRot.y + (${c.lhY.toFixed(2)}) + Math.sin(time * 5.0) * 0.18 * gestureScale;
  this.leftHand.rotation.z = this.initialLeftHandRot.z + (${c.lhZ.toFixed(2)});
}

// Right Hand (Wrist)
if (this.rightHand && this.initialRightHandRot) {
  this.rightHand.rotation.x = this.initialRightHandRot.x + (${c.rhX.toFixed(2)});
  this.rightHand.rotation.y = this.initialRightHandRot.y + (${c.rhY.toFixed(2)}) + Math.cos(time * 5.2) * -0.18 * gestureScale;
  this.rightHand.rotation.z = this.initialRightHandRot.z + (${c.rhZ.toFixed(2)});
}`;

    const out = document.getElementById('cal-code-output');
    if (out) out.innerText = snippet;
  }

  // Bind input events
  sliders.forEach(slider => {
    const el = document.getElementById(slider.id);
    const valEl = document.getElementById(slider.id + '-val');
    if (el) {
      el.addEventListener('input', () => {
        const val = parseFloat(el.value);
        window.avatarCalibration[slider.key] = val;
        if (valEl) valEl.innerText = val.toFixed(2);
        updateCodeOutput();
      });
    }
  });

  // Reset calibration
  const btnReset = document.getElementById('btn-reset-calibration');
  if (btnReset) {
    btnReset.addEventListener('click', () => {
      window.avatarCalibration = { ...defaults };
      sliders.forEach(slider => {
        const el = document.getElementById(slider.id);
        const valEl = document.getElementById(slider.id + '-val');
        if (el) {
          el.value = defaults[slider.key];
          if (valEl) valEl.innerText = defaults[slider.key].toFixed(2);
        }
      });
      updateCodeOutput();
    });
  }

  // Initialize output display
  updateCodeOutput();
}

