/**
 * LipSyncManager — accurate text-driven lip sync
 *
 * Pipeline:
 *   romanizedText → words → syllables → phoneme-sequence per syllable
 *                → timed viseme events → LERP blending each frame
 *
 * Each syllable is split into:
 *   [onset consonant cluster] → [nucleus vowel] → [coda consonant]
 * Each part gets its own time slice and viseme, so the mouth opens and
 * closes naturally rather than holding one pose per syllable.
 */
export class LipSyncManager {
  constructor() {
    this.audioCtx   = null;
    this.analyser   = null;
    this.nextPlayTime = 0;
    this.isPlaying    = false;

    this.chunkQueue      = [];
    this.isBuffering     = true;
    this.bufferThreshold = 1;

    // Timed viseme event queue — filled by scheduleChunk
    // Each entry: { startTime, endTime, viseme, weight }
    this.visemeTimeline = [];

    // Active audio source nodes (for immediate stop)
    this.activeSources = [];

    // Smoothed current viseme weights read by the renderer every frame
    this.current = {
      viseme_sil: 1.0,
      viseme_PP:  0.0,
      viseme_FF:  0.0,
      viseme_TH:  0.0,
      viseme_DD:  0.0,
      viseme_kk:  0.0,
      viseme_CH:  0.0,
      viseme_SS:  0.0,
      viseme_nn:  0.0,
      viseme_RR:  0.0,
      viseme_aa:  0.0,
      viseme_E:   0.0,
      viseme_I:   0.0,
      viseme_O:   0.0,
      viseme_U:   0.0,
    };

    // Instantaneous targets (lerped → current each frame)
    this.targets = { ...this.current };
  }

  // ── Init / lifecycle ────────────────────────────────────────────────────────

  init() {
    if (this.audioCtx) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    this.audioCtx = new Ctx();
    this.analyser = this.audioCtx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.connect(this.audioCtx.destination);
    this.nextPlayTime = this.audioCtx.currentTime;
  }

  resume() {
    if (this.audioCtx?.state === 'suspended') this.audioCtx.resume();
  }

  stop() {
    this.activeSources.forEach(src => {
      try { src.stop(); src.disconnect(); } catch (_) {}
    });
    this.activeSources  = [];
    this.visemeTimeline = [];
    this.isPlaying      = false;
    this.isBuffering    = true;
    this.chunkQueue     = [];
    if (this.audioCtx) this.nextPlayTime = this.audioCtx.currentTime;
    this._resetTargets();
  }

  flushBuffer() {
    const q = [...this.chunkQueue];
    this.chunkQueue  = [];
    this.isBuffering = false;
    q.forEach(c => this._scheduleChunk(c.samples, c.samplingRate, c.romanizedText, c.nativeText));
  }

  // ── Public: queue an audio chunk ───────────────────────────────────────────

  /**
   * @param {Float32Array} samples
   * @param {number}       samplingRate
   * @param {string}       nativeText      — native-script sentence (for display)
   * @param {string|null}  romanizedText   — Latin romanization (used for visemes)
   */
  queueAudioChunk(samples, samplingRate, nativeText, romanizedText = null) {
    this.init();
    this.resume();

    const entry = { samples, samplingRate, romanizedText, nativeText };

    if (this.isBuffering) {
      this.chunkQueue.push(entry);
      if (this.chunkQueue.length >= this.bufferThreshold) this.flushBuffer();
    } else {
      this._scheduleChunk(samples, samplingRate, romanizedText, nativeText);
    }
  }

  // ── Public: RMS volume for head-bobbing ─────────────────────────────────────

