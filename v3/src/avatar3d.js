import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';

export class Avatar3D {
  constructor(containerId, modelUrl) {
    this.container = document.getElementById(containerId);
    this.modelUrl = modelUrl;
    
    // Scene assets
    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;
    this.avatarScene = null; // Loaded GLB scene root
    
    // Rigging & Animation references
    this.morphMeshes = []; // Meshes containing morph targets (blendshapes)
    this.leftEye = null;
    this.rightEye = null;
    this.headBone = null;
    this.neckBone = null;
    
    // Loading State
    this.isLoaded = false;
    
    this.init();
  }

  init() {
    const rect = this.container.getBoundingClientRect();
    
    // 1. Create Scene & Camera
    this.scene = new THREE.Scene();
    
    this.camera = new THREE.PerspectiveCamera(40, rect.width / rect.height, 0.05, 50);
    // Position camera slightly offset to look at the head
    this.camera.position.set(0, 0, 1.25);
    
    // 2. WebGL Renderer
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
    this.renderer.setSize(rect.width, rect.height);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    
    this.container.innerHTML = '';
    this.container.appendChild(this.renderer.domElement);
    
    // 3. Orbit Controls
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.enableZoom = true;
    this.controls.minDistance = 0.2;
    this.controls.maxDistance = 3;
    this.controls.enablePan = true;
    // Limit rotation so the user stays generally front-facing
    this.controls.minAzimuthAngle = -Math.PI / 3;
    this.controls.maxAzimuthAngle = Math.PI / 3;
    this.controls.minPolarAngle = Math.PI / 3;
    this.controls.maxPolarAngle = Math.PI / 1.6;

    // 4. HDR Environment Map Generation (RoomEnvironment provides beautiful PBR skin/eye reflections)
    const pmremGenerator = new THREE.PMREMGenerator(this.renderer);
    pmremGenerator.compileEquirectangularShader();
    const envTexture = pmremGenerator.fromScene(new RoomEnvironment(this.renderer), 0.04).texture;
    this.scene.environment = envTexture;
    pmremGenerator.dispose();

    // 5. Studio Direct Lighting
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.35);
    this.scene.add(ambientLight);
    
    // Main key light
    this.keyLight = new THREE.DirectionalLight(0xfffae6, 1.25);
    this.keyLight.position.set(1.5, 2, 2);
    this.keyLight.castShadow = true;
    this.keyLight.shadow.mapSize.width = 1024;
    this.keyLight.shadow.mapSize.height = 1024;
    this.keyLight.shadow.bias = -0.0005;
    this.scene.add(this.keyLight);

    // Soft fill light
    const fillLight = new THREE.DirectionalLight(0xe6f0ff, 0.5);
    fillLight.position.set(-1.5, 1, 1.5);
    this.scene.add(fillLight);

    // Rim highlight light (outlines shoulders/hair)
    const rimLight = new THREE.DirectionalLight(0xffffff, 1.5);
    rimLight.position.set(0, 3, -2.5);
    this.scene.add(rimLight);

    // 6. Load GLB Model
    this.loadGLBModel(this.modelUrl);

    // 7. Handle Resize
    window.addEventListener('resize', this.onResize.bind(this));
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

    // Remove previous model if exists
    if (this.avatarScene) {
      this.scene.remove(this.avatarScene);
    }

