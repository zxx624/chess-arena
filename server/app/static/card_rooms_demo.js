const API_BASE = '/api/card-rooms';
const POOL_API = `${API_BASE}/pool`;
const DEFAULT_SEAT_IDS = ['seat0', 'seat1', 'seat2'];
const urlRoomId = new URLSearchParams(window.location.search).get('room_id') || '';

let currentRoomId = urlRoomId || localStorage.getItem('cardRoomAlphaRoomId') || '';
let currentRoom = null;
let currentSpectator = null;
let busy = false;

const els = {
  newRoomBtn: document.getElementById('newRoomBtn'),
  stepBtn: document.getElementById('stepBtn'),
  autoRunBtn: document.getElementById('autoRunBtn'),
  refreshBtn: document.getElementById('refreshBtn'),
  roomIdText: document.getElementById('roomIdText'),
  gameText: document.getElementById('gameText'),
  phaseText: document.getElementById('phaseText'),
  winnerText: document.getElementById('winnerText'),
  landlordText: document.getElementById('landlordText'),
  currentSeatText: document.getElementById('currentSeatText'),
  passCountText: document.getElementById('passCountText'),
  lastPlayText: document.getElementById('lastPlayText'),
  bottomCards: document.getElementById('bottomCards'),
  messageText: document.getElementById('messageText'),
  llmSeatSelect: document.getElementById('llmSeatSelect'),
  llmActionInput: document.getElementById('llmActionInput'),
  seatViewBtn: document.getElementById('seatViewBtn'),
  legalActionsBtn: document.getElementById('legalActionsBtn'),
  submitLlmActionBtn: document.getElementById('submitLlmActionBtn'),
  passActionBtn: document.getElementById('passActionBtn'),
  llmResultBox: document.getElementById('llmResultBox'),
  llmResultText: document.getElementById('llmResultText'),
  historyCount: document.getElementById('historyCount'),
  historyList: document.getElementById('historyList'),
  spectatorStatus: document.getElementById('spectatorStatus'),
  spectatorHands: document.getElementById('spectatorHands'),
  poolSlots: document.getElementById('poolSlots'),
  poolRefreshBtn: document.getElementById('poolRefreshBtn'),
  poolNameInput: document.getElementById('poolNameInput'),
  poolTokenInput: document.getElementById('poolTokenInput'),
  poolControllerText: document.getElementById('poolControllerText'),
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[char]));
}

function seatKey(value) {
  if (value === null || value === undefined || value === '') return '';
  const text = String(value);
  return text.startsWith('seat') ? text : `seat${text}`;
}

function seatNumber(value) {
  const key = seatKey(value);
  const match = key.match(/seat(\d+)/);
  return match ? Number(match[1]) : 0;
}

function setMessage(message, isError = false) {
  if (!els.messageText) return;
  els.messageText.textContent = message || '';
  els.messageText.classList.toggle('is-error', Boolean(isError));
  els.messageText.classList.toggle('is-success', Boolean(message) && !isError);
}

function allButtons() {
  return [
    els.newRoomBtn,
    els.stepBtn,
    els.autoRunBtn,
    els.refreshBtn,
    els.seatViewBtn,
    els.legalActionsBtn,
    els.submitLlmActionBtn,
    els.passActionBtn,
    els.poolRefreshBtn,
  ].filter(Boolean);
}

function setBusy(nextBusy, activeButton = null, loadingText = '处理中…') {
  busy = nextBusy;
  allButtons().forEach((button) => {
    if (!button.dataset.idleText) button.dataset.idleText = button.textContent;
    button.disabled = busy;
    button.classList.toggle('is-loading', busy && button === activeButton);
    button.setAttribute('aria-busy', busy && button === activeButton ? 'true' : 'false');
    button.textContent = busy && button === activeButton ? loadingText : button.dataset.idleText;
  });
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      Accept: 'application/json',
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch (error) {
      payload = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = payload?.detail || payload?.message || `${response.status} ${response.statusText}`;
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    error.status = response.status;
    error.payload = payload || {};
    throw error;
  }
  return payload || {};
}


function controllerId() {
  let value = localStorage.getItem('cardRoomPoolControllerId') || '';
  if (!value) {
    value = `web_${Math.random().toString(36).slice(2)}_${Date.now().toString(36)}`;
    localStorage.setItem('cardRoomPoolControllerId', value);
  }
  if (els.poolControllerText) els.poolControllerText.textContent = value;
  return value;
}

