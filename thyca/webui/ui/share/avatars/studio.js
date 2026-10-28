// Expression studio: the 'try states' dialog (live character, playback, idle gallery).
'use strict';
const lab={timers:[],mode:null,botId:null,lastChoice:'idle',sample:'',ownsGroup:false};

lab.idleTab=false;lab.lastIdle='look';

function buildIdleGallery(b){
 $('#idle-expression-grid').innerHTML=Object.entries(idleCatalog).map(([key,v],i)=>`<button class="expression-tile" data-idle-choice="${key}" style="${paletteStyle(b)}" aria-label="Thử cử chỉ ${v.label}" aria-pressed="${b.idle.active===key}"><span class="expression-index">${String(i+1).padStart(2,'0')}</span>${avatar(b,'idle','expression-preview').replace('data-selected=',`data-idle="${key}" data-selected=`)}<strong>${v.label}</strong><small>${v.hint}</small></button>`).join('');
 $('#idle-auto-toggle').checked=idleOptions.enabled;$('#idle-comments-toggle').checked=idleOptions.comments;
}
function studioTab(idle){
 lab.idleTab=idle;$('#idle-panel').hidden=!idle;$('#lifecycle-panel').hidden=idle;$('#lab-play').hidden=idle;
 $('#idle-tab').setAttribute('aria-selected',String(idle));$('#work-tab').setAttribute('aria-selected',String(!idle));
 $('#idle-tab').tabIndex=idle?0:-1;$('#work-tab').tabIndex=idle?-1:0;
 $('#lab-playback-note').textContent=idle?'Mắt tự nhìn quanh, cử chỉ và lời nhắn đổi ngẫu nhiên.':'Chọn biểu cảm để xem chuyển động.';
}
function chooseIdle(key=null){
 if(run){toast('Dừng lượt trả lời đang chạy trước khi thử cử chỉ rảnh.');return false;}
 stopLab(false);lab.botId=$('#expression-bot').value;lab.mode='idle-preview';lab.sample='';studioTab(true);
 const b=getBot(lab.botId);setState(b.id,'idle',undefined,0);const chosen=beginIdle(b.id,key,true);lab.lastIdle=chosen;
 $('#lab-stop').hidden=false;openBot(b.id);announce(b.name+': '+idleCatalog[chosen].label);return chosen;
}

/* Shape picker: three live avatar previews, one per DROP_NAMES silhouette.
   The override is browser-only (localStorage, like the tint); picking one
   persists it and patches every avatar instance in place. */