  getVolume() {
    if (!this.analyser || !this.isPlaying) return 0;
    const buf = new Uint8Array(this.analyser.frequencyBinCount);
    this.analyser.getByteTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
      const v = (buf[i] - 128) / 128;
      sum += v * v;
    }
    return Math.min(Math.sqrt(sum / buf.length) * 4.0, 1.0);
  }

  // ── Public: update called every frame ──────────────────────────────────────

  update(dt) {
    if (!this.isPlaying || !this.audioCtx) {
      this._resetTargets();
    } else {
      const now = this.audioCtx.currentTime;

      // Remove expired events
      this.visemeTimeline = this.visemeTimeline.filter(e => e.endTime >= now);

      // Find the currently active event
      const active = this.visemeTimeline.find(e => now >= e.startTime && now <= e.endTime);

      if (active) {
        // Zero all visemes, then activate only the current one
        Object.keys(this.targets).forEach(k => { this.targets[k] = 0.0; });
        // viseme_sil means silence — leave all at 0 (mouth rests closed naturally)
        if (active.viseme !== 'viseme_sil') {
          this.targets[active.viseme] = 1.0;
        }
      } else {
        this._resetTargets();
      }
    }

    // Exponential LERP — fast during speech, slightly slower at rest
    const speed = this.isPlaying ? 22.0 : 14.0;
    const lf    = 1 - Math.exp(-speed * dt);
    Object.keys(this.current).forEach(k => {
      this.current[k] += (this.targets[k] - this.current[k]) * lf;
    });
  }

  // ── Private: schedule one audio chunk ──────────────────────────────────────

  _scheduleChunk(samples, samplingRate, romanizedText, nativeText) {
    const duration    = samples.length / samplingRate;
    const currentTime = this.audioCtx.currentTime;

    if (this.nextPlayTime < currentTime) {
      this.nextPlayTime = currentTime + 0.05;
    }

    const chunkStart = this.nextPlayTime;
    const chunkEnd   = chunkStart + duration;

    // ── Audio playback ──────────────────────────────────────────────────────
    const buf = this.audioCtx.createBuffer(1, samples.length, samplingRate);
    buf.getChannelData(0).set(samples);

    const src = this.audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(this.analyser);
    src.start(chunkStart);
    this.isPlaying = true;
    this.activeSources.push(src);

    src.onended = () => {
      this.activeSources = this.activeSources.filter(s => s !== src);
      if (this.audioCtx && this.audioCtx.currentTime >= this.nextPlayTime - 0.1) {
        this.isPlaying = false;
        this._resetTargets();
      }
    };

    // ── Build viseme timeline for this chunk ────────────────────────────────
    // Prefer romanizedText for viseme mapping; fall back to nativeText
    const textForVisemes = romanizedText || nativeText || '';

    // Detect silent padding in the audio and exclude it from viseme timing
    const { activeStart, activeEnd } = this._detectActiveSpeech(samples, samplingRate);

    const visemeStart    = chunkStart + activeStart;
    const visemeDuration = Math.max(0.01, activeEnd - activeStart);

    if (textForVisemes.trim()) {
      const events = this._textToVisemeEvents(textForVisemes, visemeStart, visemeDuration);
      this.visemeTimeline.push(...events);
    }

    this.nextPlayTime = chunkEnd;
  }

  // ── Private: detect non-silent region ──────────────────────────────────────

  _detectActiveSpeech(samples, samplingRate) {
    const threshold = 0.002;
    let s = 0;
    while (s < samples.length && Math.abs(samples[s]) < threshold) s++;
    let e = samples.length - 1;
    while (e > s && Math.abs(samples[e]) < threshold) e--;

    const activeStart = s / samplingRate;
    const activeEnd   = (e + 1) / samplingRate;

    // If whole chunk is silent, return full duration
    if (s >= samples.length) return { activeStart: 0, activeEnd: samples.length / samplingRate };
    return { activeStart, activeEnd };
  }

  // ── Private: core pipeline — text → timed viseme events ────────────────────

  /**
   * Splits text into words → syllables → phoneme-sequences,
   * assigns each a time slice proportional to its phonetic complexity,
   * and returns an array of { startTime, endTime, viseme, weight } objects.
   */
  _textToVisemeEvents(text, startTime, totalDuration) {
    // 1. Clean text: strip non-alpha except spaces and apostrophes
    const clean = text.replace(/[^a-zA-Z'\s]/g, ' ').replace(/\s+/g, ' ').trim();
    if (!clean) return [];

    // 2. Split into words
    const words = clean.split(' ').filter(w => w.length > 0);

    // 3. Each word → array of phoneme-sequence objects
    //    A phoneme-sequence is an array of { viseme, relativeDuration }
    //    The relative durations are later scaled to fit the total time.
    const allSeqs = [];  // flat list of { viseme, relDur }

    for (const word of words) {
      const syllables = this._splitSyllables(word);
      for (const syllable of syllables) {
        const seq = this._syllableToPhonemeSequence(syllable);
        allSeqs.push(...seq);
      }
      // Add a short inter-word pause
      allSeqs.push({ viseme: 'viseme_sil', relDur: 0.15 });
    }

    if (allSeqs.length === 0) return [];

    // 4. Scale relative durations to fill totalDuration
    const totalRel = allSeqs.reduce((s, e) => s + e.relDur, 0);
    const scale    = totalDuration / totalRel;

    // 5. Build timed events
    const events = [];
    let t = startTime;
    for (const item of allSeqs) {
      const dur = item.relDur * scale;
      if (dur > 0.008) {  // skip events shorter than 8ms
        events.push({
          startTime: t,
          endTime:   t + dur,
          viseme:    item.viseme,
          weight:    item.viseme === 'viseme_sil' ? 0.0 : 1.0,
        });
      }
      t += dur;
    }

    return events;
  }

  // ── Private: syllable splitter ──────────────────────────────────────────────

  /**
   * Splits a word into syllables using maximal-onset principle.
   * "balance"  → ["ba", "lance"]
   * "strange"  → ["strange"]
   * "speaking" → ["spea", "king"]
   */
  _splitSyllables(word) {
    const w = word.toLowerCase().replace(/'/g, '');
    if (!w) return [];

    const vowels = /[aeiou]/;
    const syllables = [];
    let current = '';

    for (let i = 0; i < w.length; i++) {
      current += w[i];

      // Look ahead: if next char is a vowel and current ends with consonant(s),
      // and we already have a vowel in current → cut here
      const hasVowel = vowels.test(current);
      const nextIsVowel = i + 1 < w.length && vowels.test(w[i + 1]);
      const nextIsConsonant = i + 1 < w.length && !vowels.test(w[i + 1]);
      const afterNextIsVowel = i + 2 < w.length && vowels.test(w[i + 2]);

      if (hasVowel && nextIsConsonant && afterNextIsVowel) {
        // VCCV → split after first C: "bal-ance"
        syllables.push(current);
        current = '';
      } else if (hasVowel && nextIsVowel) {
        // VV — split between two vowels unless diphthong pair
        const diphthongs = ['ai','au','ay','ea','ee','ei','eu','ew','ey','ie','oa','oe','oi','oo','ou','ow','oy','ua','ue','ui','uo','uy'];
        const pair = w[i] + w[i + 1];
        if (!diphthongs.includes(pair)) {
          syllables.push(current);
          current = '';
        }
      }
    }

    if (current) syllables.push(current);
    return syllables.length > 0 ? syllables : [w];
  }

  // ── Private: syllable → phoneme sequence ───────────────────────────────────

  /**
   * Converts a syllable into an ordered sequence of viseme events with
   * relative durations.
   *
   * Structure: [onset] [nucleus] [coda]
   *   onset:   consonant cluster before the vowel  → 25% of syllable time
   *   nucleus: the vowel (longest, most visible)   → 55% of syllable time
   *   coda:    consonant(s) after the vowel        → 20% of syllable time
   *
   * For no-vowel syllables (mm, ng, hmm) the whole syllable is one event.
   */
  _syllableToPhonemeSequence(syllable) {
    const s = syllable.toLowerCase();

    // Find the vowel nucleus position
    const vowelMatch = s.match(/[aeiou]+/);
    if (!vowelMatch) {
      // No vowel — treat whole syllable as a consonant cluster
      return [{ viseme: this._consonantClusterViseme(s), relDur: 1.0 }];
    }

    const vowelIdx   = s.indexOf(vowelMatch[0]);
    const onsetStr   = s.slice(0, vowelIdx);
    const nucleusStr = vowelMatch[0];
    const codaStr    = s.slice(vowelIdx + nucleusStr.length);

    const seq = [];

    // Onset
    if (onsetStr) {
      seq.push({
        viseme: this._consonantClusterViseme(onsetStr),
        relDur: 0.25,
      });
    }

    // Nucleus vowel
    seq.push({
      viseme: this._vowelViseme(nucleusStr),
      relDur: onsetStr ? (codaStr ? 0.55 : 0.75) : (codaStr ? 0.80 : 1.0),
    });

    // Coda
    if (codaStr) {
      seq.push({
        viseme: this._consonantClusterViseme(codaStr),
        relDur: 0.20,
      });
    }

    return seq;
  }

  // ── Private: viseme lookup tables ──────────────────────────────────────────

  /**
   * Maps a consonant cluster string to its dominant viseme.
   * Uses digraph-aware matching before single-char fallback.
   */
  _consonantClusterViseme(cluster) {
    const c = cluster.toLowerCase();

    // Digraph priority (must check before single chars)
    if (/^th/.test(c) || /th$/.test(c))  return 'viseme_TH';
    if (/ch|tch/.test(c))                 return 'viseme_CH';
    if (/sh|zh/.test(c))                  return 'viseme_CH';
    if (/ph/.test(c))                     return 'viseme_FF';
    if (/wh/.test(c))                     return 'viseme_U';   // "wh" → rounded lips
    if (/ng|nk/.test(c))                  return 'viseme_kk';  // velar nasal

    // Single-char dominant consonant
    const first = c[0];
    if ('mpb'.includes(first))            return 'viseme_PP';
    if ('fv'.includes(first))             return 'viseme_FF';
    if ('sz'.includes(first))             return 'viseme_SS';
    if ('r'.includes(first))              return 'viseme_RR';
    if ('nl'.includes(first))             return 'viseme_nn';
    if ('kg'.includes(first))             return 'viseme_kk';
    if ('j'.includes(first))              return 'viseme_CH';
    if ('tdnl'.includes(first))           return 'viseme_DD';
    if ('w'.includes(first))              return 'viseme_U';
    if ('h'.includes(first))              return 'viseme_aa'; // aspirate → open mouth
    if ('y'.includes(first))              return 'viseme_I';  // palatal glide

    return 'viseme_DD'; // safe fallback (tongue tip)
  }

  /**
   * Maps a vowel string (may be diphthong) to a viseme.
   */
  _vowelViseme(vowel) {
    const v = vowel.toLowerCase();

    // Diphthongs first
    if (['ai','ay','ei','ey'].includes(v))         return 'viseme_aa'; // start open
    if (['au','aw'].includes(v))                   return 'viseme_O';
    if (['ou','ow'].includes(v))                   return 'viseme_U';  // "you","now" → rounded
    if (['oi','oy'].includes(v))                   return 'viseme_O';
    if (['oo','ue','ew','ui'].includes(v))         return 'viseme_U';
    if (['ea','ee','ie','ei'].includes(v))         return 'viseme_E';
    if (['oa','oe'].includes(v))                   return 'viseme_O';

    // Monophthongs
    switch (v[0]) {
      case 'a': return 'viseme_aa';
      case 'e': return 'viseme_E';
      case 'i': return 'viseme_I';
      case 'o': return 'viseme_O';
      case 'u': return 'viseme_U';
    }

    return 'viseme_aa'; // fallback: open mouth
  }

  // ── Private: helpers ────────────────────────────────────────────────────────

  _resetTargets() {
    // Set all viseme targets to 0 — avatar returns to neutral mouth position naturally
    Object.keys(this.targets).forEach(k => { this.targets[k] = 0.0; });
  }
}