function poolDisplayName() {
  const inputValue = (els.poolNameInput?.value || '').trim();
  if (inputValue) {
    localStorage.setItem('cardRoomPoolDisplayName', inputValue);
    return inputValue;
  }
  const stored = localStorage.getItem('cardRoomPoolDisplayName') || '';
  if (stored && els.poolNameInput) els.poolNameInput.value = stored;
  return stored || `游客${controllerId().slice(-4)}`;
}

function poolBotToken() {
  return (els.poolTokenInput?.value || '').trim();
}

function humanError(error) {
  const detail = error?.payload?.detail || error?.message || '请求失败';
  if (typeof detail === 'string') return detail;
  if (detail?.message) return String(detail.message);
  if (detail?.code) return String(detail.code);
  return '请求失败';
}

function setCurrentRoomId(roomId, options = {}) {
  currentRoomId = String(roomId || '').trim();
  if (currentRoomId) {
    localStorage.setItem('cardRoomAlphaRoomId', currentRoomId);
    if (options.updateUrl && window.history?.replaceState) {
      const url = new URL(window.location.href);
      url.searchParams.set('room_id', currentRoomId);
      window.history.replaceState({}, '', url);
    }
  }
}

function poolStatusLabel(status) {
  return {
    waiting: '等待中',
    playing: '进行中',
    finished: '已结束',
  }[status] || status || '-';
}

function renderPoolSeat(seat) {
  const label = seat?.display_name || seat?.controller_id || '空位';
  const idx = Number.isFinite(Number(seat?.seat)) ? Number(seat.seat) : '-';
  return `<span class="cr-pool-seat is-filled"><b>seat${idx}</b>${escapeHtml(label)}</span>`;
}

function renderEmptyPoolSeat(index) {
  return `<span class="cr-pool-seat"><b>seat${index}</b>等待加入</span>`;
}

function renderPool(payload = {}) {
  const slots = Array.isArray(payload.slots) ? payload.slots : [];
  if (!els.poolSlots) return;
  if (!slots.length) {
    els.poolSlots.innerHTML = '<div class="cr-empty">房间池暂无数据。</div>';
    return;
  }
  els.poolSlots.innerHTML = slots.map((slot) => {
    const seats = Array.isArray(slot.seats) ? slot.seats : [];
    const seatHtml = [0, 1, 2].map((index) => {
      const seat = seats.find((item) => Number(item.seat) === index);
      return seat ? renderPoolSeat(seat) : renderEmptyPoolSeat(index);
    }).join('');
    const roomButton = slot.room_id
      ? `<button class="btn primary" type="button" data-pool-action="open" data-room-id="${escapeHtml(slot.room_id)}">打开牌桌</button>`
      : '';
    const startButton = slot.can_start
      ? `<button class="btn" type="button" data-pool-action="start" data-slot="${slot.slot}">手动开局</button>`
      : '';
    const joinButton = `<button class="btn" type="button" data-pool-action="join" data-slot="${slot.slot}"${slot.can_join ? '' : ' disabled'}>入座</button>`;
    const leaveButton = `<button class="btn" type="button" data-pool-action="leave" data-slot="${slot.slot}">退出</button>`;
    const resetButton = `<button class="btn danger" type="button" data-pool-action="reset" data-slot="${slot.slot}">清空</button>`;
    return `<article class="cr-pool-card status-${escapeHtml(slot.status)}">
      <div class="cr-pool-head">
        <div><span>房间 ${slot.slot}</span><strong>${escapeHtml(poolStatusLabel(slot.status))}</strong></div>
        <em>${slot.occupied || 0}/${slot.capacity || 3}</em>
      </div>
      <div class="cr-pool-seats">${seatHtml}</div>
      <div class="cr-pool-meta">${slot.room_id ? `room_id：<code>${escapeHtml(slot.room_id)}</code>` : '满 3 人自动开局，随机地主。'}</div>
      <div class="cr-pool-actions">${roomButton}${startButton}${joinButton}${leaveButton}${resetButton}</div>
    </article>`;
  }).join('');
}

async function refreshPool(activeButton = els.poolRefreshBtn) {
  if (!els.poolSlots) return;
  setBusy(true, activeButton, '刷新中…');
  try {
    const payload = await requestJson(POOL_API);
    renderPool(payload);
  } catch (error) {
    els.poolSlots.innerHTML = `<div class="cr-empty">房间池加载失败：${escapeHtml(error.message)}</div>`;
  } finally {
    setBusy(false);
  }
}

