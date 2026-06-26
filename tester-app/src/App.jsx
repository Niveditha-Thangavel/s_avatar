import React, { useState, useEffect, useRef } from 'react';
import { 
  Activity, 
  Terminal as TermIcon, 
  Settings, 
  Mic, 
  Volume2, 
  Languages, 
  MessageSquare, 
  CheckCircle, 
  AlertCircle, 
  Play, 
  Square,
  Sliders,
  BarChart3,
  Eye,
  RefreshCw,
  Trash2
} from 'lucide-react';
import { AvatarWidget } from './avatar-widget';

// List of supported languages in the backend pipeline
const LANGUAGES = [
  { code: 'en', name: 'English (en)' },
  { code: 'as-IN', name: 'Assamese (as-IN)' },
  { code: 'bn-IN', name: 'Bengali (bn-IN)' },
  { code: 'brx-IN', name: 'Bodo (brx-IN)' },
  { code: 'doi-IN', name: 'Dogri (doi-IN)' },
  { code: 'gu-IN', name: 'Gujarati (gu-IN)' },
  { code: 'hi-IN', name: 'Hindi (hi-IN)' },
  { code: 'kn-IN', name: 'Kannada (kn-IN)' },
  { code: 'ks-IN', name: 'Kashmiri (ks-IN)' },
  { code: 'kok-IN', name: 'Konkani (kok-IN)' },
  { code: 'mai-IN', name: 'Maithili (mai-IN)' },
  { code: 'ml-IN', name: 'Malayalam (ml-IN)' },
  { code: 'mni-IN', name: 'Manipuri (mni-IN)' },
  { code: 'mr-IN', name: 'Marathi (mr-IN)' },
  { code: 'ne-IN', name: 'Nepali (ne-IN)' },
  { code: 'or-IN', name: 'Odia (or-IN)' },
  { code: 'pa-IN', name: 'Punjabi (pa-IN)' },
  { code: 'sa-IN', name: 'Sanskrit (sa-IN)' },
  { code: 'sat-IN', name: 'Santali (sat-IN)' },
  { code: 'sd-IN', name: 'Sindhi (sd-IN)' },
  { code: 'ta-IN', name: 'Tamil (ta-IN)' },
  { code: 'te-IN', name: 'Telugu (te-IN)' },
  { code: 'ur-IN', name: 'Urdu (ur-IN)' }
];

const EMOTIONS = ['neutral', 'happy', 'sad', 'angry', 'surprised', 'fearful'];

// Core 52 ARKit Blendshapes for visualization & manual manipulation
const BLENDSHAPE_NAMES = [
  "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
  "cheekPuff", "cheekSquintLeft", "cheekSquintRight", "eyeBlinkLeft", "eyeBlinkRight",
  "eyeLookDownLeft", "eyeLookDownRight", "eyeLookInLeft", "eyeLookInRight", "eyeLookOutLeft",
  "eyeLookOutRight", "eyeLookUpLeft", "eyeLookUpRight", "eyeSquintLeft", "eyeSquintRight",
  "eyeWideLeft", "eyeWideRight", "jawForward", "jawLeft", "jawOpen", "jawRight",
  "mouthClose", "mouthDimpleLeft", "mouthDimpleRight", "mouthFrownLeft", "mouthFrownRight",
  "mouthFunnel", "mouthLeft", "mouthLowerDownLeft", "mouthLowerDownRight", "mouthPressLeft",
  "mouthPressRight", "mouthPucker", "mouthRight", "mouthRollLower", "mouthRollUpper",
  "mouthShrugLower", "mouthShrugUpper", "mouthSmileLeft", "mouthSmileRight", "mouthStretchLeft",
  "mouthStretchRight", "mouthUpperUpLeft", "mouthUpperUpRight", "noseSneerLeft", "noseSneerRight"
];

