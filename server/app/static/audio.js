// ── Audio System: Web Audio synthesis + SpeechSynthesis + UCCI→Chinese ──
const AUDIO_KEY = 'chessArenaAudioSettings';

function audioCfg() {
  try { return JSON.parse(localStorage.getItem(AUDIO_KEY) || '{}'); } catch { return {}; }
}
function saveAudioCfg(c) {
  localStorage.setItem(AUDIO_KEY, JSON.stringify(c));
}

// Default settings
let sfxEnabled = true;
let voiceEnabled = true;
let botSpeechVoiceEnabled = true;

(function initAudioCfg() {
  const c = audioCfg();
  if (c.sfx !== undefined) sfxEnabled = c.sfx;
  else if (c.sfxEnabled !== undefined) sfxEnabled = c.sfxEnabled;
  if (c.voice !== undefined) voiceEnabled = c.voice;
  else if (c.voiceEnabled !== undefined) voiceEnabled = c.voiceEnabled;
  if (c.botSpeechVoice !== undefined) botSpeechVoiceEnabled = c.botSpeechVoice;
})();

function persistAudioCfg() {
  saveAudioCfg({ sfx: sfxEnabled, voice: voiceEnabled, botSpeechVoice: botSpeechVoiceEnabled });
}

// ── Web Audio Engine ──
let audioCtx = null;
let audioUnlocked = false;
function unlockAudio() {
  audioUnlocked = true;
  const ctx = getCtx();
  if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {});
}
function getCtx() {
  if (!audioUnlocked) return null;
  if (!audioCtx) {
    try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { return null; }
  }
  if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  return audioCtx;
}

function playTone(freq, duration, type, vol, rampDown) {
  if (!sfxEnabled) return;
  const ctx = getCtx();
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type || 'sine';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(vol || 0.3, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + (rampDown || duration));
  osc.connect(gain);
  gain.connect(ctx.destination);
  osc.start(ctx.currentTime);
  osc.stop(ctx.currentTime + duration);
}

function playNoise(duration, vol) {
  if (!sfxEnabled) return;
  const ctx = getCtx();
  if (!ctx) return;
  const bufferSize = ctx.sampleRate * duration;
  const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < bufferSize; i++) data[i] = Math.random() * 2 - 1;
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  const gain = ctx.createGain();
  const filter = ctx.createBiquadFilter();
  filter.type = 'highpass';
  filter.frequency.value = 1000;
  gain.gain.setValueAtTime(vol || 0.15, ctx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
  source.connect(filter);
  filter.connect(gain);
  gain.connect(ctx.destination);
  source.start(ctx.currentTime);
  source.stop(ctx.currentTime + duration);
}

// ── Sound Effects ──
const SoundFX = {
  move() {
    if (!sfxEnabled) return;
    const ctx = getCtx();
    if (!ctx) return;
    // Short crisp "click" – high frequency sine + tiny noise burst
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(1800, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(800, ctx.currentTime + 0.06);
    gain.gain.setValueAtTime(0.18, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.08);
    // Subtle noise for texture
    playNoise(0.04, 0.04);
  },

  capture() {
    if (!sfxEnabled) return;
    const ctx = getCtx();
    if (!ctx) return;
    // Strong impact: low thump + mid click
    const osc1 = ctx.createOscillator();
    const g1 = ctx.createGain();
    osc1.type = 'triangle';
    osc1.frequency.setValueAtTime(150, ctx.currentTime);
    osc1.frequency.exponentialRampToValueAtTime(60, ctx.currentTime + 0.12);
    g1.gain.setValueAtTime(0.4, ctx.currentTime);
    g1.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
    osc1.connect(g1);
    g1.connect(ctx.destination);
    osc1.start(ctx.currentTime);
    osc1.stop(ctx.currentTime + 0.15);
    // Higher crack
    const osc2 = ctx.createOscillator();
    const g2 = ctx.createGain();
    osc2.type = 'square';
    osc2.frequency.setValueAtTime(600, ctx.currentTime);
    osc2.frequency.exponentialRampToValueAtTime(200, ctx.currentTime + 0.06);
    g2.gain.setValueAtTime(0.12, ctx.currentTime);
    g2.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.08);
    osc2.connect(g2);
    g2.connect(ctx.destination);
    osc2.start(ctx.currentTime);
    osc2.stop(ctx.currentTime + 0.08);
    playNoise(0.06, 0.1);
  },

  check() {
    if (!sfxEnabled) return;
    const ctx = getCtx();
    if (!ctx) return;
    // Two-tone warning beep
    [800, 600].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'square';
      osc.frequency.value = freq;
      const t = ctx.currentTime + i * 0.15;
      gain.gain.setValueAtTime(0, t);
      gain.gain.linearRampToValueAtTime(0.12, t + 0.02);
      gain.gain.setValueAtTime(0.12, t + 0.1);
      gain.gain.exponentialRampToValueAtTime(0.001, t + 0.14);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.15);
    });
  },

  win() {
    if (!sfxEnabled) return;
    // Ascending arpeggio: C-E-G-C
    [523, 659, 784, 1047].forEach((freq, i) => {
      setTimeout(() => playTone(freq, 0.3, 'sine', 0.2, 0.25), i * 120);
    });
  },

  lose() {
    if (!sfxEnabled) return;
    // Descending: G-E-C
    [784, 659, 523].forEach((freq, i) => {
      setTimeout(() => playTone(freq, 0.35, 'triangle', 0.18, 0.3), i * 150);
    });
  },

  draw() {
    if (!sfxEnabled) return;
    // Neutral: two even tones
    setTimeout(() => playTone(440, 0.2, 'sine', 0.15, 0.15), 0);
    setTimeout(() => playTone(440, 0.2, 'sine', 0.15, 0.15), 250);
  }
};

