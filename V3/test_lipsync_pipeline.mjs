/**
 * test_lipsync_pipeline.mjs
 * Run: node test_lipsync_pipeline.mjs
 *
 * Replicates the full pipeline from the new lipsync.js and prints
 * every intermediate stage so you can validate the output.
 */

// ── Replicate the new pipeline logic ──────────────────────────────────────────

function splitSyllables(word) {
  const w = word.toLowerCase().replace(/'/g, '');
  if (!w) return [];

  const vowels = /[aeiou]/;
  const syllables = [];
  let current = '';

  for (let i = 0; i < w.length; i++) {
    current += w[i];
    const hasVowel       = vowels.test(current);
    const nextIsVowel    = i + 1 < w.length && vowels.test(w[i + 1]);
    const nextIsConsonant= i + 1 < w.length && !vowels.test(w[i + 1]);
    const afterNextIsVowel = i + 2 < w.length && vowels.test(w[i + 2]);

    if (hasVowel && nextIsConsonant && afterNextIsVowel) {
      syllables.push(current); current = '';
    } else if (hasVowel && nextIsVowel) {
      const diphthongs = ['ai','au','ay','ea','ee','ei','eu','ew','ey','ie',
                          'oa','oe','oi','oo','ou','ow','oy','ua','ue','ui','uo','uy'];
      const pair = w[i] + w[i + 1];
      if (!diphthongs.includes(pair)) { syllables.push(current); current = ''; }
    }
  }
  if (current) syllables.push(current);
  return syllables.length > 0 ? syllables : [w];
}

function consonantClusterViseme(cluster) {
  const c = cluster.toLowerCase();
  if (/^th/.test(c) || /th$/.test(c))  return 'viseme_TH';
  if (/ch|tch/.test(c))                return 'viseme_CH';
  if (/sh|zh/.test(c))                 return 'viseme_CH';
  if (/ph/.test(c))                    return 'viseme_FF';
  if (/wh/.test(c))                    return 'viseme_U';
  if (/ng|nk/.test(c))                 return 'viseme_kk';
  const first = c[0];
  if ('mpb'.includes(first))           return 'viseme_PP';
  if ('fv'.includes(first))            return 'viseme_FF';
  if ('sz'.includes(first))            return 'viseme_SS';
  if ('r'.includes(first))             return 'viseme_RR';
  if ('nl'.includes(first))            return 'viseme_nn';
  if ('kg'.includes(first))            return 'viseme_kk';
  if ('j'.includes(first))             return 'viseme_CH';
  if ('tdnl'.includes(first))          return 'viseme_DD';
  if ('w'.includes(first))             return 'viseme_U';
  if ('h'.includes(first))             return 'viseme_aa';
  if ('y'.includes(first))             return 'viseme_I';
  return 'viseme_DD';
}

function vowelViseme(vowel) {
  const v = vowel.toLowerCase();
  if (['ai','ay','ei','ey'].includes(v))        return 'viseme_aa';
  if (['au','aw'].includes(v))         return 'viseme_O';
  if (['ou','ow'].includes(v))         return 'viseme_U';  // "you","now" → rounded
  if (['oi','oy'].includes(v))         return 'viseme_O';
  if (['oo','ue','ew','ui'].includes(v))         return 'viseme_U';
  if (['ea','ee','ie'].includes(v))              return 'viseme_E';
  if (['oa','oe'].includes(v))                   return 'viseme_O';
  switch (v[0]) {
    case 'a': return 'viseme_aa';
    case 'e': return 'viseme_E';
    case 'i': return 'viseme_I';
    case 'o': return 'viseme_O';
    case 'u': return 'viseme_U';
  }
  return 'viseme_aa';
}

function syllableToPhonemeSequence(syllable) {
  const s = syllable.toLowerCase();
  const vowelMatch = s.match(/[aeiou]+/);
  if (!vowelMatch) {
    return [{ viseme: consonantClusterViseme(s), relDur: 1.0, part: 'no-vowel' }];
  }
  const vowelIdx   = s.indexOf(vowelMatch[0]);
  const onsetStr   = s.slice(0, vowelIdx);
  const nucleusStr = vowelMatch[0];
  const codaStr    = s.slice(vowelIdx + nucleusStr.length);

  const seq = [];
  if (onsetStr) {
    seq.push({ viseme: consonantClusterViseme(onsetStr), relDur: 0.25, part: `onset:"${onsetStr}"` });
  }
  seq.push({
    viseme: vowelViseme(nucleusStr),
    relDur: onsetStr ? (codaStr ? 0.55 : 0.75) : (codaStr ? 0.80 : 1.0),
    part: `nucleus:"${nucleusStr}"`,
  });
  if (codaStr) {
    seq.push({ viseme: consonantClusterViseme(codaStr), relDur: 0.20, part: `coda:"${codaStr}"` });
  }
  return seq;
}

function textToVisemeEvents(text, startTime = 0, totalDuration = 3.0) {
  const clean = text.replace(/[^a-zA-Z'\s]/g, ' ').replace(/\s+/g, ' ').trim();
  if (!clean) return [];

  const words = clean.split(' ').filter(w => w.length > 0);
  const allSeqs = [];

  for (const word of words) {
    const syllables = splitSyllables(word);
    for (const syl of syllables) {
      allSeqs.push(...syllableToPhonemeSequence(syl).map(s => ({ ...s, syllable: syl, word })));
    }
    allSeqs.push({ viseme: 'viseme_sil', relDur: 0.15, part: 'word-gap', syllable: '', word });
  }

  const totalRel = allSeqs.reduce((s, e) => s + e.relDur, 0);
  const scale    = totalDuration / totalRel;

  const events = [];
  let t = startTime;
  for (const item of allSeqs) {
    const dur = item.relDur * scale;
    if (dur > 0.008) {
      events.push({ ...item, startTime: t, endTime: t + dur, duration: dur });
    }
    t += dur;
  }
  return events;
}

// ── Test sentences ─────────────────────────────────────────────────────────────

const TESTS = [
  { label: 'English basic',         text: 'Hello, how are you doing today?',        dur: 2.5 },
  { label: 'English banking',       text: 'I cannot access your bank balance.',      dur: 2.0 },
  { label: 'English weather',       text: 'The weather is really nice outside.',     dur: 2.2 },
  { label: 'Romanized Tamil',       text: 'Vanakkam naan ungal AI udhaviyaalar.',    dur: 2.5 },
  { label: 'Romanized Hindi',       text: 'Namaste main aapka AI sahayak hoon.',     dur: 2.5 },
  { label: 'Digraphs / clusters',   text: 'Thank you for choosing this service.',    dur: 2.0 },
  { label: 'Diphthongs',            text: 'The rain in Spain stays mainly outside.', dur: 2.5 },
  { label: 'No-vowel words',        text: 'mm hmm okay',                             dur: 1.0 },
  { label: 'Short common words',    text: 'Yes no please stop go',                   dur: 1.5 },
];

for (const test of TESTS) {
  console.log('\n' + '═'.repeat(72));
  console.log(`  [${test.label}]`);
  console.log(`  INPUT: "${test.text}"`);
  console.log('─'.repeat(72));

  const words = test.text.replace(/[^a-zA-Z'\s]/g, ' ').trim().split(/\s+/).filter(Boolean);

  // Stage 1: graphemes
  console.log(`\n  STAGE 1 — Graphemes:`);
  console.log('  ' + test.text.split('').map(c => c === ' ' ? '·' : c).join(' '));

  // Stage 2: syllables
  console.log(`\n  STAGE 2 — Syllables:`);
  for (const word of words) {
    const syls = splitSyllables(word);
    console.log(`    "${word.toLowerCase()}" → [${syls.map(s=>`"${s}"`).join(', ')}]`);
  }

  // Stage 3: phoneme sequence per syllable
  console.log(`\n  STAGE 3 — Phoneme sequences (onset | nucleus | coda):`);
  for (const word of words) {
    const syls = splitSyllables(word);
    for (const syl of syls) {
      const seq = syllableToPhonemeSequence(syl);
      const parts = seq.map(s => `${s.part}→${s.viseme.replace('viseme_','')}(${s.relDur.toFixed(2)})`).join('  ');
      console.log(`    "${syl}" : ${parts}`);
    }
  }

  // Stage 4: timed viseme events
  const events = textToVisemeEvents(test.text, 0, test.dur);
  console.log(`\n  STAGE 4 — Timed viseme events (${events.length} events over ${test.dur}s):`);
  console.log('  ' + 'word'.padEnd(14) + 'syllable'.padEnd(10) + 'part'.padEnd(22) +
              'viseme'.padEnd(14) + 'start'.padEnd(8) + 'end'.padEnd(8) + 'dur');
  console.log('  ' + '─'.repeat(80));
  for (const e of events) {
    const wrd  = (e.word  || '').padEnd(14);
    const syl  = (e.syllable || '').padEnd(10);
    const part = (e.part || '').padEnd(22);
    const vis  = e.viseme.replace('viseme_', '').padEnd(14);
    const t0   = e.startTime.toFixed(3).padEnd(8);
    const t1   = e.endTime.toFixed(3).padEnd(8);
    const dur  = e.duration.toFixed(3);
    console.log(`  ${wrd}${syl}${part}${vis}${t0}${t1}${dur}`);
  }

  // Summary: unique visemes activated
  const unique = [...new Set(events.filter(e => e.viseme !== 'viseme_sil').map(e => e.viseme))];
  console.log(`\n  ACTIVATED visemes (${unique.length}): ${unique.map(v=>v.replace('viseme_','')).join(', ')}`);
  const silPct = (events.filter(e=>e.viseme==='viseme_sil').reduce((s,e)=>s+e.duration,0) / test.dur * 100).toFixed(1);
  console.log(`  Silence fraction: ${silPct}%`);
}

// ── Regression tests ──────────────────────────────────────────────────────────
console.log('\n' + '═'.repeat(72));
console.log('  REGRESSION TESTS — consonant clusters & diphthongs');
console.log('═'.repeat(72));

const regressionCases = [
  // [input, expected_onset_viseme, expected_nucleus_viseme]
  ['the',     'viseme_TH',  'viseme_E'],
  ['she',     'viseme_CH',  'viseme_E'],
  ['three',   'viseme_TH',  'viseme_E'],
  ['phone',   'viseme_FF',  'viseme_O'],
  ['what',    'viseme_U',   'viseme_aa'],
  ['sing',    'viseme_SS',  'viseme_I'],
  ['king',    'viseme_kk',  'viseme_I'],
  ['rain',    'viseme_RR',  'viseme_aa'],
  ['pain',    'viseme_PP',  'viseme_aa'],
  ['main',    'viseme_PP',  'viseme_aa'],
  ['day',     'viseme_DD',  'viseme_aa'],
  ['say',     'viseme_SS',  'viseme_aa'],
  ['you',     'viseme_I',   'viseme_U'],
  ['no',      'viseme_nn',  'viseme_O'],
  ['go',      'viseme_kk',  'viseme_O'],
  ['free',    'viseme_FF',  'viseme_E'],
  ['tree',    'viseme_DD',  'viseme_E'],
  ['va',      'viseme_FF',  'viseme_aa'],
  ['na',      'viseme_nn',  'viseme_aa'],
  ['ka',      'viseme_kk',  'viseme_aa'],
  ['ha',      'viseme_aa',  'viseme_aa'],
];

let passed = 0; let failed = 0;
for (const [word, expOnset, expNucleus] of regressionCases) {
  const seq = syllableToPhonemeSequence(word);
  const onset   = seq.find(s => s.part?.startsWith('onset'))?.viseme   || null;
  const nucleus = seq.find(s => s.part?.startsWith('nucleus'))?.viseme || seq[0]?.viseme;
  const oOk = expOnset   ? onset   === expOnset   : true;
  const nOk = expNucleus ? nucleus === expNucleus : true;

  if (oOk && nOk) {
    console.log(`  ✅ "${word}"`);
    passed++;
  } else {
    const got = seq.map(s=>`${s.part}→${s.viseme.replace('viseme_','')}`).join(' | ');
    console.log(`  ❌ "${word}"  got: ${got}`);
    if (!oOk) console.log(`       onset:   expected ${expOnset?.replace('viseme_','')}  got ${onset?.replace('viseme_','')}`);
    if (!nOk) console.log(`       nucleus: expected ${expNucleus?.replace('viseme_','')}  got ${nucleus?.replace('viseme_','')}`);
    failed++;
  }
}
console.log(`\n  Result: ${passed} passed, ${failed} failed out of ${regressionCases.length}`);
