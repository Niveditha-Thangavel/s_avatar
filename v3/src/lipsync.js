/**
 * LipSyncManager handles Web Audio API playback queuing,
 * phoneme-to-viseme mapping, character-ratio timeline playing,
 * and timing synchronization.
 */
export class LipSyncManager {
  constructor() {
    this.audioCtx = null;
    this.analyser = null;
    this.nextPlayTime = 0;
    this.isPlaying = false;
    
    // Audio pre-roll: start playing after the first chunk arrives.
    // Setting threshold to 1 minimises latency; flushBuffer() is called
    // when TTS signals "complete" to force-play any remaining queued chunks.
    this.chunkQueue = [];
    this.isBuffering = true;
    this.bufferThreshold = 1;
    
    // Timeline of scheduled phonemes (for standard text fallback)
    // Each entry: { startTime, endTime, targetVisemes }
    this.phonemeTimeline = [];

    // Queue of scheduled audio chunks (for Character-Ratio Sync Engine)
    // Each entry: { startTime, endTime, duration, romanizedText }
    this.playingChunks = [];

    // Active scheduled audio source nodes (to allow stopping them immediately on interruption)
    this.activeSources = [];
    
    // Target viseme values (the targets we are moving towards)
    this.targets = {
      viseme_sil: 1.0,
      viseme_PP: 0.0,
      viseme_FF: 0.0,
      viseme_TH: 0.0,
      viseme_DD: 0.0,
      viseme_kk: 0.0,
      viseme_CH: 0.0,
      viseme_SS: 0.0,
      viseme_nn: 0.0,
      viseme_RR: 0.0,
      viseme_aa: 0.0,
      viseme_E: 0.0,
      viseme_I: 0.0,
      viseme_O: 0.0,
      viseme_U: 0.0
    };

    // Current smoothed viseme values (actually read by the renderer for morphing)
    this.current = {
      viseme_sil: 1.0,
      viseme_PP: 0.0,
      viseme_FF: 0.0,
      viseme_TH: 0.0,
      viseme_DD: 0.0,
      viseme_kk: 0.0,
      viseme_CH: 0.0,
      viseme_SS: 0.0,
      viseme_nn: 0.0,
      viseme_RR: 0.0,
      viseme_aa: 0.0,
      viseme_E: 0.0,
      viseme_I: 0.0,
      viseme_O: 0.0,
      viseme_U: 0.0
    };

    // Regex lookup map for deterministic viseme morphing based on active Romanized syllable
    this.visemeRegexMap = [
      { regex: /[mpb]/i, viseme: 'viseme_PP' },
      { regex: /[fv]/i, viseme: 'viseme_FF' },
      { regex: /th/i, viseme: 'viseme_TH' },
      { regex: /[tdn]/i, viseme: 'viseme_DD' },
      { regex: /[kg]/i, viseme: 'viseme_kk' },
      { regex: /(ch|sh|j)/i, viseme: 'viseme_CH' },
      { regex: /[sz]/i, viseme: 'viseme_SS' },
      { regex: /[r]/i, viseme: 'viseme_RR' },
      { regex: /(aa|a)/i, viseme: 'viseme_aa' },
      { regex: /(ee|e)/i, viseme: 'viseme_E' },
      { regex: /(ii|i)/i, viseme: 'viseme_I' },
      { regex: /(oo|o)/i, viseme: 'viseme_O' },
      { regex: /(uu|u|w)/i, viseme: 'viseme_U' }
    ];

    // Phoneme categories and mapping weights (for traditional IPA fallback)
    this.phonemeWeights = {
      // Vowels & Diphthongs (Longer, open mouth)
      'a': 1.5, 'e': 1.4, 'i': 1.3, 'o': 1.5, 'u': 1.5,
      'ɑ': 1.5, 'æ': 1.5, 'ɔ': 1.5, 'ə': 1.1, 'ʌ': 1.3,
      'ɪ': 1.2, 'ʊ': 1.3, 'ɛ': 1.3, 'ɒ': 1.5,

      // Bilabials (Mouth closed, quick)
      'p': 0.6, 'b': 0.6, 'm': 0.8,

      // Labiodentals (Teeth on lip)
      'f': 0.9, 'v': 0.9,

      // Fricatives & Plosives (Mouth slightly open)
      's': 1.0, 'z': 1.0, 't': 0.7, 'd': 0.7, 'k': 0.7, 'g': 0.7,
      'ʃ': 1.1, 'ʒ': 1.1, 'θ': 0.9, 'ð': 0.9, 'h': 1.0,

      // Nasals & Liquids
      'n': 0.9, 'ŋ': 0.9, 'l': 1.0, 'r': 1.1,

      // Semivowels
      'w': 1.2, 'j': 1.0,

      // Pauses, spaces, punctuation
      ' ': 1.8, '_': 1.8, '.': 1.8, ',': 1.8, '?': 1.8, '!': 1.8
    };
  }