// ── UCCI → Chinese Chess Notation ──
const UCCI_PIECE = {
  a: '車', b: '馬', c: '象', d: '士', e: '将', f: '砲', g: '卒',
  A: '車', B: '馬', C: '象', D: '士', E: '将', F: '砲', G: '卒'
};
const UCCI_PIECE_RED = {
  a: '車', b: '馬', c: '相', d: '仕', e: '帅', f: '炮', g: '兵',
  A: '車', B: '馬', C: '相', D: '仕', E: '帅', F: '炮', G: '兵'
};
const CHINESE_NUMS = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
const CHINESE_NUMS10 = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'];

// UCCI format: piece(fromCol)(fromRow)(toCol)(toRow)
// Columns a-i (0-8), Rows 0-9
function ucciToChinese(move, side) {
  if (!move || move.length < 5) return move || '';
  const piece = move[0];
  const fromCol = move.charCodeAt(1) - 97; // a=0, i=8
  const fromRow = parseInt(move[2], 10);
  const toCol = move.charCodeAt(3) - 97;
  const toRow = parseInt(move[4], 10);

  const pieceMap = side === 'red' ? UCCI_PIECE_RED : UCCI_PIECE;
  const pieceName = pieceMap[piece] || piece;
  const linearPieces = 'aAfFeEgG'; // 車, 砲/炮, 帅/将, 兵/卒 — moves in straight lines
  const isLinear = linearPieces.indexOf(piece) !== -1;

  // Red: columns numbered 1-9 from RIGHT (a=9, i=1)
  // Black: columns numbered 1-9 from LEFT (a=1, i=9)
  let fromColNum, toColNum;
  if (side === 'red') {
    fromColNum = 9 - fromCol;
    toColNum = 9 - toCol;
  } else {
    fromColNum = fromCol + 1;
    toColNum = toCol + 1;
  }

  // Determine advance (进) or retreat (退)
  // Red moves up (row decreases), Black moves down (row increases)
  let isAdvance;
  if (side === 'red') {
    isAdvance = fromRow > toRow;
  } else {
    isAdvance = toRow > fromRow;
  }

  const colNumStr = CHINESE_NUMS[fromColNum];

  if (fromCol === toCol) {
    // Vertical move
    const steps = Math.abs(toRow - fromRow);
    if (isLinear) {
      return pieceName + colNumStr + (isAdvance ? '进' : '退') + CHINESE_NUMS[steps];
    } else {
      // Diagonal pieces (馬, 象, 士) — should rarely reach here, but handle
      return pieceName + colNumStr + (isAdvance ? '进' : '退') + CHINESE_NUMS[toColNum];
    }
  } else {
    // Horizontal or diagonal move
    if (isLinear) {
      return pieceName + colNumStr + '平' + CHINESE_NUMS[toColNum];
    } else {
      return pieceName + colNumStr + (isAdvance ? '进' : '退') + CHINESE_NUMS[toColNum];
    }
  }
}

// ── Speech Synthesis ──
let speechQueue = [];
let speaking = false;
let chineseVoices = [];
let speechUnlocked = false;

function markSpeechUnlocked() {
  speechUnlocked = true;
}

function warmupSpeech() {
  if (!window.speechSynthesis) return;
  markSpeechUnlocked();
  try {
    const u = new SpeechSynthesisUtterance('');
    u.volume = 0;
    u.lang = 'zh-CN';
    window.speechSynthesis.speak(u);
  } catch (e) {}
}

