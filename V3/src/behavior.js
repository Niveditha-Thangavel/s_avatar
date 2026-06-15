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
   * Updates idle procedural state variables. No speech-driven motion.
   */
  update(dt) {
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

    const breatheRotX = breathingCycle * this.breathingAmplitude;
    const breatheRotY = Math.cos(this.breathingTime * this.breathingSpeed * 0.5) * this.breathingAmplitude * 0.3;
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
