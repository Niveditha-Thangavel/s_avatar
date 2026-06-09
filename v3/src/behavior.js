/**
 * BehaviorManager generates procedural animations for breathing,
 * blinking, gaze saccades, and head bobbing.
 */
export class BehaviorManager {
  constructor() {
    // Blinking state machine
    this.blinkVal = 0;
    this.blinkTimer = 0;
    this.blinkState = 'idle'; // 'idle', 'closing', 'opening', 'waiting-double', 'double-closing', 'double-opening'
    this.blinkDuration = 0.09; // seconds (fast blink)
    this.blinkStateTime = 0;
    this.nextBlinkInterval = 3000 + Math.random() * 3000; // 3–6s

    // Gaze/Saccade state (UV coordinates shift)
    this.gazeOffset = { x: 0, y: 0 };
    this.gazeTarget = { x: 0, y: 0 };
    this.gazeTimer = 0;
    this.nextGazeInterval = 1000 + Math.random() * 2000; // 1–3s

    // Idle breathing (sine waves)
    this.breathingTime = 0;
    this.breathingSpeed = 1.8; // rad/sec
    this.breathingAmplitude = 0.022; // Increased for clear breathing sway

    // Output physical mesh transforms (rotations in radians, translations in scene units)
    this.rotation = { x: 0, y: 0, z: 0 };
    this.position = { x: 0, y: 0, z: 0 };

    // Speech-driven motion filter
    this.speechBobbing = 0;

    // Emotion states
    this.currentEmotion = 'neutral';
    this.emotions = {
      neutral: {},
      happy: {
        mouthSmileLeft: 0.45,
        mouthSmileRight: 0.45,
        cheekSquintLeft: 0.25,
        cheekSquintRight: 0.25,
        browOuterUpLeft: 0.20,
        browOuterUpRight: 0.20
      },
      sad: {
        mouthFrownLeft: 0.55,
        mouthFrownRight: 0.55,
        browInnerUp: 0.45,
        browDownLeft: 0.15,
        browDownRight: 0.15
      },
      angry: {
        browDownLeft: 0.65,
        browDownRight: 0.65,
        eyeSquintLeft: 0.35,
        eyeSquintRight: 0.35,
        mouthFrownLeft: 0.25,
        mouthFrownRight: 0.25
      },
      surprised: {
        eyeWideLeft: 0.5,
        eyeWideRight: 0.5,
        browInnerUp: 0.55,
        browOuterUpLeft: 0.35,
        browOuterUpRight: 0.35,
        mouthOpen: 0.15
      }
    };
    
    this.emotionWeights = {
      mouthSmileLeft: 0, mouthSmileRight: 0,
      cheekSquintLeft: 0, cheekSquintRight: 0,
      browOuterUpLeft: 0, browOuterUpRight: 0,
      mouthFrownLeft: 0, mouthFrownRight: 0,
      browInnerUp: 0, browDownLeft: 0, browDownRight: 0,
      eyeSquintLeft: 0, eyeSquintRight: 0,
      eyeWideLeft: 0, eyeWideRight: 0,
      mouthOpen: 0
    };
  }