// WeChat/Chrome block speech/audio before a user gesture.  Default switches can
// be ON visually, but real playback only becomes reliable after the first tap.
if (typeof document !== 'undefined') {
  ['pointerdown', 'touchstart', 'keydown'].forEach(evt => {
    document.addEventListener(evt, warmupSpeech, { once: true, passive: true });
  });
}

function refreshChineseVoices() {
  if (!window.speechSynthesis) return [];
  const voices = window.speechSynthesis.getVoices() || [];
  chineseVoices = voices.filter(v => /^zh(-|_|$)/i.test(v.lang || ''));
  chineseVoices.sort((a, b) => {
    const acn = /^zh(-|_)CN/i.test(a.lang || '') ? 0 : 1;
    const bcn = /^zh(-|_)CN/i.test(b.lang || '') ? 0 : 1;
    return acn - bcn;
  });
  return chineseVoices;
}

if (window.speechSynthesis) {
  refreshChineseVoices();
  window.speechSynthesis.onvoiceschanged = refreshChineseVoices;
}

function pickChineseVoice() {
  const voices = chineseVoices.length ? chineseVoices : refreshChineseVoices();
  if (!voices.length) return null;
  const zhCn = voices.filter(v => /^zh(-|_)CN/i.test(v.lang || ''));
  const pool = zhCn.length ? zhCn : voices;
  return pool[Math.floor(Math.random() * pool.length)] || null;
}

function randomPitch() {
  return 0.9 + Math.random() * 0.25;
}