    loader.load(
      url,
      (gltf) => {
        this.avatarScene = gltf.scene;
        this.scene.add(this.avatarScene);

        // Traverse model to locate morph target meshes and skeletal bones
        this.avatarScene.traverse((node) => {
          const name = node.name.toLowerCase();
          
          // Locate skeletal joints / eye nodes by name (robust suffix matching)
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
            
            // Adjust materials to be more realistic skin
            if (node.material) {
              node.material.roughness = Math.max(node.material.roughness, 0.45); // Make skin less glossy/plastic
              node.material.metalness = Math.min(node.material.metalness, 0.1); // Ensure skin is non-metallic
            }

            // Collect meshes that have morph targets (blendshapes)
            if (node.morphTargetDictionary && node.morphTargetInfluences) {
              this.morphMeshes.push(node);
            }
          }
        });

        console.log('[Avatar3D] Bone search result:', {
          leftEye: !!this.leftEye,
          rightEye: !!this.rightEye,
          headBone: !!this.headBone,
          neckBone: !!this.neckBone,
          leftArm: !!this.leftArm,
          rightArm: !!this.rightArm,
          leftForeArm: !!this.leftForeArm,
          rightForeArm: !!this.rightForeArm,
          leftHand: !!this.leftHand,
          rightHand: !!this.rightHand
        });

        // Relax arms from T-pose to natural A-pose hanging down
        this.relaxArms();

        // Frame the camera automatically on the avatar's head
        this.focusOnHead();
        
        this.isLoaded = true;
        console.log('[Avatar3D] Model loaded successfully! Morph meshes:', this.morphMeshes.length);
        
        // Dispatch load complete event for UI hooks
        window.dispatchEvent(new CustomEvent('avatar-loaded'));
      },
      (xhr) => {
        // Track GLB download progress
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

  /**
   * Automatically focuses camera and orbit controls target on the head of the 3D model
   */
  focusOnHead() {
    if (!this.avatarScene) return;

    // Find the head mesh or bone to get its center height
    let headCenter = new THREE.Vector3(0, 1.45, 0); // fallback default height for standard humanoid
    let headFound = false;

    // Try to find Head mesh or bone
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
      // Fallback: calculate overall bounding box and focus on the upper 15% (head region)
      const box = new THREE.Box3().setFromObject(this.avatarScene);
      const min = box.min.y;
      const max = box.max.y;
      headCenter.set(0, min + (max - min) * 0.85, 0); // Focus on upper head
    }

    // Set OrbitControls target directly to the head center
    this.controls.target.copy(headCenter);
    
    // Position camera in front of the head (zoomed out to 1.25 for chest-up/waist-up framing)
    this.camera.position.set(headCenter.x, headCenter.y + 0.05, headCenter.z + 1.25);
    this.camera.lookAt(headCenter);
    
    this.controls.update();
  }

  /**
   * Caches the original T-pose bone rotations of the arm/shoulder bones
   */
  relaxArms() {
    this.initialLeftArmRot = this.leftArm ? this.leftArm.rotation.clone() : null;
    this.initialRightArmRot = this.rightArm ? this.rightArm.rotation.clone() : null;
    this.initialLeftForeArmRot = this.leftForeArm ? this.leftForeArm.rotation.clone() : null;
    this.initialRightForeArmRot = this.rightForeArm ? this.rightForeArm.rotation.clone() : null;
    this.initialLeftHandRot = this.leftHand ? this.leftHand.rotation.clone() : null;
    this.initialRightHandRot = this.rightHand ? this.rightHand.rotation.clone() : null;
  }

