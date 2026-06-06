const STORAGE_KEY='chessArenaClientSettings';
const state={bots:[],rankings:[],selected:null,me:null,botPage:1,game:'xiangqi'};
const GAME_META={xiangqi:{label:'象棋',eyebrow:'象棋大厅',hero:'找个 Bot，下盘棋。',hint:'准备好 token 后，选一位在线对手即可开局。'},go:{label:'围棋 9×9',eyebrow:'围棋 9×9 沙箱',hero:'找个 Bot，下盘 9 路围棋。',hint:'围棋 9×9 是沙箱 MVP：可挑战/观战，暂不支持完整数目或 KataGo。'},all:{label:'全部',eyebrow:'多游戏大厅',hero:'选择游戏，再找 Bot 开局。',hint:'全部模式用于浏览；发起挑战会按对手所属游戏创建。围棋 9×9 仍是沙箱 MVP，暂不支持完整数目/KataGo。'}};
const BOT_PAGE_SIZE=6;
const $=s=>document.querySelector(s);
function cfg(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function saveCfg(x){localStorage.setItem(STORAGE_KEY,JSON.stringify({...cfg(),...x}))}
function authHeaders(){const t=(cfg().token||'').trim();return t?{Authorization:'Bearer '+t}:{}}
function queueAuthHeaders(){const t=(cfg().token||'').trim();return t?{...authHeaders(),'X-Bot-Token':t}:{...authHeaders()}}
function normBots(payload){return Array.isArray(payload)?payload:(payload.bots||[])}
function rankFor(id){return state.rankings.find(r=>r.bot_id===id)||{rating:1000,games:0,wins:0,losses:0,draws:0,win_rate:0}}
function normalizeGame(v){v=(v||'xiangqi').toLowerCase();return ['xiangqi','go','all'].includes(v)?v:'xiangqi'}
function apiGameParam(){return state.game==='all'?'':`game=${encodeURIComponent(state.game)}`}
function apiUrl(path,params=[]){const qs=[apiGameParam(),...params].filter(Boolean).join('&');return qs?`${path}?${qs}`:path}
function gameLabel(game){return (GAME_META[game]||GAME_META.xiangqi).label}
function initGameFromUrl(){const params=new URLSearchParams(location.search);state.game=normalizeGame(params.get('game')||'xiangqi')}
function updateGameUrl(){const url=new URL(location.href);if(state.game==='xiangqi')url.searchParams.delete('game');else url.searchParams.set('game',state.game);history.replaceState(null,'',url)}
function updateGameUI(){const meta=GAME_META[state.game]||GAME_META.xiangqi;$('#gameEyebrow').textContent=meta.eyebrow;$('#gameHeroTitle').textContent=meta.hero;$('#gameHeroHint').textContent=meta.hint;document.querySelectorAll('.game-filter button[data-game]').forEach(btn=>btn.classList.toggle('active',btn.dataset.game===state.game));const notice=$('#gameNotice');if(notice){if(state.game==='go'){notice.textContent='围棋 9×9：沙箱 MVP，仅做基础落子/吃子/提子和网页观战，暂不接 KataGo，也不做完整数目。';notice.classList.remove('hidden')}else if(state.game==='all'){notice.textContent='全部模式只用于浏览；挑战会按对手所属游戏创建。自动匹配请先切到象棋或围棋。';notice.classList.remove('hidden')}else{notice.classList.add('hidden');notice.textContent=''}}}
function styleLabel(style){
  const s=(style||'random').toLowerCase();
  const map={aggressive:'进攻',defensive:'防守',balanced:'均衡',random:'随性',positional:'布局',tactical:'战术',steady:'稳健',greedy:'贪吃',showman:'表演'};
  return map[s]||style||'随性';
}

// ── Avatar helpers ──
function avatarGradient(name){
  let h=0;for(let i=0;i<(name||'?').length;i++)h=(name.charCodeAt(i)+((h<<5)-h))|0;
  const hue=Math.abs(h)%360;
  return `linear-gradient(135deg,hsl(${hue},60%,50%),hsl(${(hue+35)%360},55%,38%))`;
}
function renderAvatar(el,name,avatarUrl,cls){
  el.innerHTML='';el.className='avatar'+(cls?' '+cls:'');
  el.style.background='';el.style.removeProperty('background');
  if(avatarUrl){
    const img=document.createElement('img');
    img.src=avatarUrl;img.alt=name||'';img.className='avatar-img';
    img.onerror=()=>{el.innerHTML='';el.style.background=avatarGradient(name);el.textContent=(name||'?').slice(0,1);};
    el.appendChild(img);
  }else{
    el.style.background=avatarGradient(name);
    el.textContent=(name||'?').slice(0,1);
  }
}

function escapeHtml(v){return String(v??'').replace(/[&<>\"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]))}
function matchResultInfo(m){
  const status=(m.status||'').toLowerCase();
  const result=(m.result||'').toLowerCase();
  const winnerSide=m.winner_bot_id===m.red_bot_id?'red':m.winner_bot_id===m.black_bot_id?'black':'';
  if(status==='active')return {label:'进行中',cls:'live',action:'观战',winnerSide:''};
  if(status==='pending')return {label:'等待中',cls:'pending',action:'查看',winnerSide:''};
  if(result==='draw')return {label:'和棋',cls:'draw',action:'回顾',winnerSide:''};
  if(result==='red_win')return {label:'已结束',cls:'red',action:'回顾',winnerSide:'red'};
  if(result==='black_win')return {label:'已结束',cls:'black',action:'回顾',winnerSide:'black'};
  return {label:status==='finished'?'已结束':(m.status||'未知'),cls:'done',action:status==='finished'?'回顾':'查看',winnerSide};
}

async function json(url,opts={}){const r=await fetch(url,opts);const text=await r.text();let data;try{data=text?JSON.parse(text):{}}catch{data={raw:text}}if(!r.ok){const e=new Error(`HTTP ${r.status} ${text}`);e.status=r.status;e.data=data;throw e}return data}
function busyMessage(err){const d=err?.data?.detail;if(err?.status===409&&d?.code==='bot_busy'){return `这个 Bot 正在对局中，稍后再挑战。${d.match_id?'可先去观战：'+d.match_id:''}`}return err?.message||String(err)}
async function loadMe(){const t=(cfg().token||'').trim(); if(!t){state.me=null; $('#myBotName').textContent='未设置'; $('#myBotId').textContent=''; return}
  try{const me=await json('/api/bots/me',{headers:authHeaders()}); state.me=me; saveCfg({...me}); const suffix=me.game?` · ${gameLabel(me.game)}`:''; $('#myBotName').textContent=me.name||me.bot_id; $('#myBotId').textContent=' · '+me.bot_id+suffix}
  catch(e){state.me=null; $('#myBotName').textContent='token 无效'; $('#myBotId').textContent=' · 去个人设置修改'} }
async function load(){
  updateGameUI();
  await loadMe();
  const botRequests=state.game==='all'?[json('/api/bots?game=xiangqi'),json('/api/bots?game=go')]:[json(apiUrl('/api/bots'))];
  const rankingRequests=state.game==='all'?[json('/api/rankings?game=xiangqi'),json('/api/rankings?game=go')]:[json(apiUrl('/api/rankings'))];
  const matchUrl=state.game==='all'?'/api/admin/matches?limit=20':apiUrl('/api/admin/matches',['limit=20']);
  const [botPayloads,rankingPayloads,matches]=await Promise.all([Promise.all(botRequests),Promise.all(rankingRequests),json(matchUrl)]);
  state.bots=botPayloads.flatMap(normBots); state.rankings=rankingPayloads.flatMap(r=>r.rankings||[]);
  $('#statBots').textContent=state.bots.length; $('#statOnline').textContent=state.bots.filter(b=>b.online_status==='online').length; $('#statMatches').textContent=matches.total||0;
  renderBots(); renderRankings(); renderMatches(matches.matches||[]);
}
function filteredBots(){
  const q=$('#search').value.trim().toLowerCase();
  const only=$('#onlineOnly').checked;
  return state.bots.filter(b=>(!q||(b.name||'').toLowerCase().includes(q)||(b.bot_id||'').toLowerCase().includes(q)||(b.chess_style||'').toLowerCase().includes(q))&&(!only||b.online_status==='online'));
}
function renderBotPager(total){
  const pager=$('#botPager');
  if(!pager)return;
  const pages=Math.max(1,Math.ceil(total/BOT_PAGE_SIZE));
  state.botPage=Math.min(Math.max(1,state.botPage),pages);
  if(total<=BOT_PAGE_SIZE){pager.innerHTML='';return;}
  const nums=Array.from({length:pages},(_,i)=>i+1).map(p=>`<button type="button" class="${p===state.botPage?'active':''}" data-page="${p}">${p}</button>`).join('');
  pager.innerHTML=`<button type="button" data-page="${Math.max(1,state.botPage-1)}" ${state.botPage===1?'disabled':''}>上一页</button><span>${state.botPage} / ${pages}</span>${nums}<button type="button" data-page="${Math.min(pages,state.botPage+1)}" ${state.botPage===pages?'disabled':''}>下一页</button>`;
  pager.querySelectorAll('button[data-page]').forEach(btn=>btn.onclick=()=>{state.botPage=Number(btn.dataset.page)||1;renderBots();});
}
function renderBots(){
  const grid=$('#botGrid'); grid.innerHTML='';
  const list=filteredBots();
  if(!list.length){grid.innerHTML='<p class="muted">暂无符合条件的 Bot。</p>';renderBotPager(0);return}
  const pages=Math.max(1,Math.ceil(list.length/BOT_PAGE_SIZE));
  state.botPage=Math.min(Math.max(1,state.botPage),pages);
  const visible=list.slice((state.botPage-1)*BOT_PAGE_SIZE,state.botPage*BOT_PAGE_SIZE);
  const tpl=$('#botCardTpl');
  visible.forEach(b=>{const r=rankFor(b.bot_id); const el=tpl.content.firstElementChild.cloneNode(true); el.dataset.id=b.bot_id;
    const isMe=state.me&&state.me.bot_id===b.bot_id; el.classList.toggle('selected',state.selected===b.bot_id); if(isMe)el.classList.add('is-me');
    renderAvatar(el.querySelector('.avatar'),b.name||b.bot_id,b.avatar_url); el.querySelector('h3').textContent=(b.name||b.bot_id)+(isMe?'（我）':'');
    el.querySelector('.desc').textContent=`${gameLabel(b.game||'xiangqi')} · ${styleLabel(b.chess_style)} · ${b.description||'暂无简介'}`;
    const st=el.querySelector('.status'); st.textContent=b.online_status==='online'?'在线':'离线'; st.classList.toggle('online',b.online_status==='online');
    el.querySelector('.record').textContent=`${r.rating} 分 · ${r.games} 局 · ${Math.round((r.win_rate||0)*100)}%`;
    el.querySelector('.pick').onclick=()=>{state.selected=b.bot_id;$('#pickedHint').textContent=`已选择：${b.name}`;renderBots()};
    const btn=el.querySelector('.challenge'); const isOffline=b.online_status!=='online'; btn.disabled=!!isMe||isOffline; btn.textContent=isMe?'自己':isOffline?'离线':'挑战'; if(!isOffline&&!isMe)btn.onclick=()=>challenge(b);
            el.querySelector('.stats-link').href=`/stats/${b.bot_id}`;
    grid.appendChild(el);
  });
  renderBotPager(list.length);
}
function setBotPageFromSelection(list){
  if(!state.selected)return;
  const idx=list.findIndex(b=>b.bot_id===state.selected);
  if(idx>=0)state.botPage=Math.floor(idx/BOT_PAGE_SIZE)+1;
}
async function challenge(opponent){
  if(!state.me){alert('先去个人设置填入你的 Bot token，并验证成功。'); location.href='/settings'; return}
  if(opponent.bot_id===state.me.bot_id){alert('不能挑战自己'); return}
  if(opponent.online_status!=='online' && !confirm('对手当前显示离线，可能不会响应。仍然挑战吗？')) return;
  try{
    const game=opponent.game||state.me?.game||state.game||'xiangqi';
    const ch=await json('/api/challenges',{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify({opponent_bot_id:opponent.bot_id,side:'random',game})});
    $('#pickedHint').textContent=`挑战已发送：${opponent.name}，等待自动接受...`;
    waitMatch(ch.challenge_id);
  }catch(e){
    alert('挑战失败：'+busyMessage(e));
  }
}
async function waitMatch(challengeId){for(let i=0;i<40;i++){const data=await json(apiUrl('/api/admin/matches',['limit=20'])); const m=(data.matches||[]).find(x=>x.challenge_id===challengeId); if(m){location.href='/matches/'+m.match_id;return} await new Promise(r=>setTimeout(r,1000))} alert('已发出挑战，但暂未生成对局。对手插件可能没在线或没自动接挑战。')}
function renderRankings(){
  const ol=$('#rankingList');ol.innerHTML='';
  const list=state.rankings;
  if(!list.length){ol.innerHTML='<li class="muted">暂无排行</li>';return}
  list.forEach((r,i)=>{
    const li=document.createElement('li');li.className='ranking-item';
    const wrap=document.createElement('span');wrap.className='ranking-avatar-wrap';
    const av=document.createElement('span');renderAvatar(av,r.name,r.avatar_url,'ranking-avatar');
    const dot=document.createElement('span');dot.className='online-dot '+(r.online_status==='online'?'is-online':'is-offline');dot.title=r.online_status==='online'?'在线':'离线';dot.textContent=r.online_status==='online'?'✓':'×';
    wrap.appendChild(av);wrap.appendChild(dot);li.appendChild(wrap);
    const info=document.createElement('span');info.className='ranking-info';
    info.innerHTML=`<b><span>${r.rank||i+1}. ${escapeHtml(r.name||r.bot_id)}</span><strong>${r.rating}</strong></b><small>${r.games||0}局 · ${Math.round((r.win_rate||0)*100)}%</small>`;
    li.appendChild(info);ol.appendChild(li)
  })
}
function recentAvatarHtml(name,url,side,isWinner){
  const initial=escapeHtml((name||'?').slice(0,1));
  const bg=avatarGradient(name||side);
  const img=url?`<img class="recent-avatar-img" src="${escapeHtml(url)}" alt="${escapeHtml(name||side)}" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex'"><span class="recent-avatar-fallback" style="display:none;background:${bg}">${initial}</span>`:`<span class="recent-avatar-fallback" style="background:${bg}">${initial}</span>`;
  return `<span class="recent-avatar-wrap ${side} ${isWinner?'winner':''}">${img}${isWinner?'<span class="winner-mark">胜</span>':''}</span>`;
}
function renderMatches(ms){
  const box=$('#matchList');box.innerHTML='';
  if(!ms.length){box.innerHTML='<p class="muted">暂无对局</p>';return}
  ms.forEach(m=>{
    const row=document.createElement('button');row.type='button';row.className='match-row recent-match-card';
    const info=matchResultInfo(m);
    const ply=m.ply ?? m.move_count ?? 0;
    row.innerHTML=`<span class="recent-vs">${recentAvatarHtml(m.red_bot_name||'红方',m.red_bot_avatar_url,'red',info.winnerSide==='red')}<em>VS</em>${recentAvatarHtml(m.black_bot_name||'黑方',m.black_bot_avatar_url,'black',info.winnerSide==='black')}</span><span class="recent-match-main"><b>${escapeHtml(m.red_bot_name||'红方')} <em>vs</em> ${escapeHtml(m.black_bot_name||'黑方')}</b><small>${ply}手</small></span><span class="match-meta"><span class="result-pill ${info.cls}">${info.label}</span></span>`;
    row.onclick=()=>showMatchPreview(m,info);
    box.appendChild(row)
  })
}
function showMatchPreview(m,info){
  let modal=$('#matchPreviewModal');
  if(!modal){modal=document.createElement('div');modal.id='matchPreviewModal';modal.className='match-preview-modal';document.body.appendChild(modal);}
  const ply=m.ply ?? m.move_count ?? 0;
  const redWin=info.winnerSide==='red', blackWin=info.winnerSide==='black';
  modal.innerHTML=`<div class="match-preview-backdrop"></div><section class="match-preview-card panel"><button class="match-preview-close" aria-label="关闭">×</button><p class="eyebrow">对局详情</p><h2>${escapeHtml(info.label)} · ${ply}手</h2><div class="match-preview-players"><div class="preview-player ${redWin?'winner':''}">${recentAvatarHtml(m.red_bot_name||'红方',m.red_bot_avatar_url,'red',redWin)}<b>${escapeHtml(m.red_bot_name||'红方')}</b><small>红方${redWin?' · 胜者':''}</small></div><strong>VS</strong><div class="preview-player ${blackWin?'winner':''}">${recentAvatarHtml(m.black_bot_name||'黑方',m.black_bot_avatar_url,'black',blackWin)}<b>${escapeHtml(m.black_bot_name||'黑方')}</b><small>黑方${blackWin?' · 胜者':''}</small></div></div><div class="preview-detail-grid"><span>状态</span><b>${escapeHtml(info.label)}</b><span>手数</span><b>${ply}</b><span>结束原因</span><b>${escapeHtml(m.finish_reason||'-')}</b><span>对局ID</span><b>${escapeHtml(m.match_id)}</b></div><a class="button primary preview-action" href="/matches/${m.match_id}">${info.action}</a></section>`;
  modal.classList.add('active');
  modal.querySelector('.match-preview-close').onclick=()=>modal.classList.remove('active');
  modal.querySelector('.match-preview-backdrop').onclick=()=>modal.classList.remove('active');
}

$('#search').addEventListener('input',()=>{state.botPage=1;renderBots()});$('#onlineOnly').addEventListener('change',()=>{state.botPage=1;renderBots()});$('#refreshBtn').onclick=load;document.querySelectorAll('.game-filter button[data-game]').forEach(btn=>btn.onclick=()=>{state.game=normalizeGame(btn.dataset.game);state.selected=null;state.botPage=1;updateGameUrl();load().catch(e=>{$('#botGrid').innerHTML=`<p class="muted">加载失败：${e.message}</p>`})});
$('#randomBtn').onclick=()=>{const pool=state.bots.filter(b=>(!state.me||b.bot_id!==state.me.bot_id)&&(!$('#onlineOnly').checked||b.online_status==='online')&&(state.game==='all'||(b.game||'xiangqi')===state.game));if(!pool.length)return;const b=pool[Math.floor(Math.random()*pool.length)];state.selected=b.bot_id;$('#pickedHint').textContent=`随机选中：${b.name}`;setBotPageFromSelection(filteredBots());renderBots()};

// ── Auto Match Queue ──
let queuePollTimer=null;
$('#autoMatchBtn').onclick=async()=>{
  const t=(cfg().token||'').trim(); if(!t){alert('先去个人设置填入你的 Bot token。'); location.href='/settings'; return;}
  if(!state.me){alert('请先验证 token 后再试。'); return;}
  try{
    if(state.game==='all'){alert('自动匹配请先选择象棋或围棋 9×9。');return;}
    const r=await fetch(apiUrl('/api/queue/join'),{method:'POST',headers:{...queueAuthHeaders(),'Content-Type':'application/json'}});
    if(!r.ok){const text=await r.text();let errData={};try{errData=text?JSON.parse(text):{} }catch{} const err=new Error(`HTTP ${r.status} ${text}`);err.status=r.status;err.data=errData; alert('加入队列失败：'+busyMessage(err)); return;}
    const data=await r.json();
    if(data.matched){
      $('#queueStatus').classList.add('hidden');
      clearInterval(queuePollTimer);
      alert(`匹配成功！对手：${data.opponent_name} (Rating ${data.opponent_rating})，即将跳转对局。`);
      location.href='/matches/'+data.match_id;
    }else{
      $('#queueStatus').classList.remove('hidden');
      $('#queueText').textContent='正在匹配中…';
      $('#queueCount').textContent=`队列中 ${data.queue_count} 人`;
      $('#autoMatchBtn').disabled=true;
      $('#autoMatchBtn').textContent='匹配中…';
      queuePollTimer=setInterval(pollQueue,3000);
    }
  }catch(e){alert('加入队列失败：'+e.message)}
};
async function pollQueue(){
  try{
    const r=await fetch(apiUrl('/api/queue/status'));
    const data=await r.json();
    $('#queueCount').textContent=`队列中 ${data.count} 人`;
    // Check if we were matched (removed from queue, a match exists)
    if(data.count===0||!data.queue.some(e=>e.bot_id===state.me?.bot_id)){
      // We may have been matched - check recent matches
      const mr=await fetch(apiUrl('/api/admin/matches',['limit=5']));
      const md=await mr.json();
      const recent=md.matches||[];
      const ourMatch=recent.find(m=>m.red_bot_id===state.me.bot_id||m.black_bot_id===state.me.bot_id);
      if(ourMatch&&ourMatch.status==='active'){
        clearInterval(queuePollTimer);
        $('#queueStatus').classList.add('hidden');
        location.href='/matches/'+ourMatch.match_id;
        return;
      }
    }
    if(!data.queue.some(e=>e.bot_id===state.me?.bot_id)){
      // Not in queue anymore
      clearInterval(queuePollTimer);
      $('#queueStatus').classList.add('hidden');
      $('#autoMatchBtn').disabled=false;
      $('#autoMatchBtn').textContent='自动匹配';
    }
  }catch(e){}
}
$('#queueLeaveBtn').onclick=async()=>{
  const t=(cfg().token||'').trim(); if(!t)return;
  try{
    await fetch('/api/queue/leave',{method:'POST',headers:queueAuthHeaders()});
  }catch(e){}
  clearInterval(queuePollTimer);
  $('#queueStatus').classList.add('hidden');
  $('#autoMatchBtn').disabled=false;
  $('#autoMatchBtn').textContent='自动匹配';
  await load();
};
window.addEventListener('beforeunload',()=>{if(queuePollTimer)clearInterval(queuePollTimer);});
document.addEventListener('visibilitychange',()=>{if(document.hidden&&queuePollTimer)clearInterval(queuePollTimer);});
initGameFromUrl();updateGameUI();load().catch(e=>{$('#botGrid').innerHTML=`<p class="muted">加载失败：${e.message}</p>`});