async function joinPoolSlot(slot, activeButton = null) {
  setBusy(true, activeButton, '入座中…');
  try {
    const token = poolBotToken();
    const displayName = poolDisplayName();
    const url = token ? `${POOL_API}/${encodeURIComponent(slot)}/join-token` : `${POOL_API}/${encodeURIComponent(slot)}/join`;
    const body = token
      ? { token, display_name: displayName }
      : { controller_type: 'web', controller_id: controllerId(), display_name: displayName };
    const payload = await requestJson(url, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    renderPool({ slots: [payload.slot] });
    await refreshPool(activeButton);
    const botName = payload.bot?.name || payload.seat?.display_name || displayName;
    const seatText = payload.seat?.seat_id || (payload.seat?.seat !== undefined ? `seat${payload.seat.seat}` : '座位');
    if (payload.room_id) {
      setCurrentRoomId(payload.room_id, { updateUrl: true });
      await refreshRoom(activeButton);
      setMessage(payload.auto_started ? `${botName} 已入座 ${seatText}，房间已满并自动开局。` : `${botName} 已进入进行中的房间。`);
    } else {
      const tokenHint = payload.seat_token ? '已拿到 seat token，可在下方 seat 视角使用。' : '等待凑满 3 人。';
      setMessage(`${botName} 已入座房间池 ${slot} 的 ${seatText}，${tokenHint}`);
    }
  } catch (error) {
    setMessage(`入座失败：${humanError(error)}`, true);
  } finally {
    setBusy(false);
  }
}

async function leavePoolSlot(slot, activeButton = null) {
  setBusy(true, activeButton, '退出中…');
  try {
    const token = poolBotToken();
    const url = token ? `${POOL_API}/${encodeURIComponent(slot)}/leave-token` : `${POOL_API}/${encodeURIComponent(slot)}/leave`;
    const body = token
      ? { token, display_name: poolDisplayName() }
      : { controller_type: 'web', controller_id: controllerId(), display_name: poolDisplayName() };
    await requestJson(url, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    await refreshPool(activeButton);
    setMessage(`已退出房间池 ${slot}。`);
  } catch (error) {
    setMessage(`退出房间池失败：${humanError(error)}`, true);
  } finally {
    setBusy(false);
  }
}

async function resetPoolSlot(slot, activeButton = null) {
  setBusy(true, activeButton, '清空中…');
  try {
    await requestJson(`${POOL_API}/${encodeURIComponent(slot)}/reset`, { method: 'POST' });
    await refreshPool(activeButton);
    setMessage(`已清空房间池 ${slot}。`);
  } catch (error) {
    setMessage(`清空房间池失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function startPoolSlot(slot, activeButton = null) {
  setBusy(true, activeButton, '开局中…');
  try {
    const payload = await requestJson(`${POOL_API}/${encodeURIComponent(slot)}/start`, { method: 'POST', body: JSON.stringify({}) });
    await refreshPool(activeButton);
    if (payload.room_id) {
      setCurrentRoomId(payload.room_id, { updateUrl: true });
      await refreshRoom(activeButton);
      setMessage(`房间池 ${slot} 已开局。`);
    }
  } catch (error) {
    setMessage(`房间池开局失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function openPoolRoom(roomId, activeButton = null) {
  setCurrentRoomId(roomId, { updateUrl: true });
  await refreshRoom(activeButton);
  window.scrollTo({ top: document.querySelector('.cr-table-shell')?.offsetTop || 0, behavior: 'smooth' });
}

function normalizeRoom(payload) {
  const room = payload?.state || payload?.room || payload?.card_room || payload;
  if (!room || typeof room !== 'object') return null;
  if (payload?.room_id && !room.room_id) room.room_id = payload.room_id;
  if (payload?.id && !room.room_id) room.room_id = payload.id;
  if (payload?.game && !room.game) room.game = payload.game;
  return room;
}

function roomIdOf(room = currentRoom) {
  return room?.room_id || room?.id || currentRoomId || '';
}

function gameOf(room = currentRoom) {
  return room?.game || room?.game_type || 'doudizhu';
}

function phaseOf(room = currentRoom) {
  const phase = room?.phase || '-';
  const status = room?.status || '-';
  return phase === status ? phase : `${phase} / ${status}`;
}

function landlordOf(room = currentRoom) {
  return seatKey(room?.landlord_seat ?? room?.landlord ?? room?.landlord_index) || '-';
}

function currentSeatOf(room = currentRoom) {
  return seatKey(room?.current_seat ?? room?.turn_seat ?? room?.turn_player ?? room?.turn_index) || '-';
}

function passCountOf(room = currentRoom) {
  return room?.pass_count ?? room?.passes ?? 0;
}

function winnerOf(room = currentRoom) {
  if (!room?.winner && room?.winner !== 0) return '-';
  if (typeof room.winner === 'string' || typeof room.winner === 'number') return seatKey(room.winner);
  return room.winner.seat_id || room.winner.player || room.winner.team || JSON.stringify(room.winner);
}

function seatsOf(room = currentRoom) {
  if (Array.isArray(room?.seats) && room.seats.length) return room.seats;
  const counts = room?.hands_count || {};
  return DEFAULT_SEAT_IDS.map((seatId) => ({
    id: seatId,
    seat_id: seatId,
    role: landlordOf(room) === seatId ? 'landlord' : 'farmer',
    hand_count: counts[seatId],
    is_landlord: landlordOf(room) === seatId,
  }));
}

function handCountOf(room, seat) {
  if (Number.isFinite(seat.hand_count)) return seat.hand_count;
  if (Number.isFinite(seat.card_count)) return seat.card_count;
  const seatId = seatKey(seat.seat_id ?? seat.id ?? seat.player ?? seat.index);
  const hand = room?.hands?.[seatId] || [];
  return Array.isArray(hand) ? hand.length : '-';
}

function roleOf(room, seat) {
  if (seat.role) return seat.role;
  if (seat.is_landlord) return 'landlord';
  const seatId = seatKey(seat.seat_id ?? seat.id ?? seat.player ?? seat.index);
  return landlordOf(room) === seatId ? 'landlord' : 'farmer';
}

function formatRole(role) {
  if (role === 'landlord') return '地主';
  if (role === 'farmer' || role === 'farmer1' || role === 'farmer2') return '农民';
  return role || '-';
}

function displayCard(card) {
  const text = String(card || '').trim();
  if (!text) return '-';
  if (text === 'RJ') return '大王';
  if (text === 'BJ') return '小王';
  const suit = text.slice(-1);
  const rankRaw = text.slice(0, -1);
  const rank = rankRaw === 'T' ? '10' : rankRaw;
  const symbols = { H: '♥', D: '♦', S: '♠', C: '♣' };
  return symbols[suit] ? `${symbols[suit]}${rank}` : text;
}

function seatDisplayName(seatId) {
  return ({ seat0: '本家 · seat0', seat1: '左侧对手 · seat1', seat2: '右侧对手 · seat2' })[seatId] || seatId;
}

function formatCards(cards) {
  if (!Array.isArray(cards) || !cards.length) return '-';
  return cards.map(displayCard).join(' ');
}

function formatLastPlay(room = currentRoom) {
  const last = room?.last_play;
  if (!last) return '-';
  if (typeof last === 'string') return last;
  const actor = seatKey(last.seat_id ?? last.player ?? last.seat) || '-';
  const type = last.action_type || last.action || (Array.isArray(last.cards) && last.cards.length ? 'play' : 'pass');
  if (type === 'pass') return `${seatDisplayName(actor)} 不出`;
  return `${seatDisplayName(actor)} ${formatCards(last.cards)}`;
}

function actionHistoryOf(room = currentRoom) {
  const sources = [
    room?.recent_history,
    room?.action_history,
    room?.history,
    room?.moves,
    room?.state?.recent_history,
    room?.state?.action_history,
    room?.state?.history,
    room?.state?.moves,
  ];
  for (const source of sources) {
    if (Array.isArray(source)) return source;
  }
  return [];
}

function describePattern(pattern) {
  if (!pattern) return '';
  if (typeof pattern === 'string') return pattern;
  if (typeof pattern !== 'object') return String(pattern);
  const type = pattern.type || pattern.name || pattern.kind || '';
  const length = pattern.length ? `×${pattern.length}` : '';
  const labels = {
    single: '单张',
    pair: '对子',
    triple: '三张',
    triple_with_single: '三带一',
    triple_with_pair: '三带二',
    straight: '顺子',
    pair_straight: '连对',
    bomb: '炸弹',
    rocket: '王炸',
  };
  return labels[type] ? `${labels[type]}${length}` : JSON.stringify(pattern);
}

function normalizeSeatAction(action) {
  if (!action || typeof action !== 'object') return null;
  const seatId = seatKey(action.seat_id ?? action.player ?? action.bot_id ?? action.seat ?? action.index);
  if (!seatId) return null;
  const rawType = action.action_type || action.action || action.type || (Array.isArray(action.cards) && action.cards.length ? 'play' : 'pass');
  const type = rawType === 'play_cards' ? 'play' : rawType;
  return {
    seatId,
    type,
    cards: Array.isArray(action.cards) ? action.cards : [],
    pattern: describePattern(action.pattern || action.combo || action.kind || ''),
  };
}

function latestSeatActions(room = currentRoom, spectator = currentSpectator) {
  const result = new Map(DEFAULT_SEAT_IDS.map((seatId) => [seatId, { type: 'waiting', cards: [] }]));
  const history = [
    ...actionHistoryOf(room),
    ...actionHistoryOf(spectator),
  ];
  history.forEach((entry) => {
    const action = normalizeSeatAction(entry);
    if (action && result.has(action.seatId)) result.set(action.seatId, action);
  });
  return result;
}

function renderSeatPlayArea(element, seatId, room = currentRoom, spectator = currentSpectator) {
  const zone = element.querySelector('.cr-play-zone');
  if (!zone) return;
  const action = latestSeatActions(room, spectator).get(seatId) || { type: 'waiting', cards: [] };
  element.classList.toggle('has-played', action.type === 'play' && action.cards.length > 0);
  element.classList.toggle('has-passed', action.type === 'pass');
  if (action.type === 'pass') {
    zone.innerHTML = '<span class="cr-pass-chip">不出</span>';
    return;
  }
  if (action.type === 'play' && action.cards.length) {
    const pattern = action.pattern ? `<span class="cr-play-pattern">${escapeHtml(action.pattern)}</span>` : '';
    zone.innerHTML = `${pattern}<div class="cr-card-row cr-play-cards">${renderCards(action.cards)}</div>`;
    return;
  }
  zone.innerHTML = '<span class="cr-empty">等待出牌</span>';
}

function cardClass(card) {
  const text = String(card || '');
  if (text === 'RJ' || text === 'BJ') return ' joker';
  if (/[HD]$/.test(text)) return ' red-suit';
  return '';
}

function renderCards(cards) {
  if (!Array.isArray(cards) || !cards.length) return '<span class="cr-empty">-</span>';
  return cards.map((card) => `<span class="cr-card${cardClass(card)}" title="${escapeHtml(card)}">${escapeHtml(displayCard(card))}</span>`).join('');
}

function renderSeatShell(room, seatId, spectatorPlayer = null) {
  const element = document.getElementById(`${seatId}Card`);
  if (!element) return;
  const seat = seatsOf(room).find((item) => seatKey(item.seat_id ?? item.id ?? item.player ?? item.index) === seatId) || { id: seatId };
  const role = spectatorPlayer?.role || roleOf(room, seat);
  const isLandlord = spectatorPlayer?.is_landlord || role === 'landlord';
  const isTurn = currentSeatOf(room) === seatId || spectatorPlayer?.is_current;
  const count = spectatorPlayer?.hand_count ?? (Array.isArray(spectatorPlayer?.hand) ? spectatorPlayer.hand.length : handCountOf(room, seat));
  const hand = Array.isArray(spectatorPlayer?.hand) ? spectatorPlayer.hand : null;

  element.classList.toggle('is-turn', Boolean(isTurn));
  element.classList.toggle('is-landlord', Boolean(isLandlord));
  element.querySelector('.cr-seat-head strong').textContent = seatDisplayName(seatId);
  element.querySelector('.cr-role').textContent = formatRole(role);
  element.querySelector('.cr-hand-count').textContent = `剩余：${count} 张`;
  element.querySelector('.cr-seat-meta').textContent = isTurn ? '轮到 TA 出牌' : '等待中';
  element.querySelector('.cr-seat-hand').innerHTML = hand ? renderCards(hand) : '<span class="cr-empty">游客观战接口暂不可用</span>';
  renderSeatPlayArea(element, seatId, room, currentSpectator);
}

function renderHistory(room) {
  const allHistory = actionHistoryOf(room);
  const history = allHistory.slice(-20).reverse();
  els.historyCount.textContent = `${allHistory.length} 条`;
  if (!history.length) {
    els.historyList.innerHTML = '<li class="cr-muted">暂无历史动作</li>';
    return;
  }
  els.historyList.innerHTML = history.map((action, index) => {
    const actor = seatKey(action.seat_id ?? action.player ?? action.bot_id ?? action.seat) || '-';
    const type = action.action_type || action.action || action.type || '-';
    const cards = formatCards(action.cards);
    const number = allHistory.length - index;
    const speech = String(action.speech || '').trim();
    const source = String(action.source || '').trim();
    const retries = action.retries === undefined || action.retries === null ? '' : ` · retries ${action.retries}`;
    const meta = [source ? `来源 ${source}` : '', retries].filter(Boolean).join('');
    return `<li>
      <div><span>#${number}</span><strong>${escapeHtml(actor)}</strong><em>${escapeHtml(type)}</em><code>${escapeHtml(cards)}</code></div>
      ${speech ? `<p class="cr-history-speech">${escapeHtml(speech)}</p>` : ''}
      ${meta ? `<small class="cr-history-meta">${escapeHtml(meta)}</small>` : ''}
    </li>`;
  }).join('');
}

function spectatorPlayers(data) {
  if (!data || typeof data !== 'object') return [];
  if (Array.isArray(data.players)) return data.players;
  if (Array.isArray(data.seats)) return data.seats;
  if (data.state) return spectatorPlayers(data.state);
  return [];
}

function renderSpectatorEmpty(message = '新建或刷新 CardRoom 后尝试读取游客观战接口。') {
  currentSpectator = null;
  if (els.spectatorStatus) els.spectatorStatus.textContent = '未加载';
  if (els.spectatorHands) els.spectatorHands.innerHTML = `<div class="cr-empty">${escapeHtml(message)}</div>`;
  DEFAULT_SEAT_IDS.forEach((seatId) => renderSeatShell(currentRoom || {}, seatId, null));
}

function renderSpectator(data) {
  const players = spectatorPlayers(data);
  if (!els.spectatorHands) return;
  if (!players.length || !players.some((player) => Array.isArray(player.hand))) {
    renderSpectatorEmpty('游客观战接口暂不可用');
    return;
  }
  currentSpectator = data;
  if (els.spectatorStatus) {
    const current = seatKey(data.current_seat ?? data.current_player ?? currentSeatOf(currentRoom));
    els.spectatorStatus.textContent = `当前回合：${seatDisplayName(current) || '-'} · pass ${data.pass_count ?? currentRoom?.pass_count ?? 0}`;
  }

  const bySeat = new Map();
  players.forEach((player) => {
    const seatId = seatKey(player.seat_id ?? player.id ?? player.player ?? player.seat ?? player.index);
    if (seatId) bySeat.set(seatId, player);
  });
  DEFAULT_SEAT_IDS.forEach((seatId) => renderSeatShell(currentRoom || data, seatId, bySeat.get(seatId) || null));

  els.spectatorHands.innerHTML = DEFAULT_SEAT_IDS.map((seatId) => {
    const player = bySeat.get(seatId) || {};
    const role = formatRole(player.role || (player.is_landlord ? 'landlord' : 'farmer'));
    const isTurn = currentSeatOf(currentRoom || data) === seatId || player.is_current;
    const hand = Array.isArray(player.hand) ? player.hand : [];
    const count = player.hand_count ?? hand.length;
    return `<article class="cr-spectator-seat${isTurn ? ' is-turn' : ''}${player.is_landlord || player.role === 'landlord' ? ' is-landlord' : ''}">
      <div class="cr-seat-head"><strong>${escapeHtml(seatDisplayName(seatId))}</strong><span class="cr-role">${escapeHtml(role)}</span></div>
      <div class="cr-hand-count">${isTurn ? '当前回合 · ' : ''}剩余 ${count} 张</div>
      <div class="cr-card-row cr-full-hand">${renderCards(hand)}</div>
    </article>`;
  }).join('');
}

async function refreshSpectator() {
  if (!currentRoomId) {
    renderSpectatorEmpty();
    return null;
  }
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}/spectator`);
    renderSpectator(payload);
    return payload;
  } catch (error) {
    renderSpectatorEmpty(`游客观战接口暂不可用：${error.message}`);
    return null;
  }
}

async function renderAndRefreshSpectator(room) {
  render(room, { keepSpectator: true });
  await refreshSpectator();
}

function render(room = currentRoom, options = {}) {
  currentRoom = room;
  if (!room) {
    els.roomIdText.textContent = currentRoomId || '未创建';
    els.gameText.textContent = 'doudizhu';
    els.phaseText.textContent = '-';
    els.winnerText.textContent = '-';
    els.landlordText.textContent = '-';
    els.currentSeatText.textContent = '-';
    els.passCountText.textContent = '0';
    els.lastPlayText.textContent = '-';
    els.bottomCards.innerHTML = '<span class="cr-empty">-</span>';
    DEFAULT_SEAT_IDS.forEach((seatId) => renderSeatShell({}, seatId, null));
    renderHistory({ action_history: [] });
    if (!options.keepSpectator) renderSpectatorEmpty();
    return;
  }

  const id = roomIdOf(room);
  if (id) setCurrentRoomId(id);
  els.roomIdText.textContent = id || '未创建';
  els.gameText.textContent = gameOf(room) === 'doudizhu' ? '斗地主' : gameOf(room);
  els.phaseText.textContent = phaseOf(room);
  els.winnerText.textContent = winnerOf(room) === '-' ? '-' : seatDisplayName(winnerOf(room));
  els.landlordText.textContent = landlordOf(room) === '-' ? '-' : seatDisplayName(landlordOf(room));
  els.currentSeatText.textContent = currentSeatOf(room) === '-' ? '-' : seatDisplayName(currentSeatOf(room));
  els.passCountText.textContent = String(passCountOf(room));
  els.lastPlayText.textContent = formatLastPlay(room);
  els.bottomCards.innerHTML = renderCards(room.bottom_cards || room.bottom || []);
  DEFAULT_SEAT_IDS.forEach((seatId) => renderSeatShell(room, seatId, null));
  renderHistory(room);
}

async function createRoom() {
  setBusy(true, els.newRoomBtn, '新建中…');
  try {
    const payload = await requestJson(API_BASE, {
      method: 'POST',
      body: JSON.stringify({ game: 'doudizhu', players: DEFAULT_SEAT_IDS, seed: Date.now() % 1000000 }),
    });
    const room = normalizeRoom(payload);
    if (!room) throw new Error('后端返回为空');
    await renderAndRefreshSpectator(room);
    setMessage('已新建 CardRoom，并刷新游客观战视角。');
  } catch (error) {
    setMessage(`新建 CardRoom 失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function refreshRoom(activeButton = els.refreshBtn) {
  if (!currentRoomId) {
    setMessage('还没有 room_id，请先新建 CardRoom。', true);
    render(null);
    return;
  }
  setBusy(true, activeButton, '刷新中…');
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}`);
    const room = normalizeRoom(payload);
    if (!room) throw new Error('后端返回为空');
    await renderAndRefreshSpectator(room);
    setMessage('状态已刷新，并已尝试刷新游客观战视角。');
  } catch (error) {
    setMessage(`刷新失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function stepOnce() {
  if (!currentRoomId) {
    setMessage('还没有 room_id，请先新建 CardRoom。', true);
    return;
  }
  setBusy(true, els.stepBtn, '走牌中…');
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}/step`, { method: 'POST' });
    const room = normalizeRoom(payload);
    if (!room) throw new Error('后端返回为空');
    await renderAndRefreshSpectator(room);
    setMessage('AI 已走一步，并刷新游客观战视角。');
  } catch (error) {
    setMessage(`AI 走一步失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

async function autoRun() {
  if (!currentRoomId) {
    setMessage('还没有 room_id，请先新建 CardRoom。', true);
    return;
  }
  setBusy(true, els.autoRunBtn, '自动跑中…');
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}/auto-run`, {
      method: 'POST',
      body: JSON.stringify({ max_steps: 20 }),
    });
    const room = normalizeRoom(payload);
    if (!room) throw new Error('后端返回为空');
    await renderAndRefreshSpectator(room);
    const stepsRun = payload.steps_run ?? payload.steps ?? room.steps_run;
    setMessage(stepsRun === undefined ? '已自动执行，并刷新游客观战视角。' : `本次自动执行 ${stepsRun} 步，并刷新游客观战视角。`);
  } catch (error) {
    setMessage(`自动跑失败：${error.message}`, true);
  } finally {
    setBusy(false);
  }
}

function selectedSeat() {
  return els.llmSeatSelect?.value || '0';
}

function setLlmResult(title, payload = null, isError = false) {
  if (!els.llmResultText || !els.llmResultBox) return;
  const body = payload === null || payload === undefined
    ? ''
    : typeof payload === 'string'
      ? payload
      : JSON.stringify(payload, null, 2);
  els.llmResultText.textContent = body ? `${title}\n${body}` : title;
  els.llmResultBox.classList.toggle('is-error', Boolean(isError));
  els.llmResultBox.classList.toggle('is-success', !isError && Boolean(title));
}

function apiErrorSummary(error) {
  const payload = error?.payload || {};
  return {
    code: payload.code || payload.error_code || payload.detail?.code || error?.status || '接口暂不可用',
    message: payload.message || payload.detail?.message || payload.detail || error?.message || '接口暂不可用',
    legal_hint: payload.legal_hint || payload.detail?.legal_hint || payload.hint || null,
    attempt: payload.attempt ?? payload.detail?.attempt ?? null,
  };
}

function ensureRoomForLlm() {
  if (!currentRoomId) {
    setLlmResult('还没有 room_id，请先新建 CardRoom。', { code: 'NO_ROOM', message: '还没有 room_id，请先新建 CardRoom。' }, true);
    return false;
  }
  return true;
}

async function getSeatView() {
  if (!ensureRoomForLlm()) return;
  const seat = selectedSeat();
  setBusy(true, els.seatViewBtn, '获取中…');
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}/view?seat=${encodeURIComponent(seat)}`);
    setLlmResult(`seat ${seat} 视角获取成功`, payload);
  } catch (error) {
    setLlmResult('接口暂不可用：获取 seat 视角失败', apiErrorSummary(error), true);
  } finally {
    setBusy(false);
  }
}

async function getLegalActions() {
  if (!ensureRoomForLlm()) return;
  const seat = selectedSeat();
  setBusy(true, els.legalActionsBtn, '获取中…');
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}/legal-actions?seat=${encodeURIComponent(seat)}`);
    setLlmResult(`seat ${seat} 合法动作获取成功`, payload);
  } catch (error) {
    setLlmResult('接口暂不可用：获取合法动作失败', apiErrorSummary(error), true);
  } finally {
    setBusy(false);
  }
}

function parseLlmActionInput() {
  try {
    const payload = JSON.parse(els.llmActionInput?.value || '{}');
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('JSON 必须是对象');
    return payload;
  } catch (error) {
    setLlmResult('JSON 解析失败', { code: 'INVALID_JSON', message: error.message, legal_hint: '请填写合法 JSON 对象。', attempt: null }, true);
    return null;
  }
}

async function submitLlmAction(overrideAction = null, activeButton = els.submitLlmActionBtn) {
  if (!ensureRoomForLlm()) return;
  const seat = selectedSeat();
  const action = overrideAction || parseLlmActionInput();
  if (!action) return;
  setBusy(true, activeButton, '提交中…');
  try {
    const payload = await requestJson(`${API_BASE}/${encodeURIComponent(currentRoomId)}/actions`, {
      method: 'POST',
      body: JSON.stringify({ seat: seatNumber(seat), ...action }),
    });
    setLlmResult(`seat ${seat} action 提交成功`, payload);
    const room = normalizeRoom(payload);
    if (room) await renderAndRefreshSpectator(room);
    else await refreshRoom(activeButton);
  } catch (error) {
    setLlmResult('提交失败：网站裁判未通过或接口暂不可用', apiErrorSummary(error), true);
    await refreshSpectator();
  } finally {
    setBusy(false);
  }
}

async function passAction() {
  await submitLlmAction({ action: 'pass', cards: [], reason: '手动模拟 LLM 选择 pass', source: 'manual_llm_demo' }, els.passActionBtn);
}

els.newRoomBtn?.addEventListener('click', createRoom);
els.stepBtn?.addEventListener('click', stepOnce);
els.autoRunBtn?.addEventListener('click', autoRun);
els.refreshBtn?.addEventListener('click', () => refreshRoom());
els.seatViewBtn?.addEventListener('click', getSeatView);
els.legalActionsBtn?.addEventListener('click', getLegalActions);
els.submitLlmActionBtn?.addEventListener('click', () => submitLlmAction());
els.passActionBtn?.addEventListener('click', passAction);
els.poolRefreshBtn?.addEventListener('click', () => refreshPool());
els.poolSlots?.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-pool-action]');
  if (!button || busy) return;
  const action = button.dataset.poolAction;
  const slot = button.dataset.slot;
  const roomId = button.dataset.roomId;
  if (action === 'join') joinPoolSlot(slot, button);
  else if (action === 'leave') leavePoolSlot(slot, button);
  else if (action === 'reset') resetPoolSlot(slot, button);
  else if (action === 'start') startPoolSlot(slot, button);
  else if (action === 'open') openPoolRoom(roomId, button);
});
els.poolNameInput?.addEventListener('change', poolDisplayName);

poolDisplayName();
controllerId();
render(null);
refreshPool().catch((error) => setMessage(`房间池刷新失败：${error.message}`, true));
if (currentRoomId) {
  refreshRoom().catch((error) => setMessage(`刷新失败：${error.message}`, true));
}