const SHAPE_LABELS={tall:'Giọt cao',squat:'Giọt tròn',cloud:'Giọt mây'};
function buildShapePicker(b){
 $('#shape-picker').innerHTML=DROP_NAMES.map(s=>`<button type="button" data-shape="${s}" style="${paletteStyle(b)}" aria-pressed="${b.shape===s||(!b.shape&&dropShapeFor(b).name===s)}" aria-label="Hình dạng ${SHAPE_LABELS[s]}" title="${SHAPE_LABELS[s]}"><span class="shape-preview">${avatar({...b,shape:s},b.state,'expression-preview')}</span><strong>${SHAPE_LABELS[s]}</strong></button>`).join('');
}
function buildExpressions(){
 const b=getBot($('#expression-bot').value);lab.botId=b.id;
 buildShapePicker(b);
 $('#palette-preview').innerHTML=`<span class="swatch" style="background:${b.color}"></span><span class="swatch" style="background:${b.palette.eye}"></span><span class="swatch" style="background:${b.palette.paper}"></span><span>Sáp · mắt · giấy</span>`;
 paint($('#expression-stage'),b);paint($('#group-preview'),b);
 $('#lab-portrait').innerHTML=avatar(b,b.state,'lab-avatar');
 const tile=(s,i)=>`<button class="expression-tile" data-expression="${s}" style="${paletteStyle(b)}" aria-pressed="${expressionFor(b)===s}" aria-label="Thử biểu cảm ${states[s][0]}"><span class="expression-index">${String(i+1).padStart(2,'0')}</span>${avatar(b,s,'expression-preview')}<strong>${states[s][0]}</strong><small>${states[s][2]}</small></button>`;
 $('#expression-grid').innerHTML=primaryStates.map(tile).join('');$('#expression-extras').innerHTML=extraStates.map((s,i)=>tile(s,i+8)).join('');
 $('#group-preview-members').innerHTML=bots.map(x=>`<button class="group-preview-member" data-speaker="${x.id}" style="${paletteStyle(x)}" aria-label="Cho ${x.name} nói" aria-pressed="false">${avatar(x)}<span>${x.name}</span><small>Đang lắng nghe</small></button>`).join('');
 buildIdleGallery(b);studioTab(lab.idleTab);refreshLab(b);syncGroupAvatars();
}
function refreshLab(b){
 if(!$('#lab-state')||lab.botId!==b.id)return;
 const state=expressionFor(b);
 const moment=state==='idle'&&b.idle.active?idleCatalog[b.idle.active]:null;
 $('#lab-state').textContent=moment?moment.label:states[state][1];$('#lab-description').textContent=moment?moment.detail:stateDetails[state];$('#lab-bot-name').textContent=b.name;
 $$('#idle-expression-grid [data-idle-choice]').forEach(t=>t.setAttribute('aria-pressed',String(t.dataset.idleChoice===b.idle.active)));
 $$('#expressions-dialog .expression-tile').forEach(t=>t.setAttribute('aria-pressed',String(t.dataset.expression===state)));
 $('#lab-speech').textContent=lab.sample||(state==='idle'&&b.idle.comment)||stateSummaries[state].replace(/\n/g,' ');
 $('#group-preview').hidden=lab.mode!=='group';
}
function stopLab(reset=true){
 const wasRunning=lab.mode;lab.timers.forEach(id=>{clearTimeout(id);clearInterval(id);});lab.timers=[];
 if(lab.ownsGroup){setGroupActivity(null);lab.ownsGroup=false;}
 const b=getBot(lab.botId);lab.mode=null;lab.sample='';
 if(b){if(wasRunning==='idle-preview')clearIdle(b,true);clearMusic(b.id);if(reset&&wasRunning&&!run&&['waiting','thinking','working','writing','group'].includes(b.state))setState(b.id,'idle',undefined,0);refreshLab(b);}
 $('#lab-stop').hidden=true;$('#lab-playback-note').textContent='Chọn biểu cảm để xem chuyển động.';
}
function labDelay(ms,fn){lab.timers.push(setTimeout(fn,ms));}
function labWriting(){
 const b=getBot(lab.botId);if(!b)return;
 setState(b.id,'writing',undefined,0);lab.sample='';let position=0;
 const sample='Mình đang ghép từng ý thành câu, để lời nhắn đến với bạn thật rõ ràng.';
 const timer=setInterval(()=>{
  if(b.state!=='writing'){clearInterval(timer);return;}
  const delta=sample.slice(position,position+2);position+=2;lab.sample=sample.slice(0,position);$('#lab-speech').textContent=lab.sample;notifyText(b.id,delta);
  if(position>=sample.length){clearInterval(timer);labDelay(350,()=>{if(b.state==='writing'){lab.mode=null;setState(b.id,'done',undefined,0);$('#lab-stop').hidden=true;$('#lab-playback-note').textContent='Đã xong một lượt · tự trở về nghỉ.';}});}
 },65);lab.timers.push(timer);
}
function chooseExpression(state){
 const b=getBot($('#expression-bot').value);if(run){toast('Dừng lượt minh họa đang chạy trước khi thử biểu cảm.');return;}
 stopLab(false);studioTab(false);lab.botId=b.id;lab.lastChoice=state;lab.sample='';
 if(state==='mention'){
  if(['offline','error'].includes(b.state))setState(b.id,'idle');mentionBot(b.id);
 }else if(state==='writing'){lab.mode='writing';$('#lab-stop').hidden=false;labWriting();}
 else if(state==='group'){
  lab.mode='group';lab.ownsGroup=true;setState(b.id,'group',undefined,0);setGroupActivity('group-1',b.id);$('#lab-stop').hidden=false;
  $('#lab-playback-note').textContent='Chọn một bot trong nhóm để đổi người nói.';
 }else setState(b.id,state,undefined,state==='thinking'?8:0);
 openBot(b.id);refreshLab(b);
 if(['done','error','mention'].includes(state)&&!motionPaused&&!reduced.matches){$$('.seal-avatar[data-bot-avatar="'+b.id+'"]:not(.expression-preview) ').forEach(el=>{(el.getAnimations?.({subtree:true})||[]).forEach(a=>{if(a.animationName?.startsWith('seal-done')||['seal-shake','seal-noticed'].includes(a.animationName)){a.currentTime=0;a.play();}});});}
 announce(b.name+': '+states[state][0]);
}
function playLabSequence(){
 if(run){toast('Dừng lượt minh họa đang chạy trước khi thử biểu cảm.');return;}
 stopLab(false);studioTab(false);lab.botId=$('#expression-bot').value;lab.mode='sequence';lab.sample='';
 setState(lab.botId,'idle',undefined,0);$('#lab-stop').hidden=false;$('#lab-playback-note').textContent='Nghỉ → chờ → nghĩ → làm → viết → xong';
 labDelay(650,()=>setState(lab.botId,'waiting',undefined,0));
 labDelay(2300,()=>setState(lab.botId,'thinking',undefined,1));
 labDelay(4800,()=>setState(lab.botId,'working',undefined,3));
 labDelay(6800,labWriting);
}

