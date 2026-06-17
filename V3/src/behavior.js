/**
 * BehaviorManager generates procedural animations for breathing,
 * blinking, and gaze saccades. Runs only when avatar is not speaking.
 * Speech-driven head bobbing removed — now driven entirely by server
 * blendshape matrix.
 */
export class BehaviorManager {
  constructor() {
    // Blinking state machine
    this.blinkVal = 0;
    this.blinkTimer = 0;
    this.blinkState = 'idle';
    this.blinkDuration = 0.09;
    this.blinkStateTime = 0;
    this.nextBlinkInterval = 3000 + Math.random() * 3000;

    // Gaze/Saccade state (UV coordinates shift)
    this.gazeOffset = { x: 0, y: 0 };
    this.gazeTarget = { x: 0, y: 0 };
    this.gazeTimer = 0;
    this.nextGazeInterval = 1000 + Math.random() * 2000;

    // Idle breathing (sine waves)
    this.breathingTime = 0;
    this.breathingSpeed = 1.8;
    this.breathingAmplitude = 0.022;

    // Output physical mesh transforms
    this.rotation = { x: 0, y: 0, z: 0 };
    this.position = { x: 0, y: 0, z: 0 };

    // ── Emotion-driven body motion ────────────────────────────────────────
    // Each emotion defines overrides for breathing speed/amplitude and
    // a head sway bias so the whole body "feels" different per mood.
    this._emotionBody = {
      neutral:   { breathSpeed: 1.8,  breathAmp: 0.022, swayAmp: 0.02,  headBiasY: 0.00 },
      happy:     { breathSpeed: 2.4,  breathAmp: 0.030, swayAmp: 0.035, headBiasY: 0.04 },
      sad:       { breathSpeed: 1.2,  breathAmp: 0.015, swayAmp: 0.008, headBiasY: -0.06 },
      angry:     { breathSpeed: 3.2,  breathAmp: 0.038, swayAmp: 0.012, headBiasY: 0.00 },
      surprised: { breathSpeed: 2.8,  breathAmp: 0.035, swayAmp: 0.025, headBiasY: 0.05 },
    };
    // Current interpolated body params
    this._bodyParams = { breathSpeed: 1.8, breathAmp: 0.022, swayAmp: 0.02, headBiasY: 0.00 };

    // Emotion states
    this.currentEmotion = 'happy';
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
   * Updates idle procedural state variables. No speech-driven motion.
   */
  update(dt) {
    // ── Interpolate body params toward current emotion ────────────────────
    const targetBody = this._emotionBody[this.currentEmotion] || this._emotionBody.neutral;
    const bodyLerp   = 1 - Math.exp(-2 * dt);  // slow, smooth transition ~0.5s
    for (const key of Object.keys(this._bodyParams)) {
      this._bodyParams[key] += (targetBody[key] - this._bodyParams[key]) * bodyLerp;
    }
    this.breathingSpeed    = this._bodyParams.breathSpeed;
    this.breathingAmplitude = this._bodyParams.breathAmp;

    // Interpolate active emotion weights
    const targetWeights = this.emotions[this.currentEmotion] || {};
    const emotionSpeed = 5.0;
    const emotionLerp = 1 - Math.exp(-emotionSpeed * dt);

    Object.keys(this.emotionWeights).forEach(key => {
      const target = targetWeights[key] || 0.0;
      this.emotionWeights[key] += (target - this.emotionWeights[key]) * emotionLerp;
    });

    // 1. Breathing
    this.breathingTime += dt;
    const breathingCycle = Math.sin(this.breathingTime * this.breathingSpeed);

    const swayAmp  = this._bodyParams.swayAmp;
    const biasY    = this._bodyParams.headBiasY;

    const breatheRotX = breathingCycle * this.breathingAmplitude;
    const breatheRotY = Math.cos(this.breathingTime * this.breathingSpeed * 0.5) * swayAmp * 0.3 + biasY;
    const breatheRotZ = Math.sin(this.breathingTime * this.breathingSpeed * 0.3) * this.breathingAmplitude * 0.15;
    const breathePosY = breathingCycle * this.breathingAmplitude * 0.25;

    // 2. Blinking
    this.updateBlinking(dt);

    // 3. Eye saccades
    this.updateSaccades(dt);

    // 4. Final idle transforms (no speech component)
    this.rotation.x = breatheRotX;
    this.rotation.y = breatheRotY;
    this.rotation.z = breatheRotZ;

    this.position.x = 0;
    this.position.y = breathePosY;
    this.position.z = 0;
  }

  updateBlinking(dt) {
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
        if (this.blinkVal >= 1.0) {
          this.blinkState = 'opening';
          this.blinkStateTime = 0;
        }
        break;

      case 'opening':
        this.blinkStateTime += dt;
        this.blinkVal = Math.max(1.0 - (this.blinkStateTime / this.blinkDuration), 0.0);
        if (this.blinkVal <= 0.0) {
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
        if (this.blinkStateTime >= 0.12) {
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
    this.nextBlinkInterval = 3000 + Math.random() * 3000;
  }

  updateSaccades(dt) {
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

    const lerpFactor = 1 - Math.exp(-20 * dt);
    this.gazeOffset.x += (this.gazeTarget.x - this.gazeOffset.x) * lerpFactor;
    this.gazeOffset.y += (this.gazeTarget.y - this.gazeOffset.y) * lerpFactor;
  }
}
