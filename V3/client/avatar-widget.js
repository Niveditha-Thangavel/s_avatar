/**
 * avatar-widget.js — Standalone 3D Avatar widget for embedding into any website.
 *
 * Usage:
 *   import { AvatarWidget } from './avatar-widget.js';
 *
 *   const widget = new AvatarWidget({
 *     container: document.getElementById('avatar-container'),
 *     modelUrl: '/avatar_head.glb',
 *     // optional:
 *     // onReady: () => console.log('avatar ready'),
 *     // calibration: { laX:-1.82, laY:-2.42, laZ:3.14, ... }
 *   });
 *
 *   widget.setAnimationMatrix(matrix);
 *   widget.clearAnimation();
 *   widget.setEmotion('happy');
 *
 * Requires Three.js (tested with r184). Include via importmap:
 *
 *   <script type="importmap">
 *   { "imports": {
 *       "three": "https://unpkg.com/three@0.184.0/build/three.module.js",
 *       "three/addons/": "https://unpkg.com/three@0.184.0/examples/jsm/"
 *   } }
 *   </script>
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export class AvatarWidget {
  constructor(opts = {}) {
    this.container = opts.container;
    if (!this.container) throw new Error('AvatarWidget: container is required');

    const modelUrl = opts.modelUrl || '/avatar_head.glb';
    this.onReady = opts.onReady || null;

    // Three.js objects
    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;
    this.avatarScene = null;

    // Avatar bones
    this.morphMeshes = [];
    this.leftEye = null;
    this.rightEye = null;
    this.headBone = null;
    this.neckBone = null;
    this.leftArm = null;
    this.rightArm = null;
    this.leftForeArm = null;
    this.rightForeArm = null;
    this.leftHand = null;
    this.rightHand = null;
    this.initialLeftArmRot = null;
    this.initialRightArmRot = null;
    this.initialLeftForeArmRot = null;
    this.initialRightForeArmRot = null;
    this.initialLeftHandRot = null;
    this.initialRightHandRot = null;

    this.isLoaded = false;
    this.isSpeaking = false;

    // Animation matrix state
    this.currentAnimationMatrix = null;
    this.activeTargetWeights = {};
    this.animationStartTime = 0;

    // Blink state machine
    this.blinkVal = 0;
    this.blinkTimer = 0;
    this.blinkState = 'idle';
    this.blinkDuration = 0.09;
    this.blinkStateTime = 0;
    this.nextBlinkInterval = 3000 + Math.random() * 3000;

    // Gaze saccades
    this.gazeOffset = { x: 0, y: 0 };
    this.gazeTarget = { x: 0, y: 0 };
    this.gazeTimer = 0;
    this.nextGazeInterval = 1000 + Math.random() * 2000;

    // Idle breathing (overridden per emotion)
    this.breathingTime = 0;
    this.breathingSpeed = 1.8;
    this.breathingAmplitude = 0.022;

    // Output transforms
    this.rotation = { x: 0, y: 0, z: 0 };

    // ── Emotion-driven body motion ──────────────────────────────────────────
    this._emotionBody = {
      neutral:   { breathSpeed: 1.8,  breathAmp: 0.022, swayAmp: 0.02,  headBiasY: 0.00 },
      happy:     { breathSpeed: 2.4,  breathAmp: 0.030, swayAmp: 0.035, headBiasY: 0.04 },
      sad:       { breathSpeed: 1.2,  breathAmp: 0.015, swayAmp: 0.008, headBiasY: -0.06 },
      angry:     { breathSpeed: 3.2,  breathAmp: 0.038, swayAmp: 0.012, headBiasY: 0.00 },
      surprised: { breathSpeed: 2.8,  breathAmp: 0.035, swayAmp: 0.025, headBiasY: 0.05 },
      fearful:   { breathSpeed: 2.2,  breathAmp: 0.028, swayAmp: 0.010, headBiasY: -0.03 },
    };
    this._bodyParams = { breathSpeed: 1.8, breathAmp: 0.022, swayAmp: 0.02, headBiasY: 0.00 };

    // Emotion idle blendshape targets
    this.currentEmotion = 'neutral';
    this.emotionTargets = {
      neutral: {},
      happy: {
        mouthSmileLeft: 0.45, mouthSmileRight: 0.45,
        cheekSquintLeft: 0.25, cheekSquintRight: 0.25,
        browOuterUpLeft: 0.20, browOuterUpRight: 0.20,
      },
      sad: {
        mouthFrownLeft: 0.55, mouthFrownRight: 0.55,
        browInnerUp: 0.45, browDownLeft: 0.15, browDownRight: 0.15,
      },
      angry: {
        browDownLeft: 0.65, browDownRight: 0.65,
        eyeSquintLeft: 0.35, eyeSquintRight: 0.35,
        mouthFrownLeft: 0.25, mouthFrownRight: 0.25,
        noseSneerLeft: 0.20, noseSneerRight: 0.20,
      },
      surprised: {
        eyeWideLeft: 0.55, eyeWideRight: 0.55,
        browInnerUp: 0.55, browOuterUpLeft: 0.35, browOuterUpRight: 0.35,
        mouthShrugUpper: 0.20,
      },
      fearful: {
        eyeWideLeft: 0.45, eyeWideRight: 0.45,
        browInnerUp: 0.50, browOuterUpLeft: 0.30, browOuterUpRight: 0.30,
        mouthFrownLeft: 0.25, mouthFrownRight: 0.25,
      },
    };
    this.emotionWeights = {
      mouthSmileLeft: 0, mouthSmileRight: 0,
      cheekSquintLeft: 0, cheekSquintRight: 0,
      browOuterUpLeft: 0, browOuterUpRight: 0,
      mouthFrownLeft: 0, mouthFrownRight: 0,
      browInnerUp: 0, browDownLeft: 0, browDownRight: 0,
      eyeSquintLeft: 0, eyeSquintRight: 0,
      eyeWideLeft: 0, eyeWideRight: 0,
      mouthShrugUpper: 0,
      noseSneerLeft: 0, noseSneerRight: 0,
    };

    // Posture calibration
    this.calibration = opts.calibration || {
      laX: -1.82, laY: -2.42, laZ: 3.14,
      raX: -1.82, raY: 2.62, raZ: -3.14,
      lfX: 1.10, lfY: 0.00, lfZ: -0.20,
      rfX: 1.12, rfY: 0.00, rfZ: 0.14,
      lhX: -0.10, lhY: 1.66, lhZ: 0.26,
      rhX: -0.18, rhY: -1.66, rhZ: -0.26,
    };

    // Raf handle
    this._rafId = null;
    this._lastFrameTime = performance.now();

    // ResizeObserver for automatic container resize
    this._resizeObserver = new ResizeObserver(() => this._onResize());
    this._resizeObserver.observe(this.container);

    this.init(modelUrl);
  }

  init(modelUrl) {
    const rect = this.container.getBoundingClientRect();

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(40, rect.width / rect.height, 0.05, 50);
    this.camera.position.set(0, 0, 1.25);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    this.renderer.setSize(rect.width, rect.height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;

    this.container.innerHTML = '';
    this.container.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.enableZoom = true;
    this.controls.minDistance = 0.2;
    this.controls.maxDistance = 3;
    this.controls.enablePan = true;
    this.controls.minAzimuthAngle = -Math.PI / 3;
    this.controls.maxAzimuthAngle = Math.PI / 3;
    this.controls.minPolarAngle = Math.PI / 3;
    this.controls.maxPolarAngle = Math.PI / 1.6;

    const pmremGenerator = new THREE.PMREMGenerator(this.renderer);
    pmremGenerator.compileEquirectangularShader();
    const envTexture = pmremGenerator.fromScene(new RoomEnvironment(this.renderer), 0.04).texture;
    this.scene.environment = envTexture;
    pmremGenerator.dispose();

    const ambientLight = new THREE.AmbientLight(0xffffff, 0.35);
    this.scene.add(ambientLight);

    this.keyLight = new THREE.DirectionalLight(0xfffae6, 1.25);
    this.keyLight.position.set(1.5, 2, 2);
    this.scene.add(this.keyLight);

    const fillLight = new THREE.DirectionalLight(0xe6f0ff, 0.5);
    fillLight.position.set(-1.5, 1, 1.5);
    this.scene.add(fillLight);

    const rimLight = new THREE.DirectionalLight(0xffffff, 1.5);
    rimLight.position.set(0, 3, -2.5);
    this.scene.add(rimLight);

    this.loadGLBModel(modelUrl);
    window.addEventListener('resize', () => this._onResize());

    this._rafId = requestAnimationFrame((t) => this._renderLoop(t));
  }

  // ── Public API ──────────────────────────────────────────────────────────

  syncAudio(audioContext, audioStartTime) {
    this._audioContext = audioContext;
    this._audioStartTime = audioStartTime;
  }

  setAnimationMatrix(matrix) {
    this.currentAnimationMatrix = matrix || [];
    this.activeTargetWeights = {};
    this.animationStartTime = performance.now() / 1000;
    this.isSpeaking = this.currentAnimationMatrix.length > 0;
  }

  clearAnimation() {
    this.currentAnimationMatrix = null;
    this.activeTargetWeights = {};
    this.isSpeaking = false;
    this._audioContext = null;
    this._audioStartTime = null;
    this.morphMeshes.forEach((mesh) => {
      if (mesh.morphTargetInfluences) {
        for (let i = 0; i < mesh.morphTargetInfluences.length; i++) {
          mesh.morphTargetInfluences[i] = 0.0;
        }
      }
    });
  }

  setEmotion(emotion) {
    if (!this.emotionTargets[emotion]) return;
    this.currentEmotion = emotion;
    // Force immediate morph application so there's no 1-frame lag
    const morphs = this.emotionTargets[emotion] || {};
    const allShapes = new Set(Object.values(this.emotionTargets).flatMap(m => Object.keys(m)));
    allShapes.forEach(shape => this._setMorphTarget(shape, 0.0));
    Object.entries(morphs).forEach(([shape, val]) => this._setMorphTarget(shape, val));
  }

  dispose() {
    if (this._rafId) cancelAnimationFrame(this._rafId);
    if (this._resizeObserver) this._resizeObserver.disconnect();
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
    }
    if (this.controls) this.controls.dispose();
  }

  resize() {
    this._onResize();
  }

  // ── Model loading ───────────────────────────────────────────────────────

  loadGLBModel(url) {
    const loader = new GLTFLoader();
    this.isLoaded = false;
    this.morphMeshes = [];
    this.leftEye = this.rightEye = this.headBone = this.neckBone = null;
    this.leftArm = this.rightArm = this.leftForeArm = this.rightForeArm = null;
    this.leftHand = this.rightHand = null;
    this.initialLeftArmRot = this.initialRightArmRot = null;
    this.initialLeftForeArmRot = this.initialRightForeArmRot = null;
    this.initialLeftHandRot = this.initialRightHandRot = null;

    if (this.avatarScene) {
      this.scene.remove(this.avatarScene);
    }

    loader.load(
      url,
      (gltf) => {
        this.avatarScene = gltf.scene;
        this.scene.add(this.avatarScene);

        this.avatarScene.traverse((node) => {
          const name = node.name.toLowerCase();
          if (name.includes('lefteye') || name === 'eye_l' || name === 'eyel') this.leftEye = node;
          else if (name.includes('righteye') || name === 'eye_r' || name === 'eyer') this.rightEye = node;
          else if (name === 'head' || name.endsWith('head')) this.headBone = node;
          else if (name === 'neck' || name.endsWith('neck')) this.neckBone = node;
          else if (name === 'leftarm' || name.endsWith('leftarm')) this.leftArm = node;
          else if (name === 'rightarm' || name.endsWith('rightarm')) this.rightArm = node;
          else if (name === 'leftforearm' || name.endsWith('leftforearm')) this.leftForeArm = node;
          else if (name === 'rightforearm' || name.endsWith('rightforearm')) this.rightForeArm = node;
          else if (name === 'lefthand' || name.endsWith('lefthand')) this.leftHand = node;
          else if (name === 'righthand' || name.endsWith('righthand')) this.rightHand = node;

          if (node.isMesh) {
            node.castShadow = true;
            node.receiveShadow = true;
            if (node.morphTargetDictionary && node.morphTargetInfluences) {
              this.morphMeshes.push(node);
            }
          }
        });

        this._relaxArms();
        this._focusOnHead();
        this.isLoaded = true;
        if (this.onReady) this.onReady(this);
      },
      undefined,
      (error) => console.error('[AvatarWidget] GLB load error:', error)
    );
  }

  // ── Render loop ──────────────────────────────────────────────────────────

  _renderLoop(now) {
    this._rafId = requestAnimationFrame((t) => this._renderLoop(t));

    const dt = (now - this._lastFrameTime) / 1000;
    this._lastFrameTime = now;

    if (!this.isLoaded) return;

    // Update idle procedural behavior (breathing, emotion body, blink, gaze)
    this._updateBehavior(dt);

    // Head rotation
    this._setHeadRotation(this.rotation.x, this.rotation.y, this.rotation.z);

    // Arm sways (modulated by emotion breathing time)
    this._updateArmSways(this.breathingTime);

    // Eye gaze + blink
    this._setGaze(this.gazeOffset.x * 20.0, this.gazeOffset.y * 20.0);
    this._setBlink(this.blinkVal);

    // Server-driven animation matrix (lip-sync)
    if (this.isSpeaking) {
      let elapsed;
      if (this._audioContext && this._audioStartTime !== undefined && this._audioStartTime !== null) {
        const latency = this._audioContext.outputLatency || 0.08;
        elapsed = this._audioContext.currentTime - this._audioStartTime - latency;
      } else {
        elapsed = performance.now() / 1000 - this.animationStartTime;
      }
      this._updateFromMatrix(elapsed);
      if (Object.keys(this.activeTargetWeights).length > 0) {
        this.morphMeshes.forEach((child) => {
          const dict = child.morphTargetDictionary;
          const inf = child.morphTargetInfluences;
          for (const shapeName in this.activeTargetWeights) {
            let idx = dict[shapeName];
            if (idx === undefined) {
              const lower = shapeName.toLowerCase();
              for (const k in dict) {
                if (k.toLowerCase() === lower || k.toLowerCase().endsWith('.' + lower)) {
                  idx = dict[k]; break;
                }
              }
            }
            if (idx === undefined && shapeName === 'jawOpen' && dict['mouthOpen'] !== undefined) idx = dict['mouthOpen'];
            else if (idx === undefined && shapeName === 'mouthOpen' && dict['jawOpen'] !== undefined) idx = dict['jawOpen'];
            if (idx !== undefined) {
              inf[idx] = Math.max(0, Math.min(1, this.activeTargetWeights[shapeName]));
            }
          }
        });
      }
    }

    // Emotion morph targets (idle only — server matrix already bakes emotion)
    if (!this.isSpeaking && this.emotionWeights) {
      Object.keys(this.emotionWeights).forEach((morph) => {
        this._setMorphTarget(morph, this.emotionWeights[morph]);
      });
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  // ── Behavior (idle procedural animation) ─────────────────────────────────

  _updateBehavior(dt) {
    // ── Interpolate body params toward current emotion ─────────────────────
    const targetBody = this._emotionBody[this.currentEmotion] || this._emotionBody.neutral;
    const bodyLerp = 1 - Math.exp(-2 * dt);
    for (const key of Object.keys(this._bodyParams)) {
      this._bodyParams[key] += (targetBody[key] - this._bodyParams[key]) * bodyLerp;
    }
    this.breathingSpeed    = this._bodyParams.breathSpeed;
    this.breathingAmplitude = this._bodyParams.breathAmp;

    // ── Interpolate emotion blendshape weights ──────────────────────────────
    const targetWeights = this.emotionTargets[this.currentEmotion] || {};
    const emotionLerp = 1 - Math.exp(-5.0 * dt);
    Object.keys(this.emotionWeights).forEach(key => {
      const t = targetWeights[key] || 0.0;
      this.emotionWeights[key] += (t - this.emotionWeights[key]) * emotionLerp;
    });

    // ── Breathing ───────────────────────────────────────────────────────────
    this.breathingTime += dt;
    const cycle = Math.sin(this.breathingTime * this.breathingSpeed);
    const swayAmp = this._bodyParams.swayAmp;
    const biasY   = this._bodyParams.headBiasY;

    this.rotation.x = cycle * this.breathingAmplitude;
    this.rotation.y = Math.cos(this.breathingTime * this.breathingSpeed * 0.5) * swayAmp * 0.3 + biasY;
    this.rotation.z = Math.sin(this.breathingTime * this.breathingSpeed * 0.3) * this.breathingAmplitude * 0.15;

    // Blink
    this._updateBlink(dt);

    // Gaze saccades
    this._updateSaccades(dt);
  }

  _updateBlink(dt) {
    this.blinkTimer += dt * 1000;
    switch (this.blinkState) {
      case 'idle':
        this.blinkVal = 0;
        if (this.blinkTimer >= this.nextBlinkInterval) {
          this.blinkState = 'closing';
          this.blinkStateTime = 0;
        }
        break;
      case 'closing':
        this.blinkStateTime += dt;
        this.blinkVal = Math.min(this.blinkStateTime / this.blinkDuration, 1.0);
        if (this.blinkVal >= 1.0) { this.blinkState = 'opening'; this.blinkStateTime = 0; }
        break;
      case 'opening':
        this.blinkStateTime += dt;
        this.blinkVal = Math.max(1.0 - (this.blinkStateTime / this.blinkDuration), 0.0);
        if (this.blinkVal <= 0.0) {
          if (Math.random() < 0.15) { this.blinkState = 'waiting-double'; this.blinkStateTime = 0; }
          else { this._resetBlink(); }
        }
        break;
      case 'waiting-double':
        this.blinkVal = 0;
        this.blinkStateTime += dt;
        if (this.blinkStateTime >= 0.12) { this.blinkState = 'double-closing'; this.blinkStateTime = 0; }
        break;
      case 'double-closing':
        this.blinkStateTime += dt;
        this.blinkVal = Math.min(this.blinkStateTime / this.blinkDuration, 1.0);
        if (this.blinkVal >= 1.0) { this.blinkState = 'double-opening'; this.blinkStateTime = 0; }
        break;
      case 'double-opening':
        this.blinkStateTime += dt;
        this.blinkVal = Math.max(1.0 - (this.blinkStateTime / this.blinkDuration), 0.0);
        if (this.blinkVal <= 0.0) { this._resetBlink(); }
        break;
    }
  }

  _resetBlink() {
    this.blinkState = 'idle';
    this.blinkTimer = 0;
    this.nextBlinkInterval = 3000 + Math.random() * 3000;
  }

  _updateSaccades(dt) {
    this.gazeTimer += dt * 1000;
    if (this.gazeTimer >= this.nextGazeInterval) {
      if (Math.random() < 0.70) {
        this.gazeTarget.x = (Math.random() - 0.5) * 0.012;
        this.gazeTarget.y = (Math.random() - 0.5) * 0.006;
      } else {
        this.gazeTarget.x = 0;
        this.gazeTarget.y = 0;
      }
      this.gazeTimer = 0;
      this.nextGazeInterval = 1000 + Math.random() * 2500;
    }
    const f = 1 - Math.exp(-20 * dt);
    this.gazeOffset.x += (this.gazeTarget.x - this.gazeOffset.x) * f;
    this.gazeOffset.y += (this.gazeTarget.y - this.gazeOffset.y) * f;
  }

  // ── Mesh and bone helpers ────────────────────────────────────────────────

  _setMorphTarget(name, value) {
    this.morphMeshes.forEach((mesh) => {
      let idx = mesh.morphTargetDictionary[name];
      if (idx === undefined) {
        const lower = name.toLowerCase();
        for (const k in mesh.morphTargetDictionary) {
          const lk = k.toLowerCase();
          if (lk === lower || lk.endsWith('.' + lower)) { idx = mesh.morphTargetDictionary[k]; break; }
        }
      }
      if (idx === undefined && name === 'jawOpen' && mesh.morphTargetDictionary['mouthOpen'] !== undefined) idx = mesh.morphTargetDictionary['mouthOpen'];
      else if (idx === undefined && name === 'mouthOpen' && mesh.morphTargetDictionary['jawOpen'] !== undefined) idx = mesh.morphTargetDictionary['jawOpen'];
      if (idx !== undefined) mesh.morphTargetInfluences[idx] = value;
    });
  }

  _setGaze(x, y) {
    const maxAngle = 0.22;
    if (this.leftEye && this.rightEye) {
      this.leftEye.rotation.y = x * maxAngle;
      this.leftEye.rotation.x = y * maxAngle;
      this.rightEye.rotation.y = x * maxAngle;
      this.rightEye.rotation.x = y * maxAngle;
    }
    if (x < 0) {
      this._setMorphTarget('eyeLookOutLeft', -x);
      this._setMorphTarget('eyeLookInRight', -x);
      this._setMorphTarget('eyeLookInLeft', 0);
      this._setMorphTarget('eyeLookOutRight', 0);
    } else {
      this._setMorphTarget('eyeLookInLeft', x);
      this._setMorphTarget('eyeLookOutRight', x);
      this._setMorphTarget('eyeLookOutLeft', 0);
      this._setMorphTarget('eyeLookInRight', 0);
    }
    if (y < 0) {
      this._setMorphTarget('eyeLookDownLeft', -y);
      this._setMorphTarget('eyeLookDownRight', -y);
      this._setMorphTarget('eyeLookUpLeft', 0);
      this._setMorphTarget('eyeLookUpRight', 0);
    } else {
      this._setMorphTarget('eyeLookUpLeft', y);
      this._setMorphTarget('eyeLookUpRight', y);
      this._setMorphTarget('eyeLookDownLeft', 0);
      this._setMorphTarget('eyeLookDownRight', 0);
    }
  }

  _setBlink(val) {
    this._setMorphTarget('eyeBlinkLeft', val);
    this._setMorphTarget('eyeBlinkRight', val);
  }

  _setHeadRotation(rx, ry, rz) {
    if (this.headBone) {
      this.headBone.rotation.x = rx;
      this.headBone.rotation.y = ry;
      this.headBone.rotation.z = rz;
    } else if (this.neckBone) {
      this.neckBone.rotation.x = rx;
      this.neckBone.rotation.y = ry;
      this.neckBone.rotation.z = rz;
    } else if (this.avatarScene) {
      this.avatarScene.rotation.x = rx;
      this.avatarScene.rotation.y = ry;
      this.avatarScene.rotation.z = rz;
    }
  }

  _updateArmSways(time) {
    if (!this.leftArm || !this.rightArm || !this.leftForeArm || !this.rightForeArm) return;
    const cal = this.calibration;
    const swayZ = Math.sin(time * 1.1) * 0.02;
    const swayX = Math.cos(time * 0.95) * 0.015;
    if (this.initialLeftArmRot) {
      this.leftArm.rotation.x = this.initialLeftArmRot.x + cal.laX + swayX;
      this.leftArm.rotation.y = this.initialLeftArmRot.y + cal.laY;
      this.leftArm.rotation.z = this.initialLeftArmRot.z + cal.laZ + swayZ;
    }
    if (this.initialRightArmRot) {
      this.rightArm.rotation.x = this.initialRightArmRot.x + cal.raX + swayX;
      this.rightArm.rotation.y = this.initialRightArmRot.y + cal.raY;
      this.rightArm.rotation.z = this.initialRightArmRot.z + cal.raZ + swayZ;
    }
    if (this.initialLeftForeArmRot) {
      this.leftForeArm.rotation.x = this.initialLeftForeArmRot.x + cal.lfX;
      this.leftForeArm.rotation.y = this.initialLeftForeArmRot.y + cal.lfY;
      this.leftForeArm.rotation.z = this.initialLeftForeArmRot.z + cal.lfZ + swayZ;
    }
    if (this.initialRightForeArmRot) {
      this.rightForeArm.rotation.x = this.initialRightForeArmRot.x + cal.rfX;
      this.rightForeArm.rotation.y = this.initialRightForeArmRot.y + cal.rfY;
      this.rightForeArm.rotation.z = this.initialRightForeArmRot.z + cal.rfZ + swayZ;
    }
    if (this.leftHand && this.initialLeftHandRot) {
      this.leftHand.rotation.x = this.initialLeftHandRot.x + cal.lhX;
      this.leftHand.rotation.y = this.initialLeftHandRot.y + cal.lhY;
      this.leftHand.rotation.z = this.initialLeftHandRot.z + cal.lhZ;
    }
    if (this.rightHand && this.initialRightHandRot) {
      this.rightHand.rotation.x = this.initialRightHandRot.x + cal.rhX;
      this.rightHand.rotation.y = this.initialRightHandRot.y + cal.rhY;
      this.rightHand.rotation.z = this.initialRightHandRot.z + cal.rhZ;
    }
  }

  _updateFromMatrix(elapsed) {
    const mat = this.currentAnimationMatrix;
    if (!mat || mat.length === 0) return;
    const lastFrame = mat[mat.length - 1];
    if (elapsed > lastFrame.time + 0.12) {
      this.clearAnimation();
      return;
    }
    const t = Math.max(0, Math.min(elapsed, lastFrame.time));
    let lo = 0, hi = mat.length - 1;
    while (lo < hi - 1) { const mid = (lo + hi) >> 1; if (mat[mid].time <= t) lo = mid; else hi = mid; }
    const frameA = mat[lo];
    const frameB = mat[lo + 1] || frameA;
    const dt = frameB.time - frameA.time;
    const alpha = dt > 0.0001 ? Math.min((t - frameA.time) / dt, 1.0) : 0.0;
    const weights = {};
    const bsA = frameA.blendshapes;
    const bsB = frameB.blendshapes;
    for (const name in bsA) {
      const a = bsA[name] || 0.0;
      const b = (bsB[name] !== undefined ? bsB[name] : a);
      weights[name] = a + (b - a) * alpha;
    }
    this.activeTargetWeights = weights;
  }

  _relaxArms() {
    const save = (n) => n ? n.rotation.clone() : null;
    this.initialLeftArmRot = save(this.leftArm);
    this.initialRightArmRot = save(this.rightArm);
    this.initialLeftForeArmRot = save(this.leftForeArm);
    this.initialRightForeArmRot = save(this.rightForeArm);
    this.initialLeftHandRot = save(this.leftHand);
    this.initialRightHandRot = save(this.rightHand);
  }

  _focusOnHead() {
    if (!this.avatarScene) return;
    let headCenter = new THREE.Vector3(0, 1.45, 0), found = false;
    this.avatarScene.traverse((node) => {
      if (found || !node.isMesh) return;
      if (node.name.toLowerCase().includes('head') || node.name.toLowerCase().includes('face')) {
        new THREE.Box3().setFromObject(node).getCenter(headCenter);
        found = true;
      }
    });
    if (!found && this.headBone) { this.headBone.getWorldPosition(headCenter); found = true; }
    if (!found) {
      const box = new THREE.Box3().setFromObject(this.avatarScene);
      headCenter.set(0, box.min.y + (box.max.y - box.min.y) * 0.85, 0);
    }
    this.controls.target.copy(headCenter);
    this.camera.position.set(headCenter.x, headCenter.y + 0.05, headCenter.z + 1.25);
    this.camera.lookAt(headCenter);
    this.controls.update();
  }

  _onResize() {
    if (!this.container || !this.renderer) return;
    const rect = this.container.getBoundingClientRect();
    this.camera.aspect = rect.width / rect.height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(rect.width, rect.height);
  }
}