/* "Custom bot" footer button — opens the seal studio: pick a bot, change
   its color (persisted via localStorage in setBotColor), or replay the
   demo expressions to preview each working state. */
$('#open-customize').onclick=()=>{stopLab(false);$('#expression-bot').innerHTML=bots.map(b=>`<option value="${b.id}"${b.id===selectedBot?' selected':''}>${b.name}</option>`).join('');lab.lastChoice=expressionFor(getBot(selectedBot));$('#bot-color').value=getBot(selectedBot).color;buildExpressions();$('#expressions-dialog').showModal();};
$('#expression-bot').onchange=()=>{stopLab();lab.lastChoice=expressionFor(getBot($('#expression-bot').value));$('#bot-color').value=getBot($('#expression-bot').value).color;buildExpressions();};
$('#bot-color').oninput=e=>(window.ThycaShell||window.ThycaPrototype)?.setBotColor($('#expression-bot').value,e.target.value);
$('#shape-picker').addEventListener('click',e=>{const btn=e.target.closest('[data-shape]');if(!btn)return;const id=$('#expression-bot').value;if((window.ThycaShell||window.ThycaPrototype)?.setBotShape(id,btn.dataset.shape))buildExpressions();});

['#expression-grid','#expression-extras'].forEach(selector=>$(selector).addEventListener('click',e=>{const button=e.target.closest('button[data-expression]');if(button)chooseExpression(button.dataset.expression);}));
$('#lab-play').onclick=playLabSequence;$('#lab-replay').onclick=()=>lab.idleTab?chooseIdle(lab.lastIdle):chooseExpression(lab.lastChoice);$('#lab-stop').onclick=()=>stopLab();

$('#lab-done').onclick=()=>chooseExpression('done');
$('#work-tab').onclick=()=>{stopLab();studioTab(false);};
$('#idle-tab').onclick=()=>{stopLab();studioTab(true);if(!run)chooseIdle('look');};
$$('.studio-tab').forEach(tab=>tab.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const target=e.key==='Home'?$('#work-tab'):e.key==='End'?$('#idle-tab'):tab.id==='work-tab'?$('#idle-tab'):$('#work-tab');target.focus();target.click();}}));
$('#idle-auto-toggle').onchange=e=>setIdleOptions({enabled:e.target.checked});$('#idle-comments-toggle').onchange=e=>setIdleOptions({comments:e.target.checked});
$('#idle-random').onclick=()=>chooseIdle();$('#idle-expression-grid').onclick=e=>{const btn=e.target.closest('[data-idle-choice]');if(btn)chooseIdle(btn.dataset.idleChoice);};

$('#group-preview-members').onclick=e=>{const btn=e.target.closest('[data-speaker]');if(btn&&lab.mode==='group'){setGroupActivity('group-1',btn.dataset.speaker);announce(getBot(btn.dataset.speaker).name+' đang nói.');}};
$('#expressions-dialog').addEventListener('close',()=>stopLab());