function speakText(text, opts) {
  if (!text || !window.speechSynthesis) {
    if (opts && typeof opts.onend === 'function') opts.onend();
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.rate = opts && opts.rate ? opts.rate : 0.9;
  utterance.pitch = opts && opts.pitch ? opts.pitch : randomPitch();
  utterance.volume = 0.9;
  const voice = pickChineseVoice();
  if (voice) utterance.voice = voice;
  let done = false;
  const finish = () => {
    if (done) return;
    done = true;
    if (opts && typeof opts.onend === 'function') opts.onend();
  };
  utterance.onend = finish;
  utterance.onerror = finish;
  window.speechSynthesis.speak(utterance);
  // Some browsers/WebView implementations silently never fire onend/onerror
  // when speech is blocked before user interaction. Never let this block board rendering.
  const timeoutMs = Math.max(1200, Math.min(8000, String(text).length * 180));
  setTimeout(finish, timeoutMs);
}

function processSpeechQueue() {
  if (speaking || speechQueue.length === 0) return;
  speaking = true;
  const text = speechQueue.shift();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = 'zh-CN';
  utterance.rate = 0.85;
  utterance.pitch = 1.0;
  utterance.volume = 0.9;
  utterance.onend = () => {
    speaking = false;
    setTimeout(() => processSpeechQueue(), 100);
  };
  utterance.onerror = () => {
    speaking = false;
    setTimeout(() => processSpeechQueue(), 100);
  };
  window.speechSynthesis.speak(utterance);
}

function speakChinese(text) {
  if (!voiceEnabled || !text) return;
  // Cancel any pending speech
  window.speechSynthesis.cancel();
  speechQueue = [];
  speakText(text, { rate: 0.9 });
}

// ── High-level helpers ──
function announceMove(ucci, side, captured, isCheck) {
  if (!captured && !isCheck) return;
  const cn = ucciToChinese(ucci, side);
  const sideName = side === 'red' ? '红方' : '黑方';
  let text = sideName + cn;
  if (captured && isCheck) text += '，吃子并将军';
  else if (captured) text += '，吃子';
  else if (isCheck) text += '，将军';
  speakChinese(text);
}

function onMoveEvent(moveUcci, side, captured, isCheck) {
  if (captured) {
    SoundFX.capture();
  } else {
    SoundFX.move();
  }
  if (isCheck) {
    setTimeout(() => SoundFX.check(), 250);
  }
  announceMove(moveUcci, side, captured, isCheck);
}

function speakBotSpeech(move, onDone) {
  if (!botSpeechVoiceEnabled || !move || !move.bot_speech) {
    if (typeof onDone === 'function') onDone();
    return;
  }
  window.speechSynthesis.cancel();
  speakText(move.bot_speech, { rate: 0.95, onend: onDone });
}

function onGameEnd(result) {
  if (result === '红胜' || result === 'red') {
    SoundFX.win();
    speakChinese('红方胜利');
  } else if (result === '黑胜' || result === 'black') {
    SoundFX.lose();
    speakChinese('黑方胜利');
  } else if (result === '和棋' || result === 'draw') {
    SoundFX.draw();
    speakChinese('双方和棋');
  } else {
    SoundFX.draw();
    speakChinese('对局结束');
  }
}

// Detect if a move is a check by examining FEN
// In Chinese chess FEN, we can't easily detect check from just FEN.
// Instead we look for common check patterns or rely on comment text.
function detectCheckFromComment(comment) {
  if (!comment) return false;
  return /将军|將军|check|将一军/.test(comment);
}

// ── Audio Toggle UI ──
function createAudioToggles(containerEl) {
  if (!containerEl) return;
  const div = document.createElement('div');
  div.className = 'audio-toggles';
  div.innerHTML =
    '<button id="sfxToggle" class="audio-btn" title="音效开关">' +
    (sfxEnabled ? '🔊 音效' : '🔇 音效') +
    '</button>' +
    '<button id="voiceToggle" class="audio-btn" title="语音播报开关">' +
    (voiceEnabled ? '📢 播报' : '🔕 播报') +
    '</button>' +
    '<button id="botSpeechToggle" class="audio-btn" title="Bot 台词语音开关">' +
    (botSpeechVoiceEnabled ? '💬 Bot语音 开' : '💬 Bot语音 关') +
    '</button>';
  containerEl.appendChild(div);

  document.getElementById('sfxToggle').onclick = function () {
    sfxEnabled = !sfxEnabled;
    this.textContent = sfxEnabled ? '🔊 音效' : '🔇 音效';
    if (sfxEnabled) {
      // User click unlocks Web Audio for later SSE move sounds.
      unlockAudio();
      this.classList.remove('off');
    } else {
      this.classList.add('off');
    }
    persistAudioCfg();
  };

  document.getElementById('voiceToggle').onclick = function () {
    voiceEnabled = !voiceEnabled;
    this.textContent = voiceEnabled ? '📢 播报' : '🔕 播报';
    if (voiceEnabled) {
      this.classList.remove('off');
      warmupSpeech();
    } else {
      this.classList.add('off');
      window.speechSynthesis.cancel();
    }
    persistAudioCfg();
  };

  document.getElementById('botSpeechToggle').onclick = function () {
    botSpeechVoiceEnabled = !botSpeechVoiceEnabled;
    this.textContent = botSpeechVoiceEnabled ? '💬 Bot语音 开' : '💬 Bot语音 关';
    this.classList.toggle('off', !botSpeechVoiceEnabled);
    if (botSpeechVoiceEnabled) {
      warmupSpeech();
    } else {
      window.speechSynthesis.cancel();
    }
    persistAudioCfg();
  };

  // Apply initial off state
  if (!sfxEnabled) document.getElementById('sfxToggle').classList.add('off');
  if (!voiceEnabled) document.getElementById('voiceToggle').classList.add('off');
  if (!botSpeechVoiceEnabled) document.getElementById('botSpeechToggle').classList.add('off');
}

// Track previous state for detecting changes
let lastPly = -1;
let lastStatus = '';
let lastBotSpeechPly = -1;

function resetAudioState() {
  lastPly = -1;
  lastStatus = '';
  lastBotSpeechPly = -1;
}

function markAudioStateLoaded(ply) {
  const n = Number(ply || 0);
  lastPly = n;
  lastBotSpeechPly = n;
}

function playBotSpeechBeforeRender(d, renderFn) {
  if (!d) {
    renderFn();
    return;
  }
  const ply = Number(d.ply || (d.moves ? d.moves.length : 0));
  const last = d.last_move || (d.moves && d.moves[d.moves.length - 1]);
  if (ply > lastBotSpeechPly && last && last.bot_speech && botSpeechVoiceEnabled) {
    lastBotSpeechPly = ply;
    speakBotSpeech(last, renderFn);
    return;
  }
  renderFn();
}

// Call this when new SSE match_state arrives
function handleSSEAudio(d) {
  if (!d) return;

  if (lastPly < 0) {
    markAudioStateLoaded(d.ply || (d.moves ? d.moves.length : 0));
    lastStatus = d.status || lastStatus;
    return;
  }

  // Detect game end
  if (d.status && d.status !== 'active' && d.status !== lastStatus) {
    lastStatus = d.status;
    if (d.result) onGameEnd(d.result);
    return;
  }
  lastStatus = d.status || lastStatus;

  // Detect new moves
  if (d.moves && d.moves.length > 0) {
    const newPly = d.moves.length;
    if (newPly > lastPly) {
      // Process new moves
      for (let i = lastPly < 0 ? 0 : lastPly; i < newPly; i++) {
        const mv = d.moves[i];
        if (mv && mv.move) {
          const isCheck = mv.check === true || detectCheckFromComment(mv.comment);
          onMoveEvent(mv.move, mv.side, mv.captured, isCheck);
        }
      }
      lastPly = newPly;
    }
  }

  if (d.ply != null) {
    lastPly = d.ply || lastPly;
  }
}