export default function App() {
  // Connection states
  const [serverUrl, setServerUrl] = useState('http://34.21.34.119:8765');
  const [healthStatus, setHealthStatus] = useState('idle'); // idle, checking, ok, error
  const [healthData, setHealthData] = useState(null);

  // Chat Translation test states
  const [chatText, setChatText] = useState('Hello, how are you? Welcome to the future of voice avatars.');
  const [chatLang, setChatLang] = useState('hi-IN');
  const [chatLoading, setChatLoading] = useState(false);
  const [chatResult, setChatResult] = useState(null);

  // TTS / Speak states
  const [ttsText, setTtsText] = useState('We are testing the lipsync and the emotions today.');
  const [ttsLang, setTtsLang] = useState('hi-IN');
  const [ttsSpeed, setTtsSpeed] = useState(1.0);
  const [ttsEmotion, setTtsEmotion] = useState('happy');
  const [ttsLoading, setTtsLoading] = useState(false);

  // WebSocket S2S states
  const [wsState, setWsState] = useState('disconnected'); // disconnected, connecting, connected
  const [wsLang, setWsLang] = useState('hi-IN');

  // Terminal logging
  const [logs, setLogs] = useState([]);
  
  // Dashboard Right Panel Tabs
  const [rightTab, setRightTab] = useState('monitor'); // monitor, manual

  // Latency metrics tracking
  const [latencyHistory, setLatencyHistory] = useState([]);

  // UI state for active emotion
  const [activeIdleEmotion, setActiveIdleEmotion] = useState('neutral');

  // Avatar Widget Ref
  const avatarContainerRef = useRef(null);
  const widgetRef = useRef(null);
  const audioCtxRef = useRef(null);
  const nextPlayAtRef = useRef(0);
  const playbackQueueRef = useRef([]);
  const currentActiveSeqRef = useRef(null);
  const wsRef = useRef(null);
  const micStreamRef = useRef(null);
  const micCtxRef = useRef(null);
  const workletNodeRef = useRef(null);
  const animationFrameIdRef = useRef(null);
  const pendingMetaRef = useRef(null);
  const isMutedRef = useRef(false);

  // Sliders values state (local React state for manual tab)
  const [sliderValues, setSliderValues] = useState(
    BLENDSHAPE_NAMES.reduce((acc, name) => ({ ...acc, [name]: 0 }), {})
  );

  // Elements mapping for visualizer bars to bypass React renders
  const barRefs = useRef({});

  // Add line to terminal
  const addLog = (message, type = 'info') => {
    const timestamp = new Date().toLocaleTimeString();
    setLogs(prev => [...prev.slice(-199), { timestamp, text: message, type }]); // Keep last 200 logs
  };

  // Helper to resolve URLs
  const getUrls = () => {
    const trimmed = serverUrl.trim().replace(/\/$/, '');
    const wsProto = trimmed.startsWith('https') ? 'wss' : 'ws';
    const cleanBase = trimmed.replace(/^https?:\/\//, '');
    return {
      health: `${trimmed}/health`,
      chat: `${trimmed}/chat`,
      tts: `${trimmed}/tts`,
      speak: (emotion) => `${trimmed}/speak/${emotion}`,
      audio: (url) => url.startsWith('http') ? url : `${trimmed}${url}`,
      ws: `${wsProto}://${cleanBase}/ws/s2s`
    };
  };

  // ── 1. Init Avatar Widget ──────────────────────────────────────────────────
  useEffect(() => {
    if (avatarContainerRef.current) {
      addLog('Initializing 3D Avatar Widget...', 'info');
      try {
        const widget = new AvatarWidget({
          container: avatarContainerRef.current,
          modelUrl: '/avatar_head.glb',
          onReady: () => {
            addLog('● 3D Avatar Widget ready. Model loaded.', 'success');
            widget.setEmotion('neutral');
          }
        });
        widgetRef.current = widget;

        // Monitor loop for blendshapes visualizer at 60 FPS
        const monitorLoop = () => {
          if (widgetRef.current && widgetRef.current.isLoaded) {
            const weights = widgetRef.current.isSpeaking 
              ? widgetRef.current.activeTargetWeights 
              : widgetRef.current.emotionWeights;

            // Direct DOM update to avoid React re-renders overhead
            BLENDSHAPE_NAMES.forEach(name => {
              const val = weights[name] || 0.0;
              const barElement = barRefs.current[name];
              if (barElement) {
                barElement.style.width = `${val * 100}%`;
                if (val > 0.05) {
                  barElement.classList.add('active');
                } else {
                  barElement.classList.remove('active');
                }
              }
            });
          }
          animationFrameIdRef.current = requestAnimationFrame(monitorLoop);
        };
        animationFrameIdRef.current = requestAnimationFrame(monitorLoop);

      } catch (err) {
        addLog(`Failed to initialize Avatar Widget: ${err.message}`, 'error');
      }
    }

    return () => {
      if (widgetRef.current) {
        widgetRef.current.dispose();
        widgetRef.current = null;
      }
      if (animationFrameIdRef.current) {
        cancelAnimationFrame(animationFrameIdRef.current);
      }
    };
  }, []);

  // Web Audio Context initializer
  const ensureAudioContext = () => {
    if (!audioCtxRef.current) {
      audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (audioCtxRef.current.state === 'suspended') {
      audioCtxRef.current.resume();
    }
    return audioCtxRef.current;
  };

  // Clean active session audio
  const clearPlayback = () => {
    playbackQueueRef.current.forEach(s => {
      if (s.flushTimer) clearTimeout(s.flushTimer);
    });
    playbackQueueRef.current = [];
    currentActiveSeqRef.current = null;
    nextPlayAtRef.current = 0;
    if (widgetRef.current) {
      widgetRef.current.clearAnimation();
    }
  };

  // Render loop to sync audio with Three.js morphs
  useEffect(() => {
    let activeTimer;
    const tick = () => {
      activeTimer = requestAnimationFrame(tick);
      if (!audioCtxRef.current) return;
      
      const nowAudioTime = audioCtxRef.current.currentTime;
      const queue = playbackQueueRef.current;

      // Clean up past played items
      while (queue.length > 0 && 
             queue[0].flushed && 
             queue[0].startTime !== null && 
             nowAudioTime > queue[0].startTime + queue[0].duration + 1.0) {
        const old = queue.shift();
        if (old.flushTimer) clearTimeout(old.flushTimer);
      }

      // Find currently playing sentence
      let activeSentence = null;
      for (const s of queue) {
        if (s.flushed && s.startTime !== null) {
          if (nowAudioTime <= s.startTime + s.duration + 0.2) {
            activeSentence = s;
            break;
          }
        }
      }

      if (activeSentence) {
        if (currentActiveSeqRef.current !== activeSentence.seq) {
          currentActiveSeqRef.current = activeSentence.seq;
          if (widgetRef.current) {
            if (activeSentence.matrix) {
              widgetRef.current.setAnimationMatrix(activeSentence.matrix);
            } else {
              widgetRef.current.clearAnimation();
            }
            widgetRef.current.syncAudio(audioCtxRef.current, activeSentence.startTime);
          }
        } else {
          // Handle delayed matrix mapping
          if (widgetRef.current && activeSentence.matrix && !widgetRef.current.currentAnimationMatrix) {
            widgetRef.current.setAnimationMatrix(activeSentence.matrix);
            widgetRef.current.syncAudio(audioCtxRef.current, activeSentence.startTime);
          }
        }
      } else {
        if (currentActiveSeqRef.current !== null) {
          currentActiveSeqRef.current = null;
          if (widgetRef.current) {
            widgetRef.current.clearAnimation();
          }
          isMutedRef.current = false;
        }
      }
    };
    activeTimer = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(activeTimer);
  }, []);

  // Stream individual audio chunks gaplessly as they arrive
  const playChunk = (sentence, f32, sampleRate) => {
    const ctx = ensureAudioContext();
    
    // Store chunk for analytics
    sentence.chunks.push({ f32, sampleRate });

    if (sentence.startTime === null) {
      const startTime = nextPlayAtRef.current < ctx.currentTime ? ctx.currentTime : nextPlayAtRef.current;
      sentence.startTime = startTime;
      nextPlayAtRef.current = startTime;
      sentence.flushed = true; // Mark as flushed/playing for render loop active checks
      addLog(`Started playing S2S audio stream seq=${sentence.seq}`, 'info');
    }

    const targetTime = nextPlayAtRef.current < ctx.currentTime ? ctx.currentTime : nextPlayAtRef.current;
    
    const buf = ctx.createBuffer(1, f32.length, sampleRate);
    buf.getChannelData(0).set(f32);
    
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.start(targetTime);
    
    nextPlayAtRef.current = targetTime + buf.duration;
    sentence.duration = nextPlayAtRef.current - sentence.startTime;
  };

  // Play standalone backend-generated WAV and blendshape matrix
  const playTTSResponse = async (audioUrl, matrix, text) => {
    ensureAudioContext();
    clearPlayback();
    addLog(`Loading audio file: ${audioUrl}`, 'info');
    
    try {
      const res = await fetch(audioUrl);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const arrayBuf = await res.arrayBuffer();
      
      const ctx = ensureAudioContext();
      const audioBuffer = await ctx.decodeAudioData(arrayBuf);
      
      addLog(`Playing decoded TTS wav: "${text.substring(0, 30)}..."`, 'success');
      
      const startTime = ctx.currentTime;
      const src = ctx.createBufferSource();
      src.buffer = audioBuffer;
      src.connect(ctx.destination);
      src.start(startTime);
      
      if (widgetRef.current) {
        widgetRef.current.setAnimationMatrix(matrix);
        widgetRef.current.syncAudio(ctx, startTime);
      }
      
      playbackQueueRef.current.push({
        seq: 'static',
        chunks: [],
        matrix: matrix,
        startTime: startTime,
        duration: audioBuffer.duration,
        flushed: true
      });
      
    } catch (err) {
      addLog(`Failed playing audio URL: ${err.message}`, 'error');
    }
  };

  // ── 2. Test Endpoints ──────────────────────────────────────────────────────
  
  // Health
  const checkHealth = async () => {
    setHealthStatus('checking');
    addLog(`Checking backend health: GET ${getUrls().health}...`, 'info');
    try {
      const start = performance.now();
      const res = await fetch(getUrls().health);
      const elapsed = (performance.now() - start).toFixed(0);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setHealthData(data);
      setHealthStatus('ok');
      addLog(`Health OK (${elapsed}ms). Services: STT=${data.services.vexyl_stt}, Translator=${data.services.translator}, TTS=${data.services.tts}`, 'success');
    } catch (err) {
      setHealthStatus('error');
      setHealthData(null);
      addLog(`Health check failed: ${err.message}`, 'error');
    }
  };

  // Chat Translation
  const runChatTest = async () => {
    setChatLoading(true);
    addLog(`Sending translation request: POST ${getUrls().chat} (lang=${chatLang})...`, 'info');
    try {
      const start = performance.now();
      const res = await fetch(getUrls().chat, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: chatText, lang: chatLang })
      });
      const elapsed = (performance.now() - start).toFixed(0);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setChatResult(data);
      addLog(`Translation complete (${elapsed}ms). Result: "${data.translated}"`, 'success');
    } catch (err) {
      setChatResult({ error: err.message });
      addLog(`Translation request failed: ${err.message}`, 'error');
    } finally {
      setChatLoading(false);
    }
  };

  // TTS (Text-to-Speech)
  const runTTSTest = async () => {
    setTtsLoading(true);
    addLog(`Requesting TTS: POST ${getUrls().tts} (lang=${ttsLang}, speed=${ttsSpeed})...`, 'info');
    try {
      const start = performance.now();
      const res = await fetch(getUrls().tts, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: ttsText, lang: ttsLang, speed: parseFloat(ttsSpeed) })
      });
      const elapsed = (performance.now() - start).toFixed(0);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      
      const audioUrl = getUrls().audio(data.audio_url);
      addLog(`TTS Success (${elapsed}ms). Received ${data.animation_matrix ? data.animation_matrix.length : 0} lipsync frames.`, 'success');
      
      await playTTSResponse(audioUrl, data.animation_matrix, ttsText);
      
    } catch (err) {
      addLog(`TTS request failed: ${err.message}`, 'error');
    } finally {
      setTtsLoading(false);
    }
  };

  // Speak with Emotion
  const runEmotionTest = async () => {
    setTtsLoading(true);
    addLog(`Requesting Emotion TTS: POST ${getUrls().speak(ttsEmotion)} (lang=${ttsLang}, speed=${ttsSpeed})...`, 'info');
    try {
      const start = performance.now();
      const res = await fetch(getUrls().speak(ttsEmotion), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: ttsText, lang: ttsLang, speed: parseFloat(ttsSpeed) })
      });
      const elapsed = (performance.now() - start).toFixed(0);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      
      const audioUrl = getUrls().audio(data.audio_url);
      addLog(`Emotion TTS Success (${elapsed}ms). Baked Emotion: ${data.emotion}. Received ${data.animation_matrix ? data.animation_matrix.length : 0} lipsync frames.`, 'success');
      
      await playTTSResponse(audioUrl, data.animation_matrix, ttsText);
      
    } catch (err) {
      addLog(`Emotion TTS request failed: ${err.message}`, 'error');
    } finally {
      setTtsLoading(false);
    }
  };

  // ── 3. WebSocket S2S (Speech-to-Speech) ───────────────────────────────────
  const startS2S = async () => {
    if (wsState !== 'disconnected') return;
    setWsState('connecting');
    clearPlayback();
    setLatencyHistory([]);
    ensureAudioContext();
    
    const wsUrl = getUrls().ws;
    addLog(`Connecting S2S WebSocket: ${wsUrl}...`, 'info');
    
    try {
      const ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;
      
      ws.onopen = async () => {
        setWsState('connected');
        addLog(`WebSocket connected. Starting session: lang=${wsLang}...`, 'success');
        
        ws.send(JSON.stringify({
          type: 'start',
          lang: wsLang,
          session_id: `tester_${Date.now()}`
        }));
        
        // Start microphone capture
        try {
          micStreamRef.current = await navigator.mediaDevices.getUserMedia({
            audio: {
              sampleRate: { ideal: 16000 },
              channelCount: 1,
              echoCancellation: true,
              noiseSuppression: true
            },
            video: false
          });
          
          const micCtx = new AudioContext({ sampleRate: 16000 });
          micCtxRef.current = micCtx;
          
          const code = `
            class P extends AudioWorkletProcessor {
              process(inputs) {
                const inputChannel = inputs[0]?.[0];
                if (!inputChannel) return true;
                const buffer = new Int16Array(inputChannel.length);
                for (let i = 0; i < inputChannel.length; i++) {
                  const s = Math.max(-1, Math.min(1, inputChannel[i]));
                  buffer[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                this.port.postMessage(buffer.buffer, [buffer.buffer]);
                return true;
              }
            }
            registerProcessor('pp', P);
          `;
          
          const blob = new Blob([code], { type: 'application/javascript' });
          const workletUrl = URL.createObjectURL(blob);
          await micCtx.audioWorklet.addModule(workletUrl);
          URL.revokeObjectURL(workletUrl);
          
          const src = micCtx.createMediaStreamSource(micStreamRef.current);
          const workletNode = new AudioWorkletNode(micCtx, 'pp');
          workletNodeRef.current = workletNode;
          
          workletNode.port.onmessage = (e) => {
            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && !isMutedRef.current) {
              wsRef.current.send(e.data);
            }
          };
          
          src.connect(workletNode);
          addLog('Microphone streaming started (16kHz PCM Int16)', 'info');
          
        } catch (err) {
          addLog(`Microphone access failed: ${err.message}`, 'error');
          stopS2S();
        }
      };

      ws.onmessage = (evt) => {
        // Binary audio chunk payload
        if (evt.data instanceof ArrayBuffer) {
          if (!pendingMetaRef.current) return;
          const { seq, sample_rate } = pendingMetaRef.current;
          pendingMetaRef.current = null;

          const f32 = new Float32Array(evt.data);
          const sentence = playbackQueueRef.current.find(s => s.seq === seq);
          if (sentence) {
            playChunk(sentence, f32, sample_rate);
          }
          return;
        }

        // JSON text payload
        const msg = JSON.parse(evt.data);
        
        switch (msg.type) {
          case 'transcript':
            addLog(`[WS Received] Transcript: "${msg.text}"`, 'ws-recv');
            break;
            
          case 'tts_start':
            addLog(`[WS Received] TTS Start seq=${msg.seq}: "${msg.text}"`, 'ws-recv');
            isMutedRef.current = true;
            playbackQueueRef.current.push({
              seq: msg.seq,
              chunks: [],
              matrix: null,
              startTime: null,
              duration: 0,
              flushed: false,
              flushTimer: null
            });
            break;
            
          case 'audio_chunk':
            pendingMetaRef.current = { seq: msg.seq, sample_rate: msg.sample_rate };
            break;
            
          case 'blendshape_matrix':
            addLog(`[WS Received] Blendshape matrix seq=${msg.seq}: ${msg.matrix ? msg.matrix.length : 0} frames`, 'ws-recv');
            const sentence = playbackQueueRef.current.find(s => s.seq === msg.seq);
            if (sentence) {
              sentence.matrix = msg.matrix;
            }
            break;
            
          case 'tts_end':
            addLog(`[WS Received] TTS End seq=${msg.seq}`, 'ws-recv');
            break;

          case 'pipeline_latency':
            addLog(`[WS Received] Latency metrics seq=${msg.seq}`, 'info');
            setLatencyHistory(prev => [msg, ...prev]);
            break;
            
          case 'pipeline_status':
            // heartbeat, do not print to avoid spamming
            break;
            
          case 'error':
            addLog(`[WS Error] ${msg.message}`, 'error');
            break;
            
          default:
            addLog(`[WS Received] Unknown type: ${msg.type}`, 'info');
        }
      };

      ws.onclose = () => {
        setWsState('disconnected');
        addLog('WebSocket session closed.', 'warn');
        teardownMic();
      };

      ws.onerror = (err) => {
        addLog('WebSocket connection encountered an error.', 'error');
        setWsState('disconnected');
      };
      
    } catch (err) {
      addLog(`Failed to connect WebSocket: ${err.message}`, 'error');
      setWsState('disconnected');
    }
  };

  const stopS2S = () => {
    if (wsRef.current) {
      if (wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'stop' }));
      }
      wsRef.current.close();
      wsRef.current = null;
    }
    teardownMic();
    clearPlayback();
    setWsState('disconnected');
  };

  const teardownMic = () => {
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop());
      micStreamRef.current = null;
    }
    if (micCtxRef.current) {
      micCtxRef.current.close();
      micCtxRef.current = null;
    }
    workletNodeRef.current = null;
    isMutedRef.current = false;
  };

  // Change idle emotion
  const handleIdleEmotionChange = (emotion) => {
    setActiveIdleEmotion(emotion);
    if (widgetRef.current) {
      addLog(`Updating idle emotion to: ${emotion}`, 'info');
      widgetRef.current.setEmotion(emotion);
    }
  };

  // Manual blendshape sliders adjustment
  const handleSliderChange = (name, value) => {
    const val = parseFloat(value);
    setSliderValues(prev => ({ ...prev, [name]: val }));
    if (widgetRef.current) {
      // Direct update to 3D canvas
      widgetRef.current._setMorphTarget(name, val);
    }
  };

  return (
    <div className="dashboard-layout">
      {/* Header Panel */}
      <header className="glass-panel">
        <h1>🗣️ S2S Avatar <span>TESTER v3</span></h1>
        
        <div className="server-config">
          <div className="input-group">
            <label htmlFor="server-input"><Settings size={14} style={{ marginRight: '6px' }} /> Server</label>
            <input 
              id="server-input"
              type="text" 
              value={serverUrl} 
              onChange={(e) => setServerUrl(e.target.value)} 
              placeholder="e.g. http://34.21.34.119"
            />
          </div>
          <button onClick={checkHealth} disabled={healthStatus === 'checking'}>
            <Activity size={14} /> Ping Health
          </button>
        </div>

        <div className="status-badge">
          <span className={`status-bulb ${healthStatus === 'ok' ? 'green' : healthStatus === 'error' ? 'rose' : 'orange'}`}></span>
          {healthStatus === 'ok' ? 'Online' : healthStatus === 'error' ? 'Offline' : 'Ready'}
        </div>
      </header>

      {/* Main Grid: Left Tests, Right 3D */}
      <div className="main-grid">
        
        {/* Left Column: API & Service testing cards */}
        <div className="left-column">
          
          {/* Health Information Panel */}
          {healthData && (
            <div className="glass-panel" style={{ padding: '12px' }}>
              <div className="card-header" style={{ marginBottom: '8px', paddingBottom: '4px' }}>
                <h2><CheckCircle size={14} /> Server Diagnostics</h2>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', fontSize: '12px' }}>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>STT Service:</span>
                  <div style={{ color: 'var(--accent-cyan)', fontFamily: 'var(--font-mono)', fontSize: '11px', marginTop: '2px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {healthData.services.vexyl_stt}
                  </div>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>Translator:</span>
                  <div style={{ color: 'var(--accent-green)', fontWeight: 'bold', marginTop: '2px' }}>
                    {healthData.services.translator}
                  </div>
                </div>
                <div>
                  <span style={{ color: 'var(--text-secondary)' }}>TTS Service:</span>
                  <div style={{ color: 'var(--accent-orange)', marginTop: '2px' }}>
                    {healthData.services.tts}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Translation Debug Card */}
          <div className="glass-panel">
            <div className="card-header">
              <h2><Languages size={16} /> IndicTrans2 Translation</h2>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>POST /chat</span>
            </div>
            
            <div className="form-field">
              <label htmlFor="chat-input-textarea">Source English Text</label>
              <textarea 
                id="chat-input-textarea"
                value={chatText} 
                onChange={(e) => setChatText(e.target.value)}
              />
            </div>

            <div className="control-row">
              <div>
                <label htmlFor="chat-lang-select" style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px' }}>Target Language</label>
                <select id="chat-lang-select" value={chatLang} onChange={(e) => setChatLang(e.target.value)}>
                  {LANGUAGES.filter(l => l.code !== 'en').map(l => (
                    <option key={l.code} value={l.code}>{l.name}</option>
                  ))}
                </select>
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <button 
                  onClick={runChatTest} 
                  disabled={chatLoading || healthStatus === 'idle'}
                  style={{ width: '100%' }}
                >
                  {chatLoading ? <RefreshCw size={14} className="animate-spin" /> : <MessageSquare size={14} />} Translate
                </button>
              </div>
            </div>

            {chatResult && (
              <div className="json-display">
                {chatResult.error ? (
                  <span style={{ color: 'var(--accent-rose)' }}>Error: {chatResult.error}</span>
                ) : (
                  <>
                    <div style={{ borderBottom: '1px solid rgba(255,255,255,0.05)', pb: '4px', mb: '4px' }}>
                      <span style={{ color: 'var(--accent-cyan)' }}>Target:</span> {chatResult.flores_tgt}
                    </div>
                    <div>
                      <span style={{ color: 'var(--accent-green)' }}>Translation:</span> {chatResult.translated}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>

          {/* TTS & Lipsync Testing Card */}
          <div className="glass-panel">
            <div className="card-header">
              <h2><Volume2 size={16} /> Text to Speech & Lipsync</h2>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>POST /tts & /speak</span>
            </div>

            <div className="form-field">
              <label htmlFor="tts-text-textarea">Text to Synthesize</label>
              <textarea 
                id="tts-text-textarea"
                value={ttsText} 
                onChange={(e) => setTtsText(e.target.value)}
                placeholder="Type text for TTS..."
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px', marginBottom: '12px' }}>
              <div>
                <label htmlFor="tts-lang-select" style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px' }}>Language</label>
                <select id="tts-lang-select" value={ttsLang} onChange={(e) => setTtsLang(e.target.value)}>
                  {LANGUAGES.map(l => (
                    <option key={l.code} value={l.code}>{l.name}</option>
                  ))}
                </select>
              </div>
              
              <div>
                <label htmlFor="tts-speed-input" style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px' }}>Speed ({ttsSpeed}x)</label>
                <input 
                  id="tts-speed-input"
                  type="range" 
                  min="0.5" 
                  max="2.0" 
                  step="0.1" 
                  value={ttsSpeed} 
                  onChange={(e) => setTtsSpeed(e.target.value)}
                  style={{ width: '100%', height: '32px' }}
                />
              </div>

              <div>
                <label htmlFor="tts-emotion-select" style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px' }}>Emotion</label>
                <select id="tts-emotion-select" value={ttsEmotion} onChange={(e) => setTtsEmotion(e.target.value)}>
                  {EMOTIONS.map(emo => (
                    <option key={emo} value={emo}>{emo.toUpperCase()}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="btn-container">
              <button 
                className="secondary" 
                onClick={runTTSTest} 
                disabled={ttsLoading}
              >
                <Play size={14} /> Neutral TTS
              </button>
              <button 
                onClick={runEmotionTest} 
                disabled={ttsLoading}
              >
                <Play size={14} /> TTS with Baked Emotion
              </button>
            </div>
          </div>

          {/* WebSocket Real-time Speech-to-Speech Card */}
          <div className="glass-panel">
            <div className="card-header">
              <h2><Mic size={16} /> Live WebSocket Pipeline</h2>
              <span className={`status-badge ${wsState === 'connected' ? 'success' : ''}`}>
                <span className={`status-bulb ${wsState === 'connected' ? 'green' : wsState === 'connecting' ? 'orange' : 'rose'}`}></span>
                {wsState.toUpperCase()}
              </span>
            </div>

            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px', lineHeight: '1.4' }}>
              Stream your microphone. The server runs real-time Speech-To-Text (Vexyl), translates it, synthesizes speech with lipsync frames, and streams it back.
            </p>

            <div className="control-row" style={{ gridTemplateColumns: '1fr 140px' }}>
              <div>
                <select aria-label="Speech to Speech language" value={wsLang} onChange={(e) => setWsLang(e.target.value)}>
                  {LANGUAGES.map(l => (
                    <option key={l.code} value={l.code}>{l.name}</option>
                  ))}
                </select>
              </div>
              <div>
                {wsState === 'disconnected' ? (
                  <button onClick={startS2S} style={{ width: '100%', background: 'var(--accent-green)' }}>
                    <Mic size={14} /> Start Stream
                  </button>
                ) : (
                  <button onClick={stopS2S} className="danger" style={{ width: '100%' }}>
                    <Square size={14} /> Stop Stream
                  </button>
                )}
              </div>
            </div>
          </div>

        </div>

        {/* Right Column: 3D Avatar Rendering, Idle Emotions and Sliders */}
        <div className="right-column">
          
          {/* WebGL Canvas Container */}
          <div className="glass-panel" style={{ padding: '10px' }}>
            <div className="avatar-view-container">
              <div id="avatar-container" ref={avatarContainerRef}></div>
              <div className="avatar-overlay-status">
                {currentActiveSeqRef.current ? '🔊 Speaking...' : '● Idle'}
              </div>
            </div>
          </div>

          {/* Idle Emotion Override Control */}
          <div className="glass-panel">
            <div className="card-header" style={{ marginBottom: '8px' }}>
              <h2><Eye size={16} /> Idle Expression Override</h2>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Interpolates client-side at 5 rad/s</span>
            </div>
            
            <div className="emotion-btn-grid">
              {EMOTIONS.map(emo => (
                <button 
                  key={emo} 
                  className={`emotion-badge-btn ${activeIdleEmotion === emo ? 'active' : ''}`}
                  onClick={() => handleIdleEmotionChange(emo)}
                >
                  {emo}
                </button>
              ))}
            </div>
          </div>

          {/* Diagnostics: Realtime Blendshapes Visualizer OR Manual sliders */}
          <div className="glass-panel" style={{ flex: 1, minHeight: '380px', display: 'flex', flexDirection: 'column' }}>
            
            <div className="tab-row">
              <button 
                className={`tab-btn ${rightTab === 'monitor' ? 'active' : ''}`}
                onClick={() => setRightTab('monitor')}
              >
                <BarChart3 size={14} style={{ marginRight: '6px' }} /> Real-time Blendshape Monitor
              </button>
              <button 
                className={`tab-btn ${rightTab === 'manual' ? 'active' : ''}`}
                onClick={() => setRightTab('manual')}
              >
                <Sliders size={14} style={{ marginRight: '6px' }} /> Manual Sliders
              </button>
              <button 
                className={`tab-btn ${rightTab === 'latency' ? 'active' : ''}`}
                onClick={() => setRightTab('latency')}
              >
                <Activity size={14} style={{ marginRight: '6px' }} /> Latency Monitor
              </button>
            </div>

            {rightTab === 'monitor' && (
              <div className="blendshape-visualizer">
                {BLENDSHAPE_NAMES.map(name => (
                  <div key={name} className="blendshape-bar-row">
                    <div className="blendshape-bar-header">
                      <span>{name}</span>
                    </div>
                    <div className="blendshape-bar-track">
                      <div 
                        className="blendshape-bar-fill" 
                        ref={el => barRefs.current[name] = el}
                      ></div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {rightTab === 'manual' && (
              <div className="blendshape-sliders-container">
                <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '8px' }}>
                  Adjust ARKit morph targets manually. Idle animation weights will be bypassed until you speak.
                </p>
                {BLENDSHAPE_NAMES.map(name => (
                  <div key={name} className="slider-row">
                    <span className="label" title={name}>{name}</span>
                    <input 
                      type="range" 
                      min="0" 
                      max="1" 
                      step="0.05"
                      value={sliderValues[name] || 0}
                      onChange={(e) => handleSliderChange(name, e.target.value)}
                    />
                    <span className="value">{(sliderValues[name] || 0).toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}

            {rightTab === 'latency' && (() => {
              const formatTime = (ms) => {
                if (ms === undefined || ms === null) return '0ms';
                if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
                return `${ms}ms`;
              };

              if (latencyHistory.length === 0) {
                return (
                  <div className="latency-empty-state">
                    <Activity size={28} style={{ color: 'var(--text-muted)', marginBottom: '8px' }} />
                    <p>Waiting for voice interactions...</p>
                    <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>Speak in Hindi/Tamil to see latency.</span>
                  </div>
                );
              }

              const activeSegment = latencyHistory[0];
              const stt = activeSegment.timings?.stt_inference_ms || 0;
              const translation = activeSegment.timings?.translation_ms || 0;
              const tts = activeSegment.timings?.tts_ms || 0;
              const panto = activeSegment.timings?.panto_ms || 0;
              const total = stt + translation + tts + panto;

              return (
                <div className="latency-monitor-container">
                  <div className="latency-summary-strip">
                    <div className="summary-card total">
                      <span className="label">Total S2S Latency</span>
                      <span className="value">{formatTime(total)}</span>
                    </div>
                    <div className="summary-card speed">
                      <span className="label">TTS Engine</span>
                      <span className="value">{formatTime(tts)}</span>
                    </div>
                    <div className="summary-card voice">
                      <span className="label">ASR Decode</span>
                      <span className="value">{formatTime(stt)}</span>
                    </div>
                  </div>

                  <div className="latency-timeline">
                    <div className="timeline-item">
                      <div className="timeline-badge asr">ASR</div>
                      <div className="timeline-content">
                        <div className="timeline-header">
                          <h4>1. Input Transcription (STT)</h4>
                          <span className="time-badge">{formatTime(stt)}</span>
                        </div>
                        <p className="timeline-text">"{activeSegment.source_text}"</p>
                      </div>
                    </div>

                    <div className="timeline-item">
                      <div className="timeline-badge response">LLM</div>
                      <div className="timeline-content">
                        <div className="timeline-header">
                          <h4>2. English Response Match</h4>
                          <span className="time-badge instant">Instant (Static)</span>
                        </div>
                        <p className="timeline-text">"{activeSegment.response_text}"</p>
                      </div>
                    </div>

                    <div className="timeline-item">
                      <div className="timeline-badge trans">MT</div>
                      <div className="timeline-content">
                        <div className="timeline-header">
                          <h4>3. Direct Indic Translation</h4>
                          <span className="time-badge">{formatTime(translation)}</span>
                        </div>
                        <p className="timeline-text">"{activeSegment.indic_text}"</p>
                      </div>
                    </div>

                    <div className="timeline-item">
                      <div className="timeline-badge tts">TTS</div>
                      <div className="timeline-content">
                        <div className="timeline-header">
                          <h4>4. Speech Synthesis (TTS)</h4>
                          <span className="time-badge">{formatTime(tts)}</span>
                        </div>
                        <p className="timeline-text">PCM Audio generation complete</p>
                      </div>
                    </div>

                    <div className="timeline-item">
                      <div className="timeline-badge sync">SYNC</div>
                      <div className="timeline-content">
                        <div className="timeline-header">
                          <h4>5. Blendshape Extraction</h4>
                          <span className="time-badge">{formatTime(panto)}</span>
                        </div>
                        <p className="timeline-text">Generated 3D mesh animation frames</p>
                      </div>
                    </div>
                  </div>

                  <div className="latency-history-section">
                    <h3>Interaction History</h3>
                    <div className="history-table-wrapper">
                      <table className="history-table">
                        <thead>
                          <tr>
                            <th>Seq</th>
                            <th>ASR Transcript</th>
                            <th>STT</th>
                            <th>Trans</th>
                            <th>TTS</th>
                            <th>Sync</th>
                            <th>Total</th>
                          </tr>
                        </thead>
                        <tbody>
                          {latencyHistory.map((seg, idx) => {
                            const segStt = seg.timings?.stt_inference_ms || 0;
                            const segTrans = seg.timings?.translation_ms || 0;
                            const segTts = seg.timings?.tts_ms || 0;
                            const segPanto = seg.timings?.panto_ms || 0;
                            const segTotal = segStt + segTrans + segTts + segPanto;
                            return (
                              <tr key={seg.seq || idx}>
                                <td>#{seg.seq}</td>
                                <td title={seg.source_text} className="truncate-cell">{seg.source_text}</td>
                                <td>{formatTime(segStt)}</td>
                                <td>{formatTime(segTrans)}</td>
                                <td>{formatTime(segTts)}</td>
                                <td>{formatTime(segPanto)}</td>
                                <td className="bold-cell">{formatTime(segTotal)}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              );
            })()}

          </div>

        </div>

      </div>

      {/* Terminal Log Output */}
      <div className="terminal-panel">
        <div className="terminal-header">
          <div className="terminal-title">
            <TermIcon size={14} /> PIPELINE ACTIVITY & TEST LOGS
          </div>
          <div className="terminal-controls">
            <span className="terminal-clear" onClick={() => setLogs([])}>
              <Trash2 size={12} style={{ display: 'inline', marginRight: '4px', verticalAlign: 'text-bottom' }} /> Clear Logs
            </span>
          </div>
        </div>
        
        <div className="terminal-body">
          {logs.length === 0 ? (
            <div style={{ color: 'var(--text-muted)' }}>Waiting for transactions or WS frames...</div>
          ) : (
            logs.map((log, index) => (
              <div key={index} className={`terminal-line ${log.type}`}>
                <span className="timestamp">[{log.timestamp}]</span>
                <span className="msg">{log.text}</span>
              </div>
            ))
          )}
        </div>
      </div>

    </div>
  );
}