  init() {
    if (this.audioCtx) return;
    
    // Create AudioContext (must be created/resumed on user interaction)
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    this.audioCtx = new AudioContextClass();
    
    // Create AnalyserNode for volume detection
    this.analyser = this.audioCtx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.connect(this.audioCtx.destination);
    
    this.nextPlayTime = this.audioCtx.currentTime;
  }

  resume() {
    if (this.audioCtx && this.audioCtx.state === 'suspended') {
      this.audioCtx.resume();
    }
  }

  stop() {
    // Immediately stop and disconnect all scheduled audio playbacks
    if (this.activeSources) {
      this.activeSources.forEach(source => {
        try {
          source.stop();
          source.disconnect();
        } catch (e) {
          // Ignore if already stopped or finished
        }
      });
      this.activeSources = [];
    }

    this.phonemeTimeline = [];
    this.playingChunks = [];
    this.isPlaying = false;
    this.isBuffering = true; // Reset back to buffering state for next speak
    this.chunkQueue = [];
    
    if (this.audioCtx) {
      // Re-initialize time to current
      this.nextPlayTime = this.audioCtx.currentTime;
    }
    
    // Reset targets
    this.resetTargets();
  }

  resetTargets() {
    Object.keys(this.targets).forEach(k => {
      this.targets[k] = (k === 'viseme_sil') ? 1.0 : 0.0;
    });
  }

  /**
   * Returns the current audio volume (0.0 to 1.0)
   */
  getVolume() {
    if (!this.analyser || !this.isPlaying) return 0;
    
    const dataArray = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(dataArray);
    
    // Calculate Root Mean Square (RMS) volume
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      const val = (dataArray[i] - 128) / 128; // Normalize to -1 to 1
      sum += val * val;
    }
    