  /**
   * Updates state variables based on delta time (seconds) and real-time audio volume (0..1)
   */
  update(dt, volume) {
    // Interpolate active emotion weights
    const targetWeights = this.emotions[this.currentEmotion] || {};
    const emotionSpeed = 5.0; // Smooth transitions
    const emotionLerp = 1 - Math.exp(-emotionSpeed * dt);

    Object.keys(this.emotionWeights).forEach(key => {
      const target = targetWeights[key] || 0.0;
      this.emotionWeights[key] += (target - this.emotionWeights[key]) * emotionLerp;
    });

    // 1. Update breathing timeline
    this.breathingTime += dt;
    const breathingCycle = Math.sin(this.breathingTime * this.breathingSpeed);
    
    // Breathing moves the shoulders/body up/down and tilts head
    const breatheRotX = breathingCycle * this.breathingAmplitude;
    // Add small breathing sway in Y and Z for realistic posture shifts
    const breatheRotY = Math.cos(this.breathingTime * this.breathingSpeed * 0.5) * this.breathingAmplitude * 0.3;
    const breatheRotZ = Math.sin(this.breathingTime * this.breathingSpeed * 0.3) * this.breathingAmplitude * 0.15;
    const breathePosY = breathingCycle * this.breathingAmplitude * 0.25;

    // 2. Blinking state machine
    this.updateBlinking(dt);

    // 3. Eye saccades (minor shifts in looking direction)
    this.updateSaccades(dt);

    // 4. Speech-driven head bobbing
    // Smooth the volume input to avoid jerky movements
    this.speechBobbing += (volume - this.speechBobbing) * (1 - Math.exp(-8 * dt));

    // Calculate rotation and translation offsets based on speech bobbing (increased scale factors)
    const speechRotX = Math.sin(this.breathingTime * 11) * this.speechBobbing * 0.065; // nodding
    const speechRotY = Math.cos(this.breathingTime * 7.5) * this.speechBobbing * 0.095; // head shaking
    const speechRotZ = Math.sin(this.breathingTime * 8) * this.speechBobbing * 0.038;   // head roll
    const speechPosY = Math.sin(this.breathingTime * 13) * this.speechBobbing * 0.09;  // vertical hop
    const speechPosX = Math.cos(this.breathingTime * 9) * this.speechBobbing * 0.07;   // horizontal sway

    // 5. Combine everything into final mesh transformations
    // Idle breathing + speech-driven head movement + a tiny bit of random drift
    this.rotation.x = breatheRotX + speechRotX;
    this.rotation.y = breatheRotY + speechRotY;
    this.rotation.z = breatheRotZ + speechRotZ;

    this.position.x = speechPosX;
    this.position.y = breathePosY + speechPosY;
    this.position.z = 0; // Flat plane stays at z depth
  }

  updateBlinking(dt) {
    this.blinkTimer += dt * 1000; // convert to ms

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
        if (this.blinkVal >= 1.0) {
          this.blinkState = 'opening';
          this.blinkStateTime = 0;
        }
        break;

      case 'opening':
        this.blinkStateTime += dt;
        this.blinkVal = Math.max(1.0 - (this.blinkStateTime / this.blinkDuration), 0.0);
        if (this.blinkVal <= 0.0) {
          // 15% chance of a double blink
          if (Math.random() < 0.15) {
            this.blinkState = 'waiting-double';
            this.blinkStateTime = 0;
          } else {
            this.resetBlinkTimer();
          }
        }
        break;

      case 'waiting-double':
        this.blinkVal = 0;
        this.blinkStateTime += dt;
        if (this.blinkStateTime >= 0.12) { // 120ms wait between blinks
          this.blinkState = 'double-closing';
          this.blinkStateTime = 0;
        }
        break;

      case 'double-closing':
        this.blinkStateTime += dt;
        this.blinkVal = Math.min(this.blinkStateTime / this.blinkDuration, 1.0);
        if (this.blinkVal >= 1.0) {
          this.blinkState = 'double-opening';
          this.blinkStateTime = 0;
        }
        break;

      case 'double-opening':
        this.blinkStateTime += dt;
        this.blinkVal = Math.max(1.0 - (this.blinkStateTime / this.blinkDuration), 0.0);
        if (this.blinkVal <= 0.0) {
          this.resetBlinkTimer();
        }
        break;
    }
  }

  resetBlinkTimer() {
    this.blinkState = 'idle';
    this.blinkTimer = 0;
    this.nextBlinkInterval = 3000 + Math.random() * 3000; // next blink in 3-6s
  }

  updateSaccades(dt) {
    this.gazeTimer += dt * 1000;

    if (this.gazeTimer >= this.nextGazeInterval) {
      // 70% chance of looking in a slightly different direction
      // 30% chance of looking straight back at camera
      if (Math.random() < 0.70) {
        // Tiny gaze offsets in UV space (max 0.015 horizontal, 0.008 vertical)
        this.gazeTarget.x = (Math.random() - 0.5) * 0.012;
        this.gazeTarget.y = (Math.random() - 0.5) * 0.006;
      } else {
        this.gazeTarget.x = 0;
        this.gazeTarget.y = 0;
      }
      this.gazeTimer = 0;
      this.nextGazeInterval = 1000 + Math.random() * 2500; // next shift in 1-3.5s
    }

    // Smoothly interpolate current gaze offset towards target offset (high speed)
    const lerpFactor = 1 - Math.exp(-20 * dt);
    this.gazeOffset.x += (this.gazeTarget.x - this.gazeOffset.x) * lerpFactor;
    this.gazeOffset.y += (this.gazeTarget.y - this.gazeOffset.y) * lerpFactor;
  }
}
