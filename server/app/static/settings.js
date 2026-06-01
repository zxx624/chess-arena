const STORAGE_KEY='chessArenaClientSettings';
const AUDIO_KEY='chessArenaAudioSettings';
const $=s=>document.querySelector(s);
const BUILTIN_VOICES=[
  {id:'preset:zh-cn-female-soft',name:'女声 · 温柔普通话',lang:'zh-CN',rate:0.95,pitch:1.08},
  {id:'preset:zh-cn-female-bright',name:'女声 · 活泼清亮',lang:'zh-CN',rate:1.08,pitch:1.18},
  {id:'preset:zh-cn-male-calm',name:'男声 · 沉稳普通话',lang:'zh-CN',rate:0.9,pitch:0.86},
  {id:'preset:zh-cn-male-deep',name:'男声 · 低沉慢速',lang:'zh-CN',rate:0.82,pitch:0.72},
  {id:'preset:zh-cn-child',name:'童声 · 高音快速',lang:'zh-CN',rate:1.15,pitch:1.45},
  {id:'preset:zh-tw',name:'台湾腔 · 中文',lang:'zh-TW',rate:0.95,pitch:1.03},
  {id:'preset:zh-hk',name:'粤语感 · 中文',lang:'zh-HK',rate:0.95,pitch:0.98}
];
function cleanBase(v){return (v||'').trim().replace(/\/$/,'')}
function currentOrigin(){return window.location.origin}
function base(){return cleanBase($('#arenaBase')?.value)||currentOrigin()}
function token(){return ($('#botToken')?.value||'').trim()}
function adminToken(){return ($('#adminToken')?.value||'').trim()}
function loadSettings(){try{return JSON.parse(localStorage.getItem(STORAGE_KEY)||'{}')}catch{return {}}}
function saveSettings(data){localStorage.setItem(STORAGE_KEY,JSON.stringify({...loadSettings(),...data}))}
function loadAudioSettings(){try{return JSON.parse(localStorage.getItem(AUDIO_KEY)||'{}')}catch{return {}}}
function saveAudioSettings(data){localStorage.setItem(AUDIO_KEY,JSON.stringify({...loadAudioSettings(),...data}))}
function esc(v){return String(v||'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
function clampNum(v,min,max,fallback){const n=Number(v); if(!Number.isFinite(n))return fallback; return Math.max(min,Math.min(max,n))}
function chineseVoices(){
  if(!window.speechSynthesis)return [];
  const all=window.speechSynthesis.getVoices()||[];
  const zh=all.filter(v=>(v.lang||'').toLowerCase().includes('zh'));
  return zh.length?zh:all;
}
function fillVoiceSelect(){
  const el=$('#botSpeechVoice'); if(!el)return;
  const current=loadAudioSettings().botSpeechVoiceURI||'';
  const voices=chineseVoices();
  const presetOptions=BUILTIN_VOICES.map(v=>`<option value="${esc(v.id)}">${esc(v.name)} · ${esc(v.lang)}</option>`).join('');
  const systemOptions=voices.map(v=>`<option value="${esc(v.voiceURI)}">系统：${esc(v.name)} · ${esc(v.lang)}</option>`).join('');
  el.innerHTML='<option value="">自动选择中文语音</option><optgroup label="内置可选音色">'+presetOptions+'</optgroup>'+(systemOptions?'<optgroup label="浏览器系统音色">'+systemOptions+'</optgroup>':'');
  el.value=current;
  if(el.value!==current)el.value='';
}
function selectedVoice(){return $('#botSpeechVoice')?.value||''}
function selectedRate(){return clampNum($('#botSpeechRate')?.value,0.5,1.5,0.95)}
function selectedPitch(){return clampNum($('#botSpeechPitch')?.value,0.5,2,1)}
function updateVoiceLabels(){
  const rate=selectedRate(), pitch=selectedPitch();
  if($('#botSpeechRateLabel'))$('#botSpeechRateLabel').textContent=rate.toFixed(2)+'×';
  if($('#botSpeechPitchLabel'))$('#botSpeechPitchLabel').textContent=pitch.toFixed(2);
}
function fill(){
  const s=loadSettings(), a=loadAudioSettings();
  $('#arenaBase').value=s.arenaBase||currentOrigin(); $('#botToken').value=s.token||''; $('#adminToken').value=s.adminToken||''; $('#botName').value=s.name||''; $('#avatarUrl').value=s.avatar_url||''; $('#description').value=s.description||''; $('#chessStyle').value=s.chess_style||'random'; $('#personaPrompt').value=s.persona_prompt||'';
  if($('#botSpeechRate'))$('#botSpeechRate').value=clampNum(a.botSpeechRate,0.5,1.5,0.95);
  if($('#botSpeechPitch'))$('#botSpeechPitch').value=clampNum(a.botSpeechPitch,0.5,2,1);
  fillVoiceSelect(); updateVoiceLabels();
}
function collectAudio(){return {botSpeechVoiceURI:selectedVoice(),botSpeechRate:selectedRate(),botSpeechPitch:selectedPitch()}}
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
function speakPreview(){
  if(!window.speechSynthesis){show('#verifyResult','当前浏览器不支持语音合成');return;}
  const text='这局我先记下了，下一手你可要小心。';
  const u=new SpeechSynthesisUtterance(text);
  const a=collectAudio();
  const preset=BUILTIN_VOICES.find(v=>v.id===a.botSpeechVoiceURI);
  u.lang=preset?.lang||'zh-CN'; u.rate=(preset?.rate||1)*a.botSpeechRate; u.pitch=(preset?.pitch||1)*a.botSpeechPitch; u.volume=0.9;
  const voices=chineseVoices();
  const voice=voices.find(v=>v.voiceURI===a.botSpeechVoiceURI)||voices.find(v=>(v.lang||'').toLowerCase()===(preset?.lang||'zh-cn').toLowerCase())||voices[0];
  if(voice)u.voice=voice;
  window.speechSynthesis.cancel(); window.speechSynthesis.speak(u);
}
window.addEventListener('DOMContentLoaded',()=>{
  fill();
  if(window.speechSynthesis)window.speechSynthesis.onvoiceschanged=fillVoiceSelect;
  $('#botSpeechRate')?.addEventListener('input',updateVoiceLabels); $('#botSpeechPitch')?.addEventListener('input',updateVoiceLabels);
  $('#testVoiceBtn')?.addEventListener('click',()=>{saveAudioSettings(collectAudio()); speakPreview();});
  $('#toggleToken').onclick=()=>{$('#botToken').type=$('#botToken').type==='password'?'text':'password'; $('#adminToken').type=$('#adminToken').type==='password'?'text':'password'};
  $('#saveBtn').onclick=()=>{saveSettings({arenaBase:base(),token:token(),adminToken:adminToken(),...collect()}); saveAudioSettings(collectAudio()); show('#verifyResult','已保存到当前浏览器')};
  $('#clearBtn').onclick=()=>{localStorage.removeItem(STORAGE_KEY); localStorage.removeItem(AUDIO_KEY); fill(); show('#verifyResult','已清空')};
  $('#verifyBtn').onclick=()=>verify().catch(e=>show('#verifyResult','验证失败：'+e.message));
  $('#syncBtn').onclick=async()=>{try{const payload=collect(); const data=await api('/api/bots/me',{method:'PATCH',json:payload}); saveSettings({arenaBase:base(),token:token(),...payload,...data}); saveAudioSettings(collectAudio()); show('#syncResult',data)}catch(e){show('#syncResult','同步失败：'+e.message)}}
});
