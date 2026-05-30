const matchId=document.querySelector('.match-shell').dataset.matchId;
const STORAGE_KEY='chessArenaClientSettings';
const $=s=>document.querySelector(s);
const names={r:'車',h:'馬',e:'象',a:'士',k:'将',c:'砲',p:'卒',R:'車',H:'馬',E:'相',A:'仕',K:'帅',C:'炮',P:'兵'};
function cfg(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function authHeaders(){const t=(cfg().token||'').trim();return t?{Authorization:'Bearer '+t}:{}}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
let cachedRedName='',cachedBlackName='',matchPaused=false;
let cachedRedAvatar='',cachedBlackAvatar='';
let capturedByRed=[]; // pieces red has captured (black pieces)
let capturedByBlack=[]; // pieces black has captured (red pieces)

// ── Avatar helpers ──
function avatarGradient(name){
  let h=0;for(let i=0;i<(name||'?').length;i++)h=(name.charCodeAt(i)+((h<<5)-h))|0;
  const hue=Math.abs(h)%360;
  return `linear-gradient(135deg,hsl(${hue},60%,50%),hsl(${(hue+35)%360},55%,38%))`;
}
function renderAvatarEl(el,name,avatarUrl,cls){
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
async function load(){const r=await fetch(`/api/admin/matches/${matchId}`); if(!r.ok)throw new Error(await r.text()); const m=await r.json(); cachedRedName=m.red_bot_name; cachedBlackName=m.black_bot_name; cachedRedAvatar=m.red_bot_avatar_url||''; cachedBlackAvatar=m.black_bot_avatar_url||''; matchPaused=!!m.paused; capturedByRed=[]; capturedByBlack=[]; (m.moves||[]).forEach(function(mv){if(mv.captured){if(mv.side==='red')capturedByRed.push(mv.captured);else capturedByBlack.push(mv.captured);}}); render(m)}
function render(m){
  $('#matchStatus').textContent=`${m.status} · ${m.result||'进行中'} · ${m.ply}手`; $('#updatedAt').textContent=' · '+new Date((m.updated_at||0)*1000).toLocaleString();
  $('#redName').textContent=m.red_bot_name||m.red_bot_id; $('#redId').textContent=m.red_bot_id; $('#blackName').textContent=m.black_bot_name||m.black_bot_id; $('#blackId').textContent=m.black_bot_id;
  renderAvatarEl($('#redAvatar'),m.red_bot_name||'帅',m.red_bot_avatar_url,'big'); renderAvatarEl($('#blackAvatar'),m.black_bot_name||'将',m.black_bot_avatar_url,'big dark');
  matchPaused=!!m.paused;
  updateTurnBannerContent(m.status,m.turn,m.ply,m.paused,m.result,m.red_bot_name,m.black_bot_name);
  $('#fenText').textContent=m.fen; renderBoard(m.fen); renderCaptured(); renderMoves(m);
}
function updateTurnBannerContent(status,turn,ply,paused,result,redName,blackName){
  if(status==='active'){
    if(paused){
      $('#turnBanner').textContent='⏸ 对局已暂停';
    }else{
      $('#turnBanner').textContent=`轮到${turn==='red'?'红方':'黑方'}：${turn==='red'?redName:blackName}`;
    }
  }else{
    $('#turnBanner').textContent=`对局已结束：${result||status}`;
  }
}
function renderBoard(fen){
  const board=$('#board'); board.innerHTML=''; const pieceMap={}; const layout=fen.split(' ')[0]; let row=0,col=0;
  for(const ch of layout){ if(ch==='/'){row++;col=0;continue} if(/\d/.test(ch)){col+=Number(ch);continue} pieceMap[`${row},${col}`]=ch; col++; }
  // Cannon/pawn starting position markers (row -> [cols])
  const markerRows={2:[1,7],7:[1,7],3:[0,2,4,6,8],6:[0,2,4,6,8]};
  // Coordinate labels: file labels a-i on bottom, rank labels 0-9 on right
  for(let r=0;r<10;r++)for(let c=0;c<9;c++){
    const cell=document.createElement('div'); cell.className='xq-cell';
    if(c===0) cell.classList.add('edge-l'); if(c===8) cell.classList.add('edge-r'); if(r===0) cell.classList.add('edge-t'); if(r===9) cell.classList.add('edge-b');
    if((r===4||r===5)&&c>0&&c<8) cell.classList.add('river-gap');
    if(r===4&&c===1){ cell.classList.add('river-left'); cell.innerHTML='<span class="river-text">楚河</span>'; }
    if(r===4&&c===6){ cell.classList.add('river-right'); cell.innerHTML='<span class="river-text">汉界</span>'; }
    // Palace diagonals: draw only on center cell of each palace, lines span full 3x3
    if((r===1&&c===4)||(r===8&&c===4)){
      const d1=document.createElement('span'); d1.className='palace-line d1'; const d2=document.createElement('span'); d2.className='palace-line d2'; cell.append(d1,d2);
    }
    // Cannon/pawn marker dots
    if(markerRows[r]&&markerRows[r].includes(c)){
      const lShape=document.createElement('span'); lShape.className='board-marker';
      if(c===0||c===2||c===4||c===6||c===8){ // pawn position — corner markers
        lShape.classList.add('marker-corner');
      }else{ // cannon position — diamond/cross
        lShape.classList.add('marker-diamond');
      }
      cell.appendChild(lShape);
    }
    // File labels on bottom row (row 9 = red home), rank labels on rightmost column
    if(r===9){ cell.setAttribute('data-coord','file-'+'abcdefghi'[c]); }
    if(c===8){ cell.setAttribute('data-coord','rank-'+r); }
    board.appendChild(cell);
  }
  // Place pieces on top
  const cells=board.querySelectorAll('.xq-cell');
  for(const [key,ch] of Object.entries(pieceMap)){
    const [r,c]=key.split(',').map(Number);
    const idx=r*9+c;
    if(idx<cells.length){
      const pc=document.createElement('div');pc.className='piece '+(ch===ch.toUpperCase()?'red':'black');pc.textContent=names[ch]||ch;cells[idx].appendChild(pc);
    }
  }
}
function renderCaptured(){
  function makeIcons(pieces,cls){
    return pieces.map(p=>{
      const el=document.createElement('span');
      el.className='captured-icon '+(p===p.toUpperCase()?'red':'black');
      el.textContent=names[p]||p;
      return el.outerHTML;
    }).join('');
  }
  const rc=$('#redCaptured'); if(rc)rc.innerHTML='<span class="captured-label">红方吃子：</span>'+makeIcons(capturedByRed,'black');
  const bc=$('#blackCaptured'); if(bc)bc.innerHTML='<span class="captured-label">黑方吃子：</span>'+makeIcons(capturedByBlack,'red');
}
function moveAvatarHtml(name,avatarUrl){
  if(avatarUrl){return `<img class="move-avatar-img" src="${esc(avatarUrl)}" alt="${esc(name)}" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex'"><span class="move-avatar-txt" style="display:none;background:${avatarGradient(name)}">${esc((name||'?').slice(0,1))}</span>`;}
  return `<span class="move-avatar-txt" style="background:${avatarGradient(name)}">${esc((name||'?').slice(0,1))}</span>`;
}
function renderMovesFromSSE(moves,last){
  const box=$('#moves'); if(!moves||!moves.length){box.innerHTML='<p class="muted">暂无走法，等待 Bot 出招。</p>';return}
  box.innerHTML=moves.slice().reverse().map(mv=>{const name=mv.side==='red'?(cachedRedName||'红方'):(cachedBlackName||'黑方');const avUrl=mv.side==='red'?cachedRedAvatar:cachedBlackAvatar;const avHtml=moveAvatarHtml(name,avUrl); const line=mv.comment||'（没有台词）'; return `<div class="move"><b>#${esc(mv.ply)} ${mv.side==='red'?'红':'黑'} ${esc(name)}</b><br><code>${esc(mv.move)}</code>${mv.captured?` · 吃 ${esc(names[mv.captured]||mv.captured)}`:''}<p class="comment">${avHtml} ${esc(line)}</p></div>`}).join('');
  if(last){ if(last.side==='red')$('#redLine').textContent=last.comment||'刚走了一步。'; else $('#blackLine').textContent=last.comment||'刚走了一步。'; }
}
function renderMoves(m){
  const box=$('#moves'); const moves=m.moves||[]; if(!moves.length){box.innerHTML='<p class="muted">暂无走法，等待 Bot 出招。</p>';return}
  box.innerHTML=moves.slice().reverse().map(mv=>{const name=mv.side==='red'?m.red_bot_name:m.black_bot_name;const avUrl=mv.side==='red'?m.red_bot_avatar_url:m.black_bot_avatar_url;const avHtml=moveAvatarHtml(name,avUrl); const line=mv.comment||'（没有台词）'; return `<div class="move"><b>#${esc(mv.ply)} ${mv.side==='red'?'红':'黑'} ${esc(name)}</b><br><code>${esc(mv.move)}</code>${mv.captured?` · 吃 ${esc(names[mv.captured]||mv.captured)}`:''}<p class="comment">${avHtml} ${esc(line)}</p></div>`}).join('');
  const last=moves[moves.length-1]; if(last){ if(last.side==='red')$('#redLine').textContent=last.comment||'刚走了一步。'; else $('#blackLine').textContent=last.comment||'刚走了一步。'; }
}
function updateStatusFromSSE(status,result,ply,paused){
  $('#matchStatus').textContent=`${status} · ${result||'进行中'} · ${ply}手`;
  matchPaused=!!paused;
  if(status!=='active'){
    $('#turnBanner').textContent=`对局已结束：${result||status}`;
    // Trigger game-over popup
    if(!gameIsOver){
      // We'll get moves from the SSE data separately, but set a flag for checkGameOver
      window._sseEndStatus=status;
      window._sseEndResult=result;
    }
  }else if(paused){
    $('#turnBanner').textContent='⏸ 对局已暂停';
  }
}
function updateTurnBanner(ply,paused){
  if(paused){$('#turnBanner').textContent='⏸ 对局已暂停'; return;}
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
async function pauseMatch(){
  const h=authHeaders(); if(!h.Authorization){alert('先去个人设置填入本局任一参与 Bot 的 token。'); location.href='/settings'; return;}
  const r=await fetch(`/api/matches/${matchId}/pause`,{method:'POST',headers:h});
  if(!r.ok){const text=await r.text(); alert('暂停失败：'+text); return;}
  const data=await r.json();
  matchPaused=!!data.paused;
  if(data.match){
    updateTurnBannerContent('active',data.match.turn,data.match.ply,matchPaused,null,cachedRedName,cachedBlackName);
  }
}
const stopBtn=$('#stopMatchBtn'); if(stopBtn)stopBtn.onclick=()=>stopMatch().catch(e=>alert('停止异常：'+e.message));
const pauseBtn=$('#pauseMatchBtn'); if(pauseBtn)pauseBtn.onclick=()=>pauseMatch().catch(e=>alert('暂停异常：'+e.message));

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
        // Audio: process moves and status changes
        if(typeof handleSSEAudio==='function')handleSSEAudio(d);
        if(d.fen){renderBoard(d.fen); $('#fenText').textContent=d.fen;}
        if(d.status!=null&&d.result!=null&&d.ply!=null){updateStatusFromSSE(d.status,d.result,d.ply,d.paused);}
        if(d.moves){
          renderMovesFromSSE(d.moves,d.last_move||d.moves[d.moves.length-1]);
          // Rebuild captured lists from moves
          capturedByRed=[]; capturedByBlack=[];
          (d.moves||[]).forEach(function(mv){if(mv.captured){if(mv.side==='red')capturedByRed.push(mv.captured);else capturedByBlack.push(mv.captured);}});
          renderCaptured();
        }
        if(d.ply!=null&&d.status==='active'){
          matchPaused=!!d.paused;
          updateTurnBanner(d.ply,matchPaused);
        }
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
// Initialize audio toggles
setTimeout(() => { if (typeof createAudioToggles === 'function') createAudioToggles($('#audioToggleContainer')); }, 100);
setTimeout(()=>connectSSE(),500);
// Fallback polling (every 5s, only if SSE hasn't updated recently)
let lastSseUpdate=Date.now();
setInterval(()=>{if(Date.now()-lastSseUpdate>10000){load().catch(()=>{})}},5000);

// ═══════════════════════════════════════════
// ── Replay Controller ──
// ═══════════════════════════════════════════
const INITIAL_FEN='rnbakabnr/9/1c5c1/p1p1p1p1p/9/9/P1P1P1P1P/1C5C1/9/RNBAKABNR w - - 0 1';
let fenHistory=[];      // FEN at each step: [initialFEN, afterMove1FEN, ..., afterMoveNFEN]
let replayStep=-1;      // -1 = initial position, 0..N-1 = after move N
let replayPlaying=false;
let replayTimer=null;
let replaySpeed=1000;
let allMoves=[];        // cache of moves for replay
let gameIsOver=false;   // track if game has ended
let popupShown=false;   // prevent duplicate popup

// ── FEN utilities ──
function parseFenBoard(fen){
  const rows=fen.split(' ')[0].split('/');
  const board=[];
  for(let r=0;r<10;r++){
    board[r]=Array(9).fill('');
    let c=0;
    for(const ch of rows[r]){
      if(/[1-9]/.test(ch)){c+=parseInt(ch);continue;}
      board[r][c]=ch;c++;
    }
  }
  return board;
}
function boardToFenRow(board){
  return board.map(row=>{
    let s='',empty=0;
    for(let c=0;c<9;c++){
      if(row[c]===''){empty++;continue;}
      if(empty){s+=empty;empty=0;}
      s+=row[c];
    }
    if(empty)s+=empty;
    return s;
  }).join('/');
}
function applyUcciMove(fen,ucci){
  // ucci like "h2e2": from file_h=7 rank_2 to file_e=4 rank_2
  const parts=fen.split(' ');
  const board=parseFenBoard(fen);
  const turn=parts[1]||'w';
  const fromFile=ucci.charCodeAt(0)-97; // a=0..i=8
  const fromRank=parseInt(ucci[1]);
  const toFile=ucci.charCodeAt(2)-97;
  const toRank=parseInt(ucci[3]);
  const piece=board[fromRank][fromFile];
  if(!piece||piece==='')return fen; // safety
  // Check if there's a piece at target (capture)
  const captured=board[toRank][toFile];
  board[fromRank][fromFile]='';
  board[toRank][toFile]=piece;
  const newTurn=turn==='w'?'b':'w';
  return boardToFenRow(board)+' '+newTurn+' - - 0 1';
}
function buildFenHistory(moves,startFen){
  const history=[startFen];
  let fen=startFen;
  for(const mv of moves){
    fen=applyUcciMove(fen,mv.move);
    history.push(fen);
  }
  return history;
}

// ── Replay control ──
function initReplay(moves){
  allMoves=moves||[];
  fenHistory=buildFenHistory(allMoves,INITIAL_FEN);
  replayStep=allMoves.length-1; // default to last position (show final state)
  replayPlaying=false;
  $('#replayBar').classList.add('visible');
  updateReplayUI();
  updateReplayBoard();
}
function updateReplayUI(){
  const total=allMoves.length;
  $('#replayInfo').textContent=(replayStep+1)+' / '+total+' 步';
  if(replayStep>=0&&replayStep<allMoves.length){
    const mv=allMoves[replayStep];
    const side=mv.side==='red'?'红':'黑';
    $('#replayMoveLabel').textContent='#'+mv.ply+' '+side+' '+mv.move;
  }else if(replayStep===-1){
    $('#replayMoveLabel').textContent='初始局面';
  }else{
    $('#replayMoveLabel').textContent='';
  }
  // Highlight move in sidebar
  const moveEls=$('#moves').querySelectorAll('.move');
  moveEls.forEach(function(el,i){
    // moves are rendered in reverse order
    const moveIdx=allMoves.length-1-i;
    el.classList.toggle('replay-current',moveIdx===replayStep);
  });
  // Play button state
  const playBtn=$('#replayPlayBtn');
  if(playBtn)playBtn.textContent=replayPlaying?'⏸':'▶';
  // Disable prev/next at bounds
  const prevBtn=$('#replayPrevBtn'); if(prevBtn)prevBtn.disabled=replayStep<=-1;
  const nextBtn=$('#replayNextBtn'); if(nextBtn)nextBtn.disabled=replayStep>=allMoves.length-1;
  const startBtn=$('#replayStartBtn'); if(startBtn)startBtn.disabled=replayStep<=-1;
  const endBtn=$('#replayEndBtn'); if(endBtn)endBtn.disabled=replayStep>=allMoves.length-1;
}
function updateReplayBoard(){
  let fen;
  if(replayStep===-1){
    fen=fenHistory[0]||INITIAL_FEN;
  }else if(replayStep>=0&&replayStep<fenHistory.length-1){
    fen=fenHistory[replayStep+1]; // fenHistory[0]=initial, [1]=after move 0
  }else{
    fen=fenHistory[fenHistory.length-1];
  }
  renderBoard(fen);
  $('#fenText').textContent=fen;
  // Update captured pieces for the replay position
  const capturedR=[],capturedB=[];
  for(let i=0;i<=replayStep&&i<allMoves.length;i++){
    const mv=allMoves[i];
    if(mv.captured){
      if(mv.side==='red')capturedR.push(mv.captured);
      else capturedB.push(mv.captured);
    }
  }
  // Quick render captured
  function makeIcons(pieces,cls){
    return pieces.map(p=>{
      const ch=names[p]||p;
      return '<span class="captured-icon '+(p===p.toUpperCase()?'red':'black')+'">'+ch+'</span>';
    }).join('');
  }
  const rc=$('#redCaptured'); if(rc)rc.innerHTML='<span class="captured-label">红方吃子：</span>'+makeIcons(capturedR,'black');
  const bc=$('#blackCaptured'); if(bc)bc.innerHTML='<span class="captured-label">黑方吃子：</span>'+makeIcons(capturedB,'red');
}
function goToStep(n){
  replayStep=Math.max(-1,Math.min(allMoves.length-1,n));
  updateReplayUI();
  updateReplayBoard();
}
function startReplayPlayback(){
  if(replayPlaying)return;
  if(replayStep>=allMoves.length-1)goToStep(-1); // restart from beginning
  replayPlaying=true;
  updateReplayUI();
  replayTimer=setInterval(function(){
    if(replayStep>=allMoves.length-1){
      stopReplayPlayback();
      return;
    }
    goToStep(replayStep+1);
  },replaySpeed);
}
function stopReplayPlayback(){
  replayPlaying=false;
  if(replayTimer){clearInterval(replayTimer);replayTimer=null;}
  updateReplayUI();
}
function toggleReplayPlayback(){
  if(replayPlaying)stopReplayPlayback();
  else startReplayPlayback();
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown',function(e){
  if(!gameIsOver)return; // only in replay mode
  // Don't capture if user is typing in an input
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA'||e.target.tagName==='SELECT')return;
  if(e.key==='ArrowLeft'){e.preventDefault();stopReplayPlayback();goToStep(replayStep-1);}
  else if(e.key==='ArrowRight'){e.preventDefault();stopReplayPlayback();goToStep(replayStep+1);}
  else if(e.key===' '){e.preventDefault();toggleReplayPlayback();}
  else if(e.key==='Home'){e.preventDefault();stopReplayPlayback();goToStep(-1);}
  else if(e.key==='End'){e.preventDefault();stopReplayPlayback();goToStep(allMoves.length-1);}
});

// ── Replay button handlers ──
$('#replayStartBtn').onclick=function(){stopReplayPlayback();goToStep(-1);};
$('#replayPrevBtn').onclick=function(){stopReplayPlayback();goToStep(replayStep-1);};
$('#replayPlayBtn').onclick=function(){toggleReplayPlayback();};
$('#replayNextBtn').onclick=function(){stopReplayPlayback();goToStep(replayStep+1);};
$('#replayEndBtn').onclick=function(){stopReplayPlayback();goToStep(allMoves.length-1);};
$('#replaySpeed').onchange=function(){replaySpeed=parseInt(this.value);if(replayPlaying){stopReplayPlayback();startReplayPlayback();}};

// ═══════════════════════════════════════════
// ── Victory/Defeat Popup ──
// ═══════════════════════════════════════════
function resultToReadable(result,reason){
  const map={
    'red_win':'红方胜','black_win':'黑方胜','draw':'和棋',
    'checkmate':'将死','timeout':'超时判负','resign':'认输',
    'move_limit':'步数限制','draw_agreed':'协议和棋','stalemate':'困毙和棋'
  };
  return map[result]||map[reason]||result||reason||'对局结束';
}
function showGameOverPopup(result,reason,redName,blackName,redAv,blackAv){
  if(popupShown)return;
  popupShown=true;
  const overlay=$('#gameOverPopup');
  if(!overlay)return;
  // Set icon
  let icon='🏆',color='var(--gold)';
  if(result==='red_win')icon='🔴';else if(result==='black_win')icon='⚫';else if(result==='draw')icon='🤝';
  $('#popupIcon').textContent=icon;
  // Title
  const title=resultToReadable(result);
  $('#popupTitle').textContent=title;
  if(result==='red_win')$('#popupTitle').style.color='var(--red)';
  else if(result==='black_win')$('#popupTitle').style.color='var(--black)';
  else $('#popupTitle').style.color=color;
  // Reason
  const reasonMap={'checkmate':'黑方被将死，红方获胜！','timeout':'对方超时，自动判负','resign':'对方主动认输','move_limit':'达到步数上限','draw_agreed':'双方同意和棋','stalemate':'无子可动，和棋'};
  let reasonText=reasonMap[reason]||(resultToReadable(result,reason)+' — '+((reasonMap[reason]||reason||'')));
  if(!reasonText||reasonText===title)reasonText='';
  $('#popupReason').textContent=reasonText||'';
  // Players
  $('#popupRedName').textContent=redName||'红方';
  $('#popupBlackName').textContent=blackName||'黑方';
  renderAvatarEl($('#popupRedAvatar'),redName||'帅',redAv,'');
  renderAvatarEl($('#popupBlackAvatar'),blackName||'将',blackAv,'dark');
  // Winner highlight
  const redPl=$('#popupRedPlayer'),blackPl=$('#popupBlackPlayer');
  redPl.classList.remove('popup-winner');blackPl.classList.remove('popup-winner');
  if(result==='red_win')redPl.classList.add('popup-winner');
  else if(result==='black_win')blackPl.classList.add('popup-winner');
  // Show
  overlay.classList.add('active');
  overlay.classList.remove('closing');
  // Particles
  spawnParticles(result);
  // Init replay
  if(allMoves.length>0)initReplay(allMoves);
}
function hideGameOverPopup(){
  const overlay=$('#gameOverPopup');
  if(!overlay)return;
  overlay.classList.add('closing');
  overlay.classList.remove('active');
  setTimeout(function(){overlay.classList.remove('closing');},400);
}
function spawnParticles(result){
  const container=$('#popupParticles');
  if(!container)return;
  container.innerHTML='';
  const colors=result==='red_win'?['#c7372f','#e74c3c','#f39c12','#f1c40f','#fff']:
    result==='black_win'?['#333','#555','#888','#aaa','#ddd']:
    ['#b78943','#d4a56a','#f1c40f','#e67e22','#fff'];
  for(let i=0;i<50;i++){
    const p=document.createElement('div');
    p.className='particle';
    const size=4+Math.random()*8;
    p.style.width=size+'px';p.style.height=size+'px';
    p.style.background=colors[Math.floor(Math.random()*colors.length)];
    p.style.left=(5+Math.random()*90)+'%';
    p.style.top=-(10+Math.random()*20)+'%';
    p.style.animationDuration=(1.5+Math.random()*2.5)+'s';
    p.style.animationDelay=Math.random()*.8+'s';
    container.appendChild(p);
  }
  setTimeout(function(){container.innerHTML='';},3500);
}

// ── Popup buttons ──
$('#popupRematchBtn').onclick=function(){
  hideGameOverPopup();
  // Try to create a rematch: redirect to arena with pre-selection
  location.href='/arena';
};
$('#popupLobbyBtn').onclick=function(){
  hideGameOverPopup();
  location.href='/arena';
};
// Click outside popup closes it
$('#gameOverPopup').addEventListener('click',function(e){
  if(e.target===this)hideGameOverPopup();
});

// ═══════════════════════════════════════════
// ── UCCI & Chinese Notation Export ──
// ═══════════════════════════════════════════
const RED_COL=['九','八','七','六','五','四','三','二','一']; // file a..i -> Chinese for red
const RED_ROW=['十','九','八','七','六','五','四','三','二','一']; // row 0..9 -> Chinese for red
const BLK_COL=['1','2','3','4','5','6','7','8','9'];
const BLK_ROW=['1','2','3','4','5','6','7','8','9','10'];
const PIECE_CN={r:'車',R:'車',h:'馬',H:'馬',e:'象',E:'相',a:'士',A:'仕',k:'将',K:'帅',c:'砲',C:'炮',p:'卒',P:'兵'};
// Check if a piece type uses file-based target in vertical moves (horse, elephant, advisor)
function usesFileTarget(piece){
  const t=piece.toUpperCase();
  return t==='H'||t==='E'||t==='A'; // horse, elephant, advisor
}
function ucciToChinese(ucci,side,fensBefore){
  // fensBefore: FEN before this move
  if(!ucci||ucci.length<4)return ucci;
  const fromF=ucci.charCodeAt(0)-97;
  const fromR=parseInt(ucci[1]);
  const toF=ucci.charCodeAt(2)-97;
  const toR=parseInt(ucci[3]);
  // Get piece from board
  let piece='';
  try{
    const board=parseFenBoard(fensBefore);
    piece=board[fromR][fromF]||'';
  }catch(e){}
  const pieceName=PIECE_CN[piece]||(piece===piece.toUpperCase()?'兵':'卒');
  if(side==='red'){
    const srcCol=RED_COL[fromF];
    if(fromF===toF){
      // Vertical move
      const dir=toR<fromR?'进':'退';
      if(usesFileTarget(piece)){
        return pieceName+srcCol+dir+RED_COL[toF];
      }else{
        return pieceName+srcCol+dir+RED_ROW[toR];
      }
    }else{
      return pieceName+srcCol+'平'+RED_COL[toF];
    }
  }else{
    const srcCol=BLK_COL[fromF];
    if(fromF===toF){
      const dir=toR>fromR?'进':'退';
      if(usesFileTarget(piece)){
        return pieceName+srcCol+dir+BLK_COL[toF];
      }else{
        return pieceName+srcCol+dir+BLK_ROW[toR];
      }
    }else{
      return pieceName+srcCol+'平'+BLK_COL[toF];
    }
  }
}
function buildUcciText(moves,fensBefore){
  let lines=[];
  moves.forEach(function(mv,i){
    const fen=fensBefore&&i<fensBefore.length?fensBefore[i]:null;
    const cn=fen?ucciToChinese(mv.move,mv.side,fen):'';
    lines.push('#'+mv.ply+' '+mv.move+' ['+(mv.side==='red'?'红':'黑')+']'+(cn?' '+cn:'')+(mv.captured?' 吃'+mv.captured:''));
  });
  return lines.join('\n');
}
function buildChineseText(moves,fensBefore){
  let lines=[];
  moves.forEach(function(mv,i){
    const fen=fensBefore&&i<fensBefore.length?fensBefore[i]:null;
    const cn=fen?ucciToChinese(mv.move,mv.side,fen):mv.move;
    const prefix=(mv.ply%2===1)?((Math.floor(mv.ply/2)+1)+'. '):'   ';
    lines.push(prefix+cn);
  });
  return lines.join('\n');
}
function buildCombinedText(moves,fensBefore,redName,blackName){
  let lines=[];
  lines.push('红方：'+redName+'  黑方：'+blackName);
  lines.push('');
  lines.push('=== UCCI 棋谱 ===');
  lines.push(buildUcciText(moves,fensBefore));
  lines.push('');
  lines.push('=== 中文棋谱 ===');
  lines.push(buildChineseText(moves,fensBefore));
  return lines.join('\n');
}
function showExportDialog(title,content){
  $('#exportTitle').textContent=title;
  $('#exportContent').textContent=content;
  $('#exportDialog').style.display='flex';
}
function hideExportDialog(){
  $('#exportDialog').style.display='none';
}
// Export buttons in sidebar
$('#exportUcciBtn').onclick=function(){
  if(!allMoves.length){alert('暂无走法可导出');return;}
  // fenHistory[1..N] = FEN after move 0..N-1. For ucciToChinese we need FEN BEFORE move.
  // fenHistory[0]=initial, fenHistory[1]=after move 0, so FEN before move i is fenHistory[i]
  const fensBefore=fenHistory.length>1?fenHistory.slice(0,fenHistory.length-1):[INITIAL_FEN];
  showExportDialog('UCCI 棋谱',buildUcciText(allMoves,fensBefore));
};
$('#exportChineseBtn').onclick=function(){
  if(!allMoves.length){alert('暂无走法可导出');return;}
  const fensBefore=fenHistory.length>1?fenHistory.slice(0,fenHistory.length-1):[INITIAL_FEN];
  showExportDialog('中文棋谱',buildChineseText(allMoves,fensBefore));
};
$('#exportDownloadBtn').onclick=function(){
  if(!allMoves.length){alert('暂无走法可导出');return;}
  const fensBefore=fenHistory.length>1?fenHistory.slice(0,fenHistory.length-1):[INITIAL_FEN];
  const text=buildCombinedText(allMoves,fensBefore,cachedRedName,cachedBlackName);
  const blob=new Blob([text],{type:'text/plain;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download='chess-record-'+matchId+'.txt';
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
};
// Export dialog close/copy
$('#exportCloseBtn').onclick=hideExportDialog;
$('#exportDialog').addEventListener('click',function(e){if(e.target===this)hideExportDialog();});
$('#exportCopyBtn').onclick=function(){
  const text=$('#exportContent').textContent;
  if(navigator.clipboard){
    navigator.clipboard.writeText(text).then(function(){alert('已复制到剪贴板！');}).catch(function(){alert('复制失败');});
  }else{
    // Fallback
    const ta=document.createElement('textarea');ta.value=text;
    document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);
    alert('已复制到剪贴板！');
  }
};

// ═══════════════════════════════════════════
// ── Game Over Detection Hooks ──
// ═══════════════════════════════════════════
function checkGameOver(status,result,reason,moves,redName,blackName,redAv,blackAv){
  if(status==='active'||gameIsOver)return;
  gameIsOver=true;
  allMoves=moves||[];
  // Show popup with slight delay for dramatic effect
  setTimeout(function(){
    showGameOverPopup(result,reason,redName,blackName,redAv,blackAv);
  },600);
}

// ── Patch the SSE match_state handler inline by wrapping after connectSSE ──
// The SSE handler already calls updateStatusFromSSE which sets _sseEndStatus.
// We listen inside the SSE for moves and trigger checkGameOver when done.
const _origRenderMovesFromSSE=renderMovesFromSSE;
renderMovesFromSSE=function(moves,last){
  _origRenderMovesFromSSE(moves,last);
  // If we have end status flag and moves, trigger game over
  if(window._sseEndStatus&&window._sseEndResult&&moves&&moves.length>0&&!gameIsOver){
    checkGameOver(window._sseEndStatus,window._sseEndResult,'',
      moves,cachedRedName,cachedBlackName,cachedRedAvatar,cachedBlackAvatar);
    delete window._sseEndStatus;
    delete window._sseEndResult;
  }
};

// ── Delayed initial load check for games already over ──
setTimeout(async function(){
  if(gameIsOver)return;
  try{
    const r=await fetch(`/api/admin/matches/${matchId}`);
    if(!r.ok)return;
    const m=await r.json();
    if(m.status!=='active'){
      checkGameOver(m.status,m.result,m.end_reason||'',m.moves||[],
        m.red_bot_name||'',m.black_bot_name||'',
        m.red_bot_avatar_url||'',m.black_bot_avatar_url||'');
    }
  }catch(e){}
},1500);
