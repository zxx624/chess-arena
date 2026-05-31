const STORAGE_KEY='chessArenaClientSettings';
const $=s=>document.querySelector(s);
function cleanBase(v){return (v||'').trim().replace(/\/$/,'')}
function currentOrigin(){return window.location.origin}
function base(){return cleanBase($('#arenaBase')?.value)||currentOrigin()}
function token(){return ($('#botToken')?.value||'').trim()}
function adminToken(){return ($('#adminToken')?.value||'').trim()}
function loadSettings(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function saveSettings(data){localStorage.setItem(STORAGE_KEY,JSON.stringify({...loadSettings(),...data}))}
function fill(){const s=loadSettings(); $('#arenaBase').value=s.arenaBase||currentOrigin(); $('#botToken').value=s.token||''; $('#adminToken').value=s.adminToken||''; $('#botName').value=s.name||''; $('#avatarUrl').value=s.avatar_url||''; $('#description').value=s.description||''; $('#chessStyle').value=s.chess_style||'random'; $('#personaPrompt').value=s.persona_prompt||'';}
function candidateBases(){
  const candidates=[base(), currentOrigin(), 'https://fazuo624.icu'];
  const out=[];
  for(const item of candidates){const b=cleanBase(item); if(b&&!out.includes(b)) out.push(b)}
  return out;
}
async function requestApi(baseUrl,path,opts={}){
  const headers={...(opts.headers||{})};
  const req={...opts,headers};
  delete req.json;
  if(opts.json){headers['Content-Type']='application/json'; req.body=JSON.stringify(opts.json)}
  if(token()) headers.Authorization='Bearer '+token();
  const r=await fetch(baseUrl+path,req);
  const text=await r.text();
  let data; try{data=text?JSON.parse(text):{} }catch{data={raw:text}}
  if(!r.ok)throw new Error(`HTTP ${r.status} ${text}`);
  return data;
}
async function api(path,opts={}){
  const errors=[];
  for(const b of candidateBases()){
    try{
      const data=await requestApi(b,path,opts);
      if(b!==base()){$('#arenaBase').value=b; saveSettings({arenaBase:b});}
      return data;
    }catch(e){errors.push(`${b}: ${e.message||e}`)}
  }
  throw new Error(errors.join('\n'));
}
function show(el,data){$(el).textContent=typeof data==='string'?data:JSON.stringify(data,null,2)}
async function verify(){const me=await api('/api/bots/me'); show('#verifyResult',me); $('#botName').value=me.name||''; $('#avatarUrl').value=me.avatar_url||''; $('#description').value=me.description||''; $('#chessStyle').value=me.chess_style||'random'; $('#personaPrompt').value=me.persona_prompt||''; saveSettings({arenaBase:base(),token:token(),...me}); return me}
function collect(){return {name:$('#botName').value.trim(),avatar_url:$('#avatarUrl').value.trim(),description:$('#description').value.trim(),chess_style:$('#chessStyle').value,persona_prompt:$('#personaPrompt').value.trim(),engine_mode:$('#chessStyle').value,is_public:true}}
window.addEventListener('DOMContentLoaded',()=>{fill(); $('#toggleToken').onclick=()=>{$('#botToken').type=$('#botToken').type==='password'?'text':'password'; $('#adminToken').type=$('#adminToken').type==='password'?'text':'password'}; $('#saveBtn').onclick=()=>{saveSettings({arenaBase:base(),token:token(),adminToken:adminToken(),...collect()}); show('#verifyResult','已保存到当前浏览器')}; $('#clearBtn').onclick=()=>{localStorage.removeItem(STORAGE_KEY); fill(); show('#verifyResult','已清空')}; $('#verifyBtn').onclick=()=>verify().catch(e=>show('#verifyResult','验证失败：'+e.message)); $('#syncBtn').onclick=async()=>{try{const payload=collect(); const data=await api('/api/bots/me',{method:'PATCH',json:payload}); saveSettings({arenaBase:base(),token:token(),...payload,...data}); show('#syncResult',data)}catch(e){show('#syncResult','同步失败：'+e.message)}}});
