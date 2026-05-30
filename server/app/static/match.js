const matchId=document.querySelector('.match-shell').dataset.matchId;
const STORAGE_KEY='chessArenaClientSettings';
const $=s=>document.querySelector(s);
const names={r:'車',h:'馬',e:'象',a:'士',k:'将',c:'砲',p:'卒',R:'車',H:'馬',E:'相',A:'仕',K:'帅',C:'炮',P:'兵'};
function cfg(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function authHeaders(){const t=(cfg().token||'').trim();return t?{Authorization:'Bearer '+t}:{}}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
let cachedRedName='',cachedBlackName='';
async function load(){const r=await fetch(`/api/admin/matches/${matchId}`); if(!r.ok)throw new Error(await r.text()); const m=await r.json(); cachedRedName=m.red_bot_name; cachedBlackName=m.black_bot_name; render(m)}
function render(m){
  $('#matchStatus').textContent=`${m.status} · ${m.result||'进行中'} · ${m.ply}手`; $('#updatedAt').textContent=' · '+new Date((m.updated_at||0)*1000).toLocaleString();
  $('#redName').textContent=m.red_bot_name||m.red_bot_id; $('#redId').textContent=m.red_bot_id; $('#blackName').textContent=m.black_bot_name||m.black_bot_id; $('#blackId').textContent=m.black_bot_id;
  $('#redAvatar').textContent=(m.red_bot_name||'帅').slice(0,1); $('#blackAvatar').textContent=(m.black_bot_name||'将').slice(0,1);
  $('#turnBanner').textContent=m.status==='active'?`轮到${m.turn==='red'?'红方':'黑方'}：${m.turn==='red'?m.red_bot_name:m.black_bot_name}`:`对局已结束：${m.result||m.status}`;
  $('#fenText').textContent=m.fen; renderBoard(m.fen); renderMoves(m);
}
function renderBoard(fen){
  const board=$('#board'); board.innerHTML=''; const pieceMap={}; const layout=fen.split(' ')[0]; let row=0,col=0;
  for(const ch of layout){ if(ch==='/'){row++;col=0;continue} if(/\d/.test(ch)){col+=Number(ch);continue} pieceMap[`${row},${col}`]=ch; col++; }
  for(let r=0;r<10;r++)for(let c=0;c<9;c++){
    const cell=document.createElement('div'); cell.className='xq-cell';
    if(c===0) cell.classList.add('edge-l'); if(c===8) cell.classList.add('edge-r'); if(r===0) cell.classList.add('edge-t'); if(r===9) cell.classList.add('edge-b');
    if((r===4||r===5)&&c>0&&c<8) cell.classList.add('river-gap');
    if(r===4&&c===1){ cell.classList.add('river-left'); cell.innerHTML='<span class="river-text">楚河</span>'; }
    if(r===4&&c===6){ cell.classList.add('river-right'); cell.innerHTML='<span class="river-text">汉界</span>'; }
    if((r===1||r===8)&&c===4){ const d1=document.createElement('span'); d1.className='palace-line d1'; const d2=document.createElement('span'); d2.className='palace-line d2'; cell.append(d1,d2); }
    const p=pieceMap[`${r},${c}`]; if(p){const pc=document.createElement('div');pc.className='piece '+(p===p.toUpperCase()?'red':'black');pc.textContent=names[p]||p;cell.appendChild(pc)}
    board.appendChild(cell)
  }
}
function renderMovesFromSSE(moves,last){
  const box=$('#moves'); if(!moves||!moves.length){box.innerHTML='<p class="muted">暂无走法，等待 Bot 出招。</p>';return}
  box.innerHTML=moves.slice().reverse().map(mv=>{const name=mv.side==='red'?(cachedRedName||'红方'):(cachedBlackName||'黑方'); const line=mv.comment||'（没有台词）'; return `<div class="move"><b>#${esc(mv.ply)} ${mv.side==='red'?'红':'黑'} ${esc(name)}</b><br><code>${esc(mv.move)}</code>${mv.captured?` · 吃 ${esc(names[mv.captured]||mv.captured)}`:''}<p class="comment">${esc(line)}</p></div>`}).join('');
  if(last){ if(last.side==='red')$('#redLine').textContent=last.comment||'刚走了一步。'; else $('#blackLine').textContent=last.comment||'刚走了一步。'; }
}
function renderMoves(m){
  const box=$('#moves'); const moves=m.moves||[]; if(!moves.length){box.innerHTML='<p class="muted">暂无走法，等待 Bot 出招。</p>';return}
  box.innerHTML=moves.slice().reverse().map(mv=>{const name=mv.side==='red'?m.red_bot_name:m.black_bot_name; const line=mv.comment||'（没有台词）'; return `<div class="move"><b>#${esc(mv.ply)} ${mv.side==='red'?'红':'黑'} ${esc(name)}</b><br><code>${esc(mv.move)}</code>${mv.captured?` · 吃 ${esc(names[mv.captured]||mv.captured)}`:''}<p class="comment">${esc(line)}</p></div>`}).join('');
  const last=moves[moves.length-1]; if(last){ if(last.side==='red')$('#redLine').textContent=last.comment||'刚走了一步。'; else $('#blackLine').textContent=last.comment||'刚走了一步。'; }
}
function updateStatusFromSSE(status,result,ply){
  $('#matchStatus').textContent=`${status} · ${result||'进行中'} · ${ply}手`;
  if(status!=='active'){
    $('#turnBanner').textContent=`对局已结束：${result||status}`;
  }
}
function updateTurnBanner(ply){
  const turn=ply%2===0?'red':'black';
  $('#turnBanner').textContent=`轮到${turn==='red'?'红方':'黑方'}：${turn==='red'?cachedRedName:cachedBlackName}`;
}
async function stopMatch(){
  if(!confirm('确定停止这局吗？停止后双方 Bot 不会继续下。')) return;
  const h=authHeaders(); if(!h.Authorization){alert('先去个人设置填入本局任一参与 Bot 的 token。'); location.href='/settings'; return;}
  const r=await fetch(`/api/matches/${matchId}/stop`,{method:'POST',headers:h});
  const text=await r.text(); if(!r.ok){alert('停止失败：'+text); return;}
  await load(); alert('已停止本局');
}
const stopBtn=$('#stopMatchBtn'); if(stopBtn)stopBtn.onclick=()=>stopMatch().catch(e=>alert('停止异常：'+e.message));

// ── SSE Spectator ──
function connectSSE(){
  const bar=$('#sseStatus'); if(!bar)return;
  bar.innerHTML='<span class="pulse"></span> 连接中…'; bar.style.color='var(--muted)';
  try{
    const es=new EventSource('/sse/match/'+matchId);
    es.addEventListener('match_state',e=>{
      lastSseUpdate=Date.now();
      bar.innerHTML='<span class="pulse"></span> 实时同步中';
      bar.style.color='var(--green)';
      try{
        const d=JSON.parse(e.data);
        if(d.fen){renderBoard(d.fen); $('#fenText').textContent=d.fen;}
        if(d.status!=null&&d.result!=null&&d.ply!=null){updateStatusFromSSE(d.status,d.result,d.ply);}
        if(d.moves){renderMovesFromSSE(d.moves,d.last_move||d.moves[d.moves.length-1]);}
        if(d.ply!=null&&d.status==='active'){updateTurnBanner(d.ply);}
      }catch(err){console.error('SSE parse error',err)}
    });
    es.addEventListener('open',()=>{
      bar.innerHTML='<span class="pulse"></span> 实时同步中';
      bar.style.color='var(--green)';
    });
    es.addEventListener('error',()=>{
      bar.innerHTML='<span class="pulse" style="background:var(--red);animation:none"></span> 连接断开，稍后重试…';
      bar.style.color='var(--red)';
      es.close();
      setTimeout(connectSSE,3000);
    });
    return es;
  }catch(e){
    bar.innerHTML='连接不可用'; bar.style.color='var(--red)';
  }
}
load().catch(e=>{$('#matchStatus').textContent='加载失败：'+e.message});
setTimeout(()=>connectSSE(),500);
// Fallback polling (every 5s, only if SSE hasn't updated recently)
let lastSseUpdate=Date.now();
setInterval(()=>{if(Date.now()-lastSseUpdate>10000){load().catch(()=>{})}},5000);
