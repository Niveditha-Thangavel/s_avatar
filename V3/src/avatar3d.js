import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

export class Avatar3D {
  constructor(containerId, modelUrl) {
    this.container = document.getElementById(containerId);
    this.modelUrl = modelUrl;

    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;
    this.avatarScene = null;

    this.morphMeshes = [];
    this.leftEye = null;
    this.rightEye = null;
    this.headBone = null;
    this.neckBone = null;

    this.isLoaded = false;

    // Server-driven animation matrix
    this.currentAnimationMatrix = null;
    this.activeTargetWeights = {};
    this.animationStartTime = 0;
    this.isSpeaking = false;

    this.init();
  }

  init() {
    const rect = this.container.getBoundingClientRect();

    this.scene = new THREE.Scene();

    this.camera = new THREE.PerspectiveCamera(40, rect.width / rect.height, 0.05, 50);
    this.camera.position.set(0, 0, 1.25);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    this.renderer.setSize(rect.width, rect.height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;

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
    this.keyLight.castShadow = true;
    this.keyLight.shadow.mapSize.width = 1024;
    this.keyLight.shadow.mapSize.height = 1024;
    this.keyLight.shadow.bias = -0.0005;
    this.scene.add(this.keyLight);

    const fillLight = new THREE.DirectionalLight(0xe6f0ff, 0.5);
    fillLight.position.set(-1.5, 1, 1.5);
    this.scene.add(fillLight);

    const rimLight = new THREE.DirectionalLight(0xffffff, 1.5);
    rimLight.position.set(0, 3, -2.5);
    this.scene.add(rimLight);

    this.loadGLBModel(this.modelUrl);

    window.addEventListener('resize', this.onResize.bind(this));
  }

  /**
   * Set the server-driven animation matrix.
   * The render loop drives timing via the AudioContext clock (elapsed seconds
   * since audio.start()). animationStartTime is only used as a fallback when
   * the AudioContext elapsed time is not available.
   * @param {{time:number, blendshapes:object}[]} matrix - frames at 30fps
   */
  setAnimationMatrix(matrix) {
    this.currentAnimationMatrix = matrix || [];
    this.activeTargetWeights = {};
    this.animationStartTime = performance.now() / 1000;  // fallback only
    this.isSpeaking = this.currentAnimationMatrix.length > 0;
  }

  clearAnimation() {
    this.currentAnimationMatrix = null;
    this.activeTargetWeights = {};
    this.isSpeaking = false;

    // Reset ALL morph target influences to zero so no shape is frozen
    // on the face after speech ends (prevents puckered-lip resting pose).
    // The BehaviorManager will smoothly ramp emotion weights back via lerp.
    this.morphMeshes.forEach((mesh) => {
      if (mesh.morphTargetInfluences) {
        for (let i = 0; i < mesh.morphTargetInfluences.length; i++) {
          mesh.morphTargetInfluences[i] = 0.0;
        }
      }
    });
  }

  /**
   * Set the current emotion — no-op, kept for API compatibility.
   * @param {string} emotion
   */
  setEmotion(emotion) {
    // colour changing removed
  }

  loadGLBModel(url) {
    const loader = new GLTFLoader();
    this.isLoaded = false;
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

          if (name.includes('lefteye') || name === 'eye_l' || name === 'eyel' || name.endsWith('lefteye')) {
            this.leftEye = node;
          } else if (name.includes('righteye') || name === 'eye_r' || name === 'eyer' || name.endsWith('righteye')) {
            this.rightEye = node;
          } else if (name === 'head' || name.endsWith('head')) {
            this.headBone = node;
          } else if (name === 'neck' || name.endsWith('neck')) {
            this.neckBone = node;
          } else if (name === 'leftarm' || name.endsWith('leftarm') || (name.includes('leftarm') && !name.includes('forearm') && !name.includes('shoulder'))) {
            this.leftArm = node;
          } else if (name === 'rightarm' || name.endsWith('rightarm') || (name.includes('rightarm') && !name.includes('forearm') && !name.includes('shoulder'))) {
            this.rightArm = node;
          } else if (name === 'leftforearm' || name.endsWith('leftforearm')) {
            this.leftForeArm = node;
          } else if (name === 'rightforearm' || name.endsWith('rightforearm')) {
            this.rightForeArm = node;
          } else if (name === 'lefthand' || name.endsWith('lefthand')) {
            this.leftHand = node;
          } else if (name === 'righthand' || name.endsWith('righthand')) {
            this.rightHand = node;
          }

          if (node.isMesh) {
            node.castShadow = true;
            node.receiveShadow = true;

            if (node.material) {
              node.material.roughness = Math.max(node.material.roughness, 0.45);
              node.material.metalness = Math.min(node.material.metalness, 0.1);
            }

            if (node.morphTargetDictionary && node.morphTargetInfluences) {
              this.morphMeshes.push(node);
            }
          }
        });

        this.relaxArms();
        this.focusOnHead();

        this.isLoaded = true;

        window.dispatchEvent(new CustomEvent('avatar-loaded'));
      },
      (xhr) => {
        if (xhr.lengthComputable) {
          const percent = (xhr.loaded / xhr.total) * 100;
          window.dispatchEvent(new CustomEvent('avatar-loading-progress', { detail: percent }));
        }
      },
      (error) => {
        console.error('[Avatar3D] Error loading GLB model:', error);
      }
    );
  }

  focusOnHead() {
    if (!this.avatarScene) return;

    let headCenter = new THREE.Vector3(0, 1.45, 0);
    let headFound = false;

    this.avatarScene.traverse((node) => {
      if (headFound) return;
      if (node.isMesh && (node.name.toLowerCase().includes('head') || node.name.toLowerCase().includes('face'))) {
        const box = new THREE.Box3().setFromObject(node);
        box.getCenter(headCenter);
        headFound = true;
      }
    });

    if (!headFound && this.headBone) {
      this.headBone.getWorldPosition(headCenter);
      headFound = true;
    }

    if (!headFound) {
      const box = new THREE.Box3().setFromObject(this.avatarScene);
      const min = box.min.y;
      const max = box.max.y;
      headCenter.set(0, min + (max - min) * 0.85, 0);
    }

    this.controls.target.copy(headCenter);
    this.camera.position.set(headCenter.x, headCenter.y + 0.05, headCenter.z + 1.25);
    this.camera.lookAt(headCenter);
    this.controls.update();
  }

  relaxArms() {
    this.initialLeftArmRot = this.leftArm ? this.leftArm.rotation.clone() : null;
    this.initialRightArmRot = this.rightArm ? this.rightArm.rotation.clone() : null;
    this.initialLeftForeArmRot = this.leftForeArm ? this.leftForeArm.rotation.clone() : null;
    this.initialRightForeArmRot = this.rightForeArm ? this.rightForeArm.rotation.clone() : null;
    this.initialLeftHandRot = this.leftHand ? this.leftHand.rotation.clone() : null;
    this.initialRightHandRot = this.rightHand ? this.rightHand.rotation.clone() : null;
  }

  updateArmSways(time) {
    if (!this.leftArm || !this.rightArm || !this.leftForeArm || !this.rightForeArm) return;

    const cal = window.avatarCalibration || {
      laX: -1.82, laY: -2.42, laZ: 3.14,
      raX: -1.82, raY: 2.62, raZ: -3.14,
      lfX: 1.10, lfY: 0.00, lfZ: -0.20,
      rfX: 1.12, rfY: 0.00, rfZ: 0.14,
      lhX: -0.10, lhY: 1.66, lhZ: 0.26,
      rhX: -0.18, rhY: -1.66, rhZ: -0.26
    };

    const swayZ = Math.sin(time * 1.1) * 0.02;
    const swayX = Math.cos(time * 0.95) * 0.015;

    this.leftArm.rotation.x = this.initialLeftArmRot.x + cal.laX + swayX;
    this.leftArm.rotation.y = this.initialLeftArmRot.y + cal.laY;
    this.leftArm.rotation.z = this.initialLeftArmRot.z + cal.laZ + swayZ;

    this.rightArm.rotation.x = this.initialRightArmRot.x + cal.raX + swayX;
    this.rightArm.rotation.y = this.initialRightArmRot.y + cal.raY;
    this.rightArm.rotation.z = this.initialRightArmRot.z + cal.raZ + swayZ;

    this.leftForeArm.rotation.x = this.initialLeftForeArmRot.x + cal.lfX;
    this.leftForeArm.rotation.y = this.initialLeftForeArmRot.y + cal.lfY;
    this.leftForeArm.rotation.z = this.initialLeftForeArmRot.z + cal.lfZ + swayZ;

    this.rightForeArm.rotation.x = this.initialRightForeArmRot.x + cal.rfX;
    this.rightForeArm.rotation.y = this.initialRightForeArmRot.y + cal.rfY;
    this.rightForeArm.rotation.z = this.initialRightForeArmRot.z + cal.rfZ + swayZ;

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

  setMorphTarget(name, value) {
    this.morphMeshes.forEach((mesh) => {
      let index = mesh.morphTargetDictionary[name];

      if (index === undefined) {
        const lowerName = name.toLowerCase();
        const foundKey = Object.keys(mesh.morphTargetDictionary).find((k) => {
          const lk = k.toLowerCase();
          return lk === lowerName || lk.endsWith('.' + lowerName);
        });
        if (foundKey !== undefined) {
          index = mesh.morphTargetDictionary[foundKey];
        }
      }

      if (index === undefined) {
        if (name === 'jawOpen' && mesh.morphTargetDictionary['mouthOpen'] !== undefined) {
          index = mesh.morphTargetDictionary['mouthOpen'];
        } else if (name === 'mouthOpen' && mesh.morphTargetDictionary['jawOpen'] !== undefined) {
          index = mesh.morphTargetDictionary['jawOpen'];
        }
      }

      if (index !== undefined) {
        mesh.morphTargetInfluences[index] = value;
      }
    });
  }

  setGaze(x, y) {
    const maxAngle = 0.22;

    if (this.leftEye && this.rightEye) {
      this.leftEye.rotation.y = x * maxAngle;
      this.leftEye.rotation.x = y * maxAngle;
      this.rightEye.rotation.y = x * maxAngle;
      this.rightEye.rotation.x = y * maxAngle;
    }

    if (x < 0) {
      this.setMorphTarget('eyeLookOutLeft', -x);
      this.setMorphTarget('eyeLookInRight', -x);
      this.setMorphTarget('eyeLookInLeft', 0);
      this.setMorphTarget('eyeLookOutRight', 0);
    } else {
      this.setMorphTarget('eyeLookInLeft', x);
      this.setMorphTarget('eyeLookOutRight', x);
      this.setMorphTarget('eyeLookOutLeft', 0);
      this.setMorphTarget('eyeLookInRight', 0);
    }

    if (y < 0) {
      this.setMorphTarget('eyeLookDownLeft', -y);
      this.setMorphTarget('eyeLookDownRight', -y);
      this.setMorphTarget('eyeLookUpLeft', 0);
      this.setMorphTarget('eyeLookUpRight', 0);
    } else {
      this.setMorphTarget('eyeLookUpLeft', y);
      this.setMorphTarget('eyeLookUpRight', y);
      this.setMorphTarget('eyeLookDownLeft', 0);
      this.setMorphTarget('eyeLookDownRight', 0);
    }
  }

  setBlink(blinkVal) {
    this.setMorphTarget('eyeBlinkLeft', blinkVal);
    this.setMorphTarget('eyeBlinkRight', blinkVal);
  }

  setHeadRotation(rx, ry, rz) {
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

  onResize() {
    if (!this.container || !this.renderer) return;
    const rect = this.container.getBoundingClientRect();
    this.camera.aspect = rect.width / rect.height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(rect.width, rect.height);
  }

  /**
   * Update blendshape weights from the server animation matrix.
   *
   * Uses binary search to find the surrounding frames, then linearly interpolates
   * between them for smooth 60Hz/120Hz upsampling of the 30FPS matrix.
   *
   * @param {number} elapsed - seconds since audio playback started (AudioContext clock)
   */
  _updateFromMatrix(elapsed) {
    const mat = this.currentAnimationMatrix;
    if (!mat || mat.length === 0) return;

    const lastFrame = mat[mat.length - 1];

    // Animation finished — clear and return
    if (elapsed > lastFrame.time + 0.12) {
      this.clearAnimation();
      return;
    }

    // Clamp elapsed to valid range
    const t = Math.max(0, Math.min(elapsed, lastFrame.time));

    // Binary search for the floor frame index
    let lo = 0, hi = mat.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (mat[mid].time <= t) lo = mid; else hi = mid;
    }

    const frameA = mat[lo];
    const frameB = mat[lo + 1] || frameA;   // guard: past last frame

    // Interpolation factor in [0, 1]
    const dt = frameB.time - frameA.time;
    const alpha = dt > 0.0001 ? Math.min((t - frameA.time) / dt, 1.0) : 0.0;

    // Build interpolated target weights
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

  render(dt, behavior, elapsed) {
    if (!this.isLoaded) return;

    // ── 1. Head rotation (idle breathing from behavior) ──
    this.setHeadRotation(behavior.rotation.x, behavior.rotation.y, behavior.rotation.z);

    // ── 2. Arm sways ──
    this.updateArmSways(behavior.breathingTime);

    // ── 3. Eye gaze + blink (procedural idle) ──
    this.setGaze(behavior.gazeOffset.x * 20.0, behavior.gazeOffset.y * 20.0);
    this.setBlink(behavior.blinkVal);

    // ── 5. Server-driven blendshape matrix (animation + emotion) ──
    if (this.isSpeaking) {
      // elapsed is AudioContext.currentTime − audioStartTime (precise, monotonic).
      // Fall back to performance clock when audio context isn't available.
      const animElapsed = (elapsed !== null && elapsed !== undefined)
        ? elapsed
        : (performance.now() / 1000 - this.animationStartTime);

      this._updateFromMatrix(animElapsed);

      if (this.isSpeaking && Object.keys(this.activeTargetWeights).length > 0) {
        // Apply interpolated weights directly — NO extra lerp here.
        // The matrix was already smoothed by PantoMatrix; adding another lerp
        // introduces lag that breaks the audio↔mouth sync.
        this.morphMeshes.forEach((child) => {
          const dict = child.morphTargetDictionary;
          const inf  = child.morphTargetInfluences;
          for (const shapeName in this.activeTargetWeights) {
            // Direct name match
            let idx = dict[shapeName];

            // Case-insensitive / dot-suffix fallback
            if (idx === undefined) {
              const lower = shapeName.toLowerCase();
              for (const k in dict) {
                const lk = k.toLowerCase();
                if (lk === lower || lk.endsWith('.' + lower)) { idx = dict[k]; break; }
              }
            }

            // jawOpen ↔ mouthOpen alias
            if (idx === undefined) {
              if (shapeName === 'jawOpen' && dict['mouthOpen'] !== undefined) idx = dict['mouthOpen'];
              else if (shapeName === 'mouthOpen' && dict['jawOpen'] !== undefined) idx = dict['jawOpen'];
            }

            if (idx !== undefined) {
              inf[idx] = Math.max(0, Math.min(1, this.activeTargetWeights[shapeName]));
            }
          }
        });
      }
    }

    // ── 6. Emotion morph targets (idle only — server matrix already bakes emotion) ──
    if (!this.isSpeaking && behavior.emotionWeights) {
      Object.keys(behavior.emotionWeights).forEach((morph) => {
        this.setMorphTarget(morph, behavior.emotionWeights[morph]);
      });
    }

    // ── 7. Render ──
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