  /**
   * Updates arm sways procedurally based on breathingTime (creates small, natural hanging arm movement)
   */
  updateArmSways(time, speechBobbing) {
    if (!this.leftArm || !this.rightArm || !this.leftForeArm || !this.rightForeArm) {
      console.warn('[Avatar3D] updateArmSways returning early because a bone is missing:', {
        leftArm: !!this.leftArm,
        rightArm: !!this.rightArm,
        leftForeArm: !!this.leftForeArm,
        rightForeArm: !!this.rightForeArm
      });
      return;
    }
    if (!this.initialLeftArmRot || !this.initialRightArmRot || !this.initialLeftForeArmRot || !this.initialRightForeArmRot) {
      console.warn('[Avatar3D] updateArmSways returning early because initial rotation is missing:', {
        initialLeftArmRot: !!this.initialLeftArmRot,
        initialRightArmRot: !!this.initialRightArmRot,
        initialLeftForeArmRot: !!this.initialLeftForeArmRot,
        initialRightForeArmRot: !!this.initialRightForeArmRot
      });
      return;
    }

    // 1. Base breathing sways
    const swayZ = Math.sin(time * 1.1) * 0.02;
    const swayX = Math.cos(time * 0.95) * 0.015;

    // 2. Speech gesture offsets (disabled - hands stay in clasped posture)
    const gestureScale = 0;
    const gestureLeftX = Math.sin(time * 4.5) * 0.15 * gestureScale;
    const gestureLeftY = Math.cos(time * 3.8) * 0.12 * gestureScale;
    const gestureLeftZ = Math.sin(time * 5.0) * 0.15 * gestureScale;

    const gestureRightX = Math.sin(time * 4.2 + 0.3) * -0.15 * gestureScale;
    const gestureRightY = Math.cos(time * 3.6 + 0.3) * -0.12 * gestureScale;
    const gestureRightZ = Math.sin(time * 5.1 + 0.3) * -0.15 * gestureScale;

    // 3. Pose upper arms (shoulders) - loading user's new calibrated values
    const cal = window.avatarCalibration || {
      laX: -1.82, laY: -2.42, laZ: 3.14,
      raX: -1.82, raY: 2.62, raZ: -3.14,
      lfX: 1.10, lfY: 0.00, lfZ: -0.20,
      rfX: 1.12, rfY: 0.00, rfZ: 0.14,
      lhX: -0.10, lhY: 1.66, lhZ: 0.26,
      rhX: -0.18, rhY: -1.66, rhZ: -0.26
    };

    // Left Arm (upper)
    this.leftArm.rotation.x = this.initialLeftArmRot.x + cal.laX + swayX + gestureLeftX;
    this.leftArm.rotation.y = this.initialLeftArmRot.y + cal.laY + gestureLeftY;
    this.leftArm.rotation.z = this.initialLeftArmRot.z + cal.laZ + swayZ + gestureLeftZ;

    // Right Arm (upper)
    this.rightArm.rotation.x = this.initialRightArmRot.x + cal.raX + swayX + gestureRightX;
    this.rightArm.rotation.y = this.initialRightArmRot.y + cal.raY + gestureRightY;
    this.rightArm.rotation.z = this.initialRightArmRot.z + cal.raZ + swayZ + gestureRightZ;

    // 4. Pose forearms (elbows)
    // Left Forearm
    this.leftForeArm.rotation.x = this.initialLeftForeArmRot.x + cal.lfX + gestureLeftX * 0.8;
    this.leftForeArm.rotation.y = this.initialLeftForeArmRot.y + cal.lfY + gestureLeftY * 0.8;
    this.leftForeArm.rotation.z = this.initialLeftForeArmRot.z + cal.lfZ + swayZ + gestureLeftZ * 0.8;

    // Right Forearm
    this.rightForeArm.rotation.x = this.initialRightForeArmRot.x + cal.rfX + gestureRightX * 0.8;
    this.rightForeArm.rotation.y = this.initialRightForeArmRot.y + cal.rfY + gestureRightY * 0.8;
    this.rightForeArm.rotation.z = this.initialRightForeArmRot.z + cal.rfZ + swayZ + gestureRightZ * 0.8;

    // 5. Pose hands (wrists) to allow palms direction adjustment + dynamic talking sways
    if (this.leftHand && this.initialLeftHandRot) {
      this.leftHand.rotation.x = this.initialLeftHandRot.x + cal.lhX;
      this.leftHand.rotation.y = this.initialLeftHandRot.y + cal.lhY + Math.sin(time * 5.0) * 0.18 * gestureScale;
      this.leftHand.rotation.z = this.initialLeftHandRot.z + cal.lhZ;
    }

    if (this.rightHand && this.initialRightHandRot) {
      this.rightHand.rotation.x = this.initialRightHandRot.x + cal.rhX;
      this.rightHand.rotation.y = this.initialRightHandRot.y + cal.rhY + Math.cos(time * 5.2) * -0.18 * gestureScale;
      this.rightHand.rotation.z = this.initialRightHandRot.z + cal.rhZ;
    }
  }