    const rms = Math.sqrt(sum / dataArray.length);
    // Scale up slightly for head motion sensitivity
    return Math.min(rms * 4.0, 1.0);
  }

  /**
   * Rules-based Syllable Parser for Romanized text.
   * Splits a word into consonant-vowel syllable blocks.
   */
  splitIntoSyllables(word) {
    const pattern = /[^aeiou]*[aeiou]+(?:[^aeiou](?![aeiou]))*/gi;
    const matches = word.match(pattern);
    return matches || [word];
  }

  /**
   * Maps a Romanized syllable block to target visemes via Regex lookup.
   */
  mapSyllableToVisemes(syllable) {
    const defaultVisemes = {
      viseme_sil: 1.0, viseme_PP: 0.0, viseme_FF: 0.0, viseme_TH: 0.0,
      viseme_DD: 0.0, viseme_kk: 0.0, viseme_CH: 0.0, viseme_SS: 0.0,
      viseme_nn: 0.0, viseme_RR: 0.0, viseme_aa: 0.0, viseme_E: 0.0,
      viseme_I: 0.0, viseme_O: 0.0, viseme_U: 0.0
    };
    
    const visemes = { ...defaultVisemes, viseme_sil: 0.0 };
    if (!syllable || !syllable.trim()) {
      visemes.viseme_sil = 1.0;
      return visemes;
    }

    let matched = false;
    for (const item of this.visemeRegexMap) {
      if (item.regex.test(syllable)) {
        visemes[item.viseme] = 1.0;
        matched = true;
        break;
      }
    }

    if (!matched) {
      visemes.viseme_sil = 1.0;
    }
    return visemes;
  }

  /**
   * Maps an IPA character to target viseme blendshapes (fallback)
   */
  mapPhonemeToVisemes(char) {
    const defaultVisemes = {
      viseme_sil: 1.0, viseme_PP: 0.0, viseme_FF: 0.0, viseme_TH: 0.0,
      viseme_DD: 0.0, viseme_kk: 0.0, viseme_CH: 0.0, viseme_SS: 0.0,
      viseme_nn: 0.0, viseme_RR: 0.0, viseme_aa: 0.0, viseme_E: 0.0,
      viseme_I: 0.0, viseme_O: 0.0, viseme_U: 0.0
    };
    
    const visemes = { ...defaultVisemes, viseme_sil: 0.0 };
    
    // 1. Bilabials (p, b, m, β, ɸ)
    if (['p', 'b', 'm', 'β', 'ɸ'].includes(char)) {
      visemes.viseme_PP = 1.0;
      return visemes;
    }
    // 2. Labiodentals (f, v, ɱ)
    if (['f', 'v', 'ɱ'].includes(char)) {
      visemes.viseme_FF = 1.0;
      return visemes;
    }
    // 3. Dentals (θ, ð)
    if (['θ', 'ð'].includes(char)) {
      visemes.viseme_TH = 1.0;
      return visemes;
    }
    // 4. Alveolar Plosives (t, d, ɾ, ɽ, ʔ)
    if (['t', 'd', 'ɾ', 'ɽ', 'ʔ'].includes(char)) {
      visemes.viseme_DD = 1.0;
      return visemes;
    }
    // 5. Velars & Palatals (k, g, ɡ, c, ɟ, q, ɢ)
    if (['k', 'g', 'ɡ', 'c', 'ɟ', 'q', 'ɢ'].includes(char)) {
      visemes.viseme_kk = 1.0;
      return visemes;
    }
    // 6. Postalveolars, Affricates & Palatal Glides (ʃ, ʒ, ʧ, ʤ, j, ɲ, ç, ʎ)
    if (['ʃ', 'ʒ', 'ʧ', 'ʤ', 'j', 'ɲ', 'ç', 'ʎ', 'tʃ', 'dʒ'].includes(char)) {
      visemes.viseme_CH = 1.0;
      return visemes;
    }
    // 7. Sibilants (s, z, ɬ, ɮ)
    if (['s', 'z', 'ɬ', 'ɮ'].includes(char)) {
      visemes.viseme_SS = 1.0;
      return visemes;
    }
    // 8. Nasals & Liquids (n, l, ŋ, ɫ, ɳ, ɭ)
    if (['n', 'ŋ', 'l', 'ɫ', 'ɳ', 'ɭ'].includes(char)) {
      visemes.viseme_nn = 1.0;
      return visemes;
    }
    // 9. Approximants / R-sounds (r, ɹ, ɻ, ʀ, ʁ)
    if (['r', 'ɹ', 'ɻ', 'ʀ', 'ʁ'].includes(char)) {
      visemes.viseme_RR = 1.0;
      return visemes;
    }
    // 10. Open & Mid-Central Vowels (a, ɑ, æ, ʌ, ɒ, ə, ɐ, ɜ, ɝ)
    if (['a', 'ɑ', 'æ', 'ʌ', 'ɒ', 'ə', 'ɐ', 'ɜ', 'ɝ', 'ah'].includes(char)) {
      visemes.viseme_aa = 1.0;
      return visemes;
    }
    // 11. Mid-Front Vowels (e, ɛ, œ, ø, eɪ', 'eh'].includes(char)) {
    if (['e', 'ɛ', 'œ', 'ø', 'eɪ', 'eh'].includes(char)) {
      visemes.viseme_E = 1.0;
      return visemes;
    }
    // 12. Close-Front Vowels (i, ɪ, y, ih)
    if (['i', 'ɪ', 'y', 'ih'].includes(char)) {
      visemes.viseme_I = 1.0;
      return visemes;
    }
    // 13. Close-Mid-Back Vowels (o, ɔ, oh)
    if (['o', 'ɔ', 'oʊ', 'oh'].includes(char)) {
      visemes.viseme_O = 1.0;
      return visemes;
    }
    // 14. Close-Back Vowels & Rounded Approximants (u, ʊ, w, ʍ, ʉ)
    if (['u', 'ʊ', 'w', 'ʍ', 'ʉ'].includes(char)) {
      visemes.viseme_U = 1.0;
      return visemes;
    }
    
    // Silence/default fallback
    visemes.viseme_sil = 1.0;
    return visemes;
  }

  /**
   * Queue a new chunk of audio samples (applying pre-rolling buffer)
   */
  queueAudioChunk(samples, samplingRate, phonemeString, romanizedText = null) {
    this.init();
    this.resume();

    if (this.isBuffering) {
      this.chunkQueue.push({ samples, samplingRate, phonemeString, romanizedText });
      
      // Once we cross the pre-roll threshold, flush and play all contiguously
      if (this.chunkQueue.length >= this.bufferThreshold) {
        this.flushBuffer();
      }
    } else {
      // Already playing, schedule immediately
      this.scheduleChunk(samples, samplingRate, phonemeString, romanizedText);
    }
  }

  /**
   * Flushes the pre-roll buffer queue and starts scheduled playback.
   */
  flushBuffer() {
    const tempQueue = [...this.chunkQueue];
    this.chunkQueue = [];
    this.isBuffering = false;

    tempQueue.forEach(chunk => {
      this.scheduleChunk(chunk.samples, chunk.samplingRate, chunk.phonemeString, chunk.romanizedText);
    });
  }

  /**
   * Schedules a single audio chunk and aligns its phonemes or romanized text
   */
  scheduleChunk(samples, samplingRate, phonemeString, romanizedText = null) {
    const duration = samples.length / samplingRate;
    const currentTime = this.audioCtx.currentTime;

    // If we've drifted behind, reset the play timeline
    if (this.nextPlayTime < currentTime) {
      this.nextPlayTime = currentTime + 0.15; // safety buffer
    }

    const chunkStartTime = this.nextPlayTime;
    const chunkEndTime = chunkStartTime + duration;

    // 1. Create Audio Buffer
    const audioBuffer = this.audioCtx.createBuffer(1, samples.length, samplingRate);
    audioBuffer.getChannelData(0).set(samples);

    // 2. Create and connect source node
    const sourceNode = this.audioCtx.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(this.analyser);

    // Track active source
    this.activeSources.push(sourceNode);

    // 3. Play buffer
    sourceNode.start(chunkStartTime);
    this.isPlaying = true;

    // Handle play state end
    sourceNode.onended = () => {
      // Remove from active sources
      this.activeSources = this.activeSources.filter(src => src !== sourceNode);

      // Check if timeline is empty
      if (this.audioCtx && this.audioCtx.currentTime >= this.nextPlayTime - 0.15) {
        this.isPlaying = false;
        this.resetTargets();
      }
    };

    if (romanizedText) {
      // Use Character-Ratio Timeline Mapping
      this.playingChunks.push({
        startTime: chunkStartTime,
        endTime: chunkEndTime,
        duration: duration,
        romanizedText: romanizedText
      });
    } else {
      // Fallback: Align phonemes precisely by trimming silent padding in audio timeline
      const threshold = 0.002;
      let startIdx = 0;
      while (startIdx < samples.length && Math.abs(samples[startIdx]) < threshold) {
        startIdx++;
      }

      if (startIdx === samples.length) {
        // Entire chunk is silent
        this.alignPhonemes('', chunkStartTime, duration);
      } else {
        let endIdx = samples.length - 1;
        while (endIdx > startIdx && Math.abs(samples[endIdx]) < threshold) {
          endIdx--;
        }

        const leadingSilence = startIdx / samplingRate;
        const trailingSilence = (samples.length - 1 - endIdx) / samplingRate;
        const activeDuration = duration - leadingSilence - trailingSilence;

        if (activeDuration > 0.01) {
          // Schedule leading silence
          if (leadingSilence > 0.01) {
            this.alignPhonemes('', chunkStartTime, leadingSilence);
          }
          // Schedule active speech phonemes
          this.alignPhonemes(phonemeString, chunkStartTime + leadingSilence, activeDuration);
          // Schedule trailing silence
          if (trailingSilence > 0.01) {
            this.alignPhonemes('', chunkEndTime - trailingSilence, trailingSilence);
          }
        } else {
          // Fallback if active speech duration calculation is too small
          this.alignPhonemes(phonemeString, chunkStartTime, duration);
        }
      }
    }

    // Advance scheduling pointer
    this.nextPlayTime = chunkEndTime;
  }

  /**
   * Splits a phoneme string and distributes the duration proportionally (fallback).
   */
  alignPhonemes(phonemes, startTime, totalDuration) {
    if (!phonemes || phonemes.length === 0) {
      // Push a silence block
      this.phonemeTimeline.push({
        startTime: startTime,
        endTime: startTime + totalDuration,
        targetVisemes: {
          viseme_sil: 1.0, viseme_PP: 0.0, viseme_FF: 0.0, viseme_TH: 0.0,
          viseme_DD: 0.0, viseme_kk: 0.0, viseme_CH: 0.0, viseme_SS: 0.0,
          viseme_nn: 0.0, viseme_RR: 0.0, viseme_aa: 0.0, viseme_E: 0.0,
          viseme_I: 0.0, viseme_O: 0.0, viseme_U: 0.0
        }
      });
      return;
    }

    const cleanPhonemes = [];
    for (let i = 0; i < phonemes.length; i++) {
      const char = phonemes[i];
      if (char === 'ˈ' || char === 'ˌ') {
        continue;
      }
      if (char === 'ː' && cleanPhonemes.length > 0) {
        cleanPhonemes[cleanPhonemes.length - 1].weight *= 1.8;
        continue;
      }
      
      const weight = this.phonemeWeights[char.toLowerCase()] || 1.0;
      cleanPhonemes.push({ char, weight });
    }

    if (cleanPhonemes.length === 0) return;

    const totalWeight = cleanPhonemes.reduce((sum, item) => sum + item.weight, 0);

    let elapsed = 0;
    for (const item of cleanPhonemes) {
      const itemDuration = (item.weight / totalWeight) * totalDuration;
      const start = startTime + elapsed;
      const end = start + itemDuration;

      this.phonemeTimeline.push({
        startTime: start,
        endTime: end,
        targetVisemes: this.mapPhonemeToVisemes(item.char.toLowerCase())
      });

      elapsed += itemDuration;
    }
  }

  /**
   * Updates current and targets based on the delta time and playback timeline
   */
  update(dt) {
    if (!this.isPlaying || !this.audioCtx) {
      this.resetTargets();
    } else {
      const playTime = this.audioCtx.currentTime;

      // Clean up past playing chunks
      this.playingChunks = this.playingChunks.filter(c => c.endTime >= playTime);

      const activeChunk = this.playingChunks.find(
        c => playTime >= c.startTime && playTime <= c.endTime
      );

      if (activeChunk) {
        // Character-Ratio Sync Engine: scrub active Romanized syllable
        const elapsed = playTime - activeChunk.startTime;
        const ratio = Math.max(0, Math.min(1, elapsed / activeChunk.duration));

        const words = activeChunk.romanizedText.split(/\s+/).filter(w => w.length > 0);
        const syllables = [];
        for (const word of words) {
          syllables.push(...this.splitIntoSyllables(word));
        }

        if (syllables.length > 0) {
          const syllableIndex = Math.min(
            syllables.length - 1,
            Math.floor(ratio * syllables.length)
          );
          const activeSyllable = syllables[syllableIndex] || '';
          const targetVisemes = this.mapSyllableToVisemes(activeSyllable);
          
          Object.keys(this.targets).forEach(k => {
            this.targets[k] = targetVisemes[k] !== undefined ? targetVisemes[k] : 0.0;
          });
        } else {
          this.resetTargets();
        }
      } else {
        // Fallback to traditional timeline scheduling
        this.phonemeTimeline = this.phonemeTimeline.filter(event => event.endTime >= playTime);

        const activeEvent = this.phonemeTimeline.find(
          event => playTime >= event.startTime && playTime <= event.endTime
        );

        if (activeEvent) {
          Object.keys(this.targets).forEach(k => {
            this.targets[k] = activeEvent.targetVisemes[k] !== undefined ? activeEvent.targetVisemes[k] : 0.0;
          });
        } else {
          this.resetTargets();
        }
      }
    }

    // Smoothly LERP current values towards targets
    const speed = 20.0;
    const lerpFactor = 1 - Math.exp(-speed * dt);
    
    Object.keys(this.current).forEach(k => {
      this.current[k] += (this.targets[k] - this.current[k]) * lerpFactor;
    });
  }
}
