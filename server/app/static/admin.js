const ADMIN_KEY = 'chessArenaAdminToken';

function $(id) { return document.getElementById(id); }
function setStatus(msg) { $('adminStatus').textContent = msg; }
function setBotCount(n) { const el = $('botCount'); if (el) el.textContent = `${n || 0} 个`; }
function adminToken() { return ($('adminToken').value || localStorage.getItem(ADMIN_KEY) || '').trim(); }
function authHeaders() { return { 'Authorization': `Bearer ${adminToken()}` }; }
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}
function shortTime(ts) {
  if (!ts) return '-';
  try { return new Date(ts * 1000).toLocaleString('zh-CN', { hour12:false }); } catch { return '-'; }
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}), ...authHeaders() };
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { ...opts, headers });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) throw new Error(`${res.status} ${data.detail || data.raw || res.statusText}`);
  return data;
}

function renderBots(bots) {
  const tbody = $('adminBotRows');
  if (!bots.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">暂无棋手</td></tr>';
    setBotCount(0);
    return;
  }
  setBotCount(bots.length);
  tbody.innerHTML = bots.map(b => `
    <tr data-bot-id="${escapeHtml(b.bot_id)}">
      <td>
        <strong>${escapeHtml(b.name)}</strong><br>
        <span class="muted mini-text">${escapeHtml(b.bot_id)}</span><br>
        <span class="muted mini-text">${escapeHtml(b.description || '')}</span>
      </td>
      <td>${escapeHtml(b.online_status)}<br><span class="muted mini-text">${shortTime(b.last_seen_at)}</span><br><span class="muted mini-text">引擎/模型：${escapeHtml(b.engine_mode || '-')} / ${escapeHtml(b.client_type || '-')}</span></td>
      <td>${b.rating || 1000}<br><span class="muted mini-text">${b.games || 0}局 / 胜${b.wins || 0} 负${b.losses || 0} 和${b.draws || 0}</span></td>
      <td><code class="token-code">${escapeHtml(b.token)}</code></td>
      <td class="admin-actions">
        <button class="ghost copy-token" data-token="${escapeHtml(b.token)}">复制 token</button>
        <button class="danger delete-bot" data-name="${escapeHtml(b.name)}">删除</button>
      </td>
    </tr>
  `).join('');
}

async function loadBots() {
  const token = adminToken();
  if (!token) { setStatus('先填写管理员 token'); return; }
  localStorage.setItem(ADMIN_KEY, token);
  setStatus('加载中...');
  const data = await api('/api/admin/bots');
  renderBots(data.bots || []);
  setStatus(`已加载 ${data.total || 0} 个棋手。`);
}

async function createBot() {
  const name = $('newBotName').value.trim();
  if (!name) { setStatus('新增失败：棋手名字不能为空'); return; }
  const payload = {
    name,
    token: $('newBotToken').value.trim() || null,
    avatar_url: $('newBotAvatar').value.trim() || null,
    description: $('newBotDescription').value.trim() || null,
    chess_style: $('newBotStyle').value,
    is_public: true,
    is_enabled: true,
  };
  setStatus('正在新增...');
  const data = await api('/api/admin/bots', { method: 'POST', body: JSON.stringify(payload) });
  setStatus(`已新增：${data.bot.name}\nToken: ${data.bot.token}`);
  clearForm();
  await loadBots();
}

async function deleteBot(row, name) {
  const botId = row.dataset.botId;
  if (!botId) return;
  if (!confirm(`确定删除棋手「${name}」？相关挑战、对局、走子、排名历史也会一起清理。`)) return;
  setStatus(`正在删除 ${name}...`);
  const data = await api(`/api/admin/bots/${encodeURIComponent(botId)}`, { method: 'DELETE' });
  setStatus(`已删除 ${botId}，清理对局 ${data.deleted_matches} 个，挑战 ${data.deleted_challenges} 个。`);
  await loadBots();
}

function clearForm() {
  ['newBotName','newBotToken','newBotAvatar','newBotDescription'].forEach(id => $(id).value = '');
  $('newBotStyle').value = 'random';
}

window.addEventListener('DOMContentLoaded', () => {
  $('adminToken').value = localStorage.getItem(ADMIN_KEY) || '';
  $('toggleAdminToken').addEventListener('click', () => {
    $('adminToken').type = $('adminToken').type === 'password' ? 'text' : 'password';
  });
  $('saveAdminToken').addEventListener('click', () => loadBots().catch(e => setStatus(`加载失败：${e.message}`)));
  $('refreshBots').addEventListener('click', () => loadBots().catch(e => setStatus(`刷新失败：${e.message}`)));
  $('createBotBtn').addEventListener('click', () => createBot().catch(e => setStatus(`新增失败：${e.message}`)));
  $('clearNewBot').addEventListener('click', clearForm);
  $('adminBotRows').addEventListener('click', async (e) => {
    const copyBtn = e.target.closest('.copy-token');
    if (copyBtn) {
      await navigator.clipboard.writeText(copyBtn.dataset.token || '');
      setStatus('token 已复制到剪贴板');
      return;
    }
    const delBtn = e.target.closest('.delete-bot');
    if (delBtn) {
      const row = delBtn.closest('tr');
      await deleteBot(row, delBtn.dataset.name || row.dataset.botId).catch(err => setStatus(`删除失败：${err.message}`));
    }
  });
  if ($('adminToken').value) loadBots().catch(e => setStatus(`自动加载失败：${e.message}`));
});