  /**
   * Sets morph target weights across all meshes by name
   */
  setMorphTarget(name, value) {
    this.morphMeshes.forEach((mesh) => {
      let index = mesh.morphTargetDictionary[name];

      // Suffix matching fallback (e.g. if morph target is prefixed like 'meshName.morphName')
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
      
      // Fallback for jawOpen <-> mouthOpen (cross-compatibility for different models)
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

  /**
   * Sets the eye gaze offset (horizontal/vertical)
   */
  setGaze(x, y) {
    const maxAngle = 0.22; // max angle in radians (~12.5 deg)
    
    // 1. Skeletal Bone eye rotation (Mixamo/Humanoid style)
    if (this.leftEye && this.rightEye) {
      this.leftEye.rotation.y = x * maxAngle;
      this.leftEye.rotation.x = y * maxAngle;
      
      this.rightEye.rotation.y = x * maxAngle;
      this.rightEye.rotation.x = y * maxAngle;
    }
    
    // 2. ARKit Morph Target eye look (for models using blendshapes for eyes)
    if (x < 0) {
      // Look Left
      this.setMorphTarget('eyeLookOutLeft', -x);
      this.setMorphTarget('eyeLookInRight', -x);
      this.setMorphTarget('eyeLookInLeft', 0);
      this.setMorphTarget('eyeLookOutRight', 0);
    } else {
      // Look Right
      this.setMorphTarget('eyeLookInLeft', x);
      this.setMorphTarget('eyeLookOutRight', x);
      this.setMorphTarget('eyeLookOutLeft', 0);
      this.setMorphTarget('eyeLookInRight', 0);
    }

    if (y < 0) {
      // Look Down
      this.setMorphTarget('eyeLookDownLeft', -y);
      this.setMorphTarget('eyeLookDownRight', -y);
      this.setMorphTarget('eyeLookUpLeft', 0);
      this.setMorphTarget('eyeLookUpRight', 0);
    } else {
      // Look Up
      this.setMorphTarget('eyeLookUpLeft', y);
      this.setMorphTarget('eyeLookUpRight', y);
      this.setMorphTarget('eyeLookDownLeft', 0);
      this.setMorphTarget('eyeLookDownRight', 0);
    }
  }

  /**
   * Sets eye blink value
   */
  setBlink(blinkVal) {
    this.setMorphTarget('eyeBlinkLeft', blinkVal);
    this.setMorphTarget('eyeBlinkRight', blinkVal);
  }

  /**
   * Rotates head/neck bones or scene root for idle sways and nodding
   */
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
      // Fallback: rotate the entire model root gently
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

  render(dt, behavior, lipsync) {
    if (!this.isLoaded) return;

    // 1. Apply Head Nodding and Breathing Rotations
    this.setHeadRotation(behavior.rotation.x, behavior.rotation.y, behavior.rotation.z);

    // 2. Apply procedural relaxed arm and hand sways (idle movements + gestures)
    this.updateArmSways(behavior.breathingTime, behavior.speechBobbing);

    // 3. Apply Eye Saccades (gazeOffset)
    this.setGaze(behavior.gazeOffset.x * 20.0, behavior.gazeOffset.y * 20.0); // Scale up gaze coordinates for visibility

    // 4. Apply Eyelid Blinking
    this.setBlink(behavior.blinkVal);

    // 5. Apply Speech-driven Viseme Weights
    Object.keys(lipsync.current).forEach((viseme) => {
      this.setMorphTarget(viseme, lipsync.current[viseme]);
    });

    // 6. Apply Emotion Morph Target Weights
    if (behavior.emotionWeights) {
      Object.keys(behavior.emotionWeights).forEach((morph) => {
        this.setMorphTarget(morph, behavior.emotionWeights[morph]);
      });
    }

    // 7. Update Orbit Controls and render scene
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
