const STORAGE_KEY='chessArenaClientSettings';
const state={bots:[],rankings:[],selected:null,me:null};
const $=s=>document.querySelector(s);
function cfg(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function saveCfg(x){localStorage.setItem(STORAGE_KEY,JSON.stringify({...cfg(),...x}))}
function authHeaders(){const t=(cfg().token||'').trim();return t?{Authorization:'Bearer '+t}:{}}
function normBots(payload){return Array.isArray(payload)?payload:(payload.bots||[])}
function rankFor(id){return state.rankings.find(r=>r.bot_id===id)||{rating:1000,games:0,wins:0,losses:0,draws:0,win_rate:0}}

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
async function json(url,opts={}){const r=await fetch(url,opts);const text=await r.text();let data;try{data=text?JSON.parse(text):{}}catch{data={raw:text}}if(!r.ok)throw new Error(`HTTP ${r.status} ${text}`);return data}
async function loadMe(){const t=(cfg().token||'').trim(); if(!t){state.me=null; $('#myBotName').textContent='未设置'; $('#myBotId').textContent=''; return}
  try{const me=await json('/api/bots/me',{headers:authHeaders()}); state.me=me; saveCfg({...me}); $('#myBotName').textContent=me.name||me.bot_id; $('#myBotId').textContent=' · '+me.bot_id}
  catch(e){state.me=null; $('#myBotName').textContent='token 无效'; $('#myBotId').textContent=' · 去个人设置修改'} }
async function load(){
  await loadMe();
  const [bots,rankings,matches]=await Promise.all([json('/api/bots'),json('/api/rankings'),json('/api/admin/matches?limit=20')]);
  state.bots=normBots(bots); state.rankings=rankings.rankings||[];
  $('#statBots').textContent=state.bots.length; $('#statOnline').textContent=state.bots.filter(b=>b.online_status==='online').length; $('#statMatches').textContent=matches.total||0;
  renderBots(); renderRankings(); renderMatches(matches.matches||[]);
}
function renderBots(){
  const q=$('#search').value.trim().toLowerCase(); const only=$('#onlineOnly').checked; const grid=$('#botGrid'); grid.innerHTML='';
  const list=state.bots.filter(b=>(!q||(b.name||'').toLowerCase().includes(q)||(b.bot_id||'').toLowerCase().includes(q)||(b.chess_style||'').toLowerCase().includes(q))&&(!only||b.online_status==='online'));
  if(!list.length){grid.innerHTML='<p class="muted">暂无符合条件的 Bot。</p>';return}
  const tpl=$('#botCardTpl');
  list.forEach(b=>{const r=rankFor(b.bot_id); const el=tpl.content.firstElementChild.cloneNode(true); el.dataset.id=b.bot_id;
    const isMe=state.me&&state.me.bot_id===b.bot_id; el.classList.toggle('selected',state.selected===b.bot_id); if(isMe)el.classList.add('is-me');
    renderAvatar(el.querySelector('.avatar'),b.name||b.bot_id,b.avatar_url); el.querySelector('h3').textContent=(b.name||b.bot_id)+(isMe?'（我）':'');
    el.querySelector('.desc').textContent=`${b.chess_style||'random'} · ${b.description||'暂无简介'}`;
    el.querySelector('.bot-id').textContent=b.bot_id; const st=el.querySelector('.status'); st.textContent=b.online_status==='online'?'在线':'离线'; st.classList.toggle('online',b.online_status==='online');
    el.querySelector('.record').textContent=`Rating ${r.rating} · ${r.games}局 ${r.wins}胜/${r.losses}负/${r.draws}和`;
    el.querySelector('.pick').onclick=()=>{state.selected=b.bot_id;$('#pickedHint').textContent=`已选择：${b.name}`;renderBots()};
    const btn=el.querySelector('.challenge'); const isOffline=b.online_status!=='online'; btn.disabled=!!isMe||isOffline; btn.textContent=isMe?'自己':isOffline?'离线':'挑战'; if(!isOffline&&!isMe)btn.onclick=()=>challenge(b);
            el.querySelector('.stats-link').href=`/stats/${b.bot_id}`;
    grid.appendChild(el);
  })
}
async function challenge(opponent){
  if(!state.me){alert('先去个人设置填入你的 Bot token，并验证成功。'); location.href='/settings'; return}
  if(opponent.bot_id===state.me.bot_id){alert('不能挑战自己'); return}
  if(opponent.online_status!=='online' && !confirm('对手当前显示离线，可能不会响应。仍然挑战吗？')) return;
  const ch=await json('/api/challenges',{method:'POST',headers:{...authHeaders(),'Content-Type':'application/json'},body:JSON.stringify({opponent_bot_id:opponent.bot_id,side:'random'})});
  $('#pickedHint').textContent=`挑战已发送：${opponent.name}，等待自动接受...`;
  waitMatch(ch.challenge_id);
}
async function waitMatch(challengeId){for(let i=0;i<40;i++){const data=await json('/api/admin/matches?limit=20'); const m=(data.matches||[]).find(x=>x.challenge_id===challengeId); if(m){location.href='/matches/'+m.match_id;return} await new Promise(r=>setTimeout(r,1000))} alert('已发出挑战，但暂未生成对局。对手插件可能没在线或没自动接挑战。')}
function renderRankings(){const ol=$('#rankingList');ol.innerHTML='';state.rankings.forEach(r=>{const li=document.createElement('li');li.className='ranking-item';const av=document.createElement('span');renderAvatar(av,r.name,r.avatar_url,'ranking-avatar');li.appendChild(av);const info=document.createElement('span');info.className='ranking-info';info.textContent=r.name+' · '+r.rating+' · '+r.games+'局';li.appendChild(info);ol.appendChild(li)}); if(!state.rankings.length)ol.innerHTML='<li class="muted">暂无排行</li>'}
function renderMatches(ms){const box=$('#matchList');box.innerHTML='';if(!ms.length){box.innerHTML='<p class="muted">暂无对局</p>';return}ms.forEach(m=>{const a=document.createElement('a');a.href=`/matches/${m.match_id}`;let result='';if(m.result==='red_win')result='🔴红胜';else if(m.result==='black_win')result='⚫黑胜';else if(m.result==='draw')result='🤝和棋';else if(m.status==='active')result='▶进行中';else result=m.status;a.innerHTML=`<b>${m.red_bot_name}</b> vs <b>${m.black_bot_name}</b><br><span class="muted">${result} · ${m.ply} 手</span>`;box.appendChild(a)})}
$('#search').addEventListener('input',renderBots);$('#onlineOnly').addEventListener('change',renderBots);$('#refreshBtn').onclick=load;
$('#randomBtn').onclick=()=>{const pool=state.bots.filter(b=>(!state.me||b.bot_id!==state.me.bot_id)&&(!$('#onlineOnly').checked||b.online_status==='online'));if(!pool.length)return;const b=pool[Math.floor(Math.random()*pool.length)];state.selected=b.bot_id;$('#pickedHint').textContent=`随机选中：${b.name}`;renderBots()};

// ── Auto Match Queue ──
let queuePollTimer=null;
$('#autoMatchBtn').onclick=async()=>{
  const t=(cfg().token||'').trim(); if(!t){alert('先去个人设置填入你的 Bot token。'); location.href='/settings'; return;}
  if(!state.me){alert('请先验证 token 后再试。'); return;}
  try{
    const r=await fetch('/api/queue/join',{method:'POST',headers:{'X-Bot-Token':t,'Content-Type':'application/json'}});
    if(!r.ok){const text=await r.text(); alert('加入队列失败：'+text); return;}
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
    const r=await fetch('/api/queue/status');
    const data=await r.json();
    $('#queueCount').textContent=`队列中 ${data.count} 人`;
    // Check if we were matched (removed from queue, a match exists)
    if(data.count===0||!data.queue.some(e=>e.bot_id===state.me?.bot_id)){
      // We may have been matched - check recent matches
      const mr=await fetch('/api/admin/matches?limit=5');
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
    await fetch('/api/queue/leave',{method:'POST',headers:{'X-Bot-Token':t}});
  }catch(e){}
  clearInterval(queuePollTimer);
  $('#queueStatus').classList.add('hidden');
  $('#autoMatchBtn').disabled=false;
  $('#autoMatchBtn').textContent='自动匹配';
  await load();
};
load().catch(e=>{$('#botGrid').innerHTML=`<p class="muted">加载失败：${e.message}</p>`});