'use strict';
let activeGroup=null;
const returningBotIds=new Set();
function awayGroupFor(b){
 if(!b||typeof groups==='undefined'||!Array.isArray(groups))return null;
 if(activeGroup&&Array.isArray(activeGroup.ids)&&activeGroup.ids.includes(b.id)){
  return groups.find(g=>g&&g.id===activeGroup.id)||null;
 }
 const live=groups.find(g=>g&&g.working&&Array.isArray(g.bots)&&g.bots.includes(b.id));
 if(live)return live;
 if(b.roomId&&['working','thinking','writing','group'].includes(b.state)){
  return groups.find(g=>g&&g.id===b.roomId)||null;
 }
 return null;
}
function setPeerExchange(exchange){
 const prev=typeof window!=='undefined'?window.ThycaPeerExchange:null;
 if(typeof window!=='undefined')window.ThycaPeerExchange=exchange?{...exchange}:null;
 const touch=new Set();
 if(prev?.a)touch.add(prev.a);
 if(prev?.b)touch.add(prev.b);
 if(exchange?.a)touch.add(exchange.a);
 if(exchange?.b)touch.add(exchange.b);
 touch.forEach(id=>{const b=getBot(id);if(b&&typeof updateCard==='function')updateCard(b);});
 if(typeof wakeEyeLoop==='function')wakeEyeLoop();
}

function activeSidebarBot(){const router=window.ThycaRouter;return router?.currentPage?.()?router.highlightedBot():selectedBot;}
function sidebarActionLabel(b){const router=window.ThycaRouter;return router?.currentPage?.()?router.sidebarLabel(b.id,b.name,states[expressionFor(b)][1]):`Trò chuyện với ${b.name}`;}
function previewLine(b){
 const groups=livePreviewGroups(b,currentChatWindow());
 if(!groups.total)return '';
 return groups.shown.map(g=>`${g.kind==='skill'?'skill ':''}${g.name}${g.active>1?` \u00d7${g.active}`:''}`).join(', ');
}
function previewExtraCount(b){
 return livePreviewGroups(b,currentChatWindow()).extra;
}
function taskStatusText(b){
 const window=currentChatWindow();
 if(!window||b.taskWindow!==window){
  if(b.open)return 'Ch\u01b0a r\u00f5 c\u1eeda s\u1ed5. M\u1edf h\u1ed9p \u0111\u1ec3 xem vi\u1ec7c trong c\u1eeda s\u1ed5 chat hi\u1ec7n t\u1ea1i.';
  return '';
 }
 const cache=b.taskCache;
 if(!cache||cache.status==='loading')return '\u0110ang t\u1ea3i vi\u1ec7c\u2026';
 if(cache.status==='unavailable')return 'Kh\u00f4ng xem \u0111\u01b0\u1ee3c vi\u1ec7c l\u00fac n\u00e0y.';
 if(cache.status==='error')return 'Kh\u00f4ng t\u1ea3i \u0111\u01b0\u1ee3c danh s\u00e1ch vi\u1ec7c.';
 const tasks=(cache.payload&&cache.payload.tasks)||[];
 if(!tasks.length)return '';
 return `${tasks.length} vi\u1ec7c \u0111ang ch\u1ea1y`;
}
function taskRows(b){
 const window=currentChatWindow();
 if(!window||b.taskWindow!==window)return [];
 const cache=b.taskCache;
 if(!cache||cache.status!=='ok')return [];
 const tasks=(cache.payload&&cache.payload.tasks)||[];
 const seen=new Map();
 return tasks.filter(t=>t&&t.title).map(t=>{
  const base=`${String(t.title)}\0${String(t.status||'open')}\0${(typeof t.waiting_on==='string'&&t.waiting_on)||''}`;
  const nth=seen.get(base)||0;seen.set(base,nth+1);
  return {
   key:`${base}\0#${nth}`,
   title:String(t.title),
   status:String(t.status||'open'),
   waitingOn:(typeof t.waiting_on==='string'&&t.waiting_on)||null,
  };
 });
}
function card(b){
 const activeId=activeSidebarBot();
 const groups=livePreviewGroups(b,typeof currentChatWindow==='function'?currentChatWindow():null);
 const more=groups.extra>0?`<button class="live-more" data-action="live-more" data-bot-id="${b.id}" aria-label="Xem to\u00e0n b\u1ed9 ${groups.total} vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}" title="Xem to\u00e0n b\u1ed9">+${groups.extra}</button>`:'';
 const ex = typeof window !== 'undefined' ? window.ThycaPeerExchange : null;
 const ag = awayGroupFor(b);
 const isAway = Boolean(ag);
 const exPartner = ex && (b.id === ex.a || b.id === ex.b) ? getBot(b.id === ex.a ? ex.b : ex.a) : null;
 const effState = isAway ? 'group' : b.state;
 const roleText = isAway
  ? `Đang ở nhóm · ${ag.title}`
  : (exPartner ? `${ex.sender === b.id ? 'Đang gửi \u2192' : 'Đang nhận \u2190'} ${exPartner.name}` : (b.role || ''));
 const awayPill = isAway
  ? `<button type="button" class="away-pill" data-action="jump-group" data-group-id="${escapeHTML(ag.id)}" aria-label="${escapeHTML('Mở nhóm ' + ag.title)}">${escapeHTML(ag.title)} \u2197</button>`
  : '';
 const peerPill = (!isAway && exPartner)
  ? `<span class="peer-pill" data-dir="${ex.sender === b.id ? 'out' : 'in'}">${ex.sender === b.id ? '\u2192' : '\u2190'} ${escapeHTML(exPartner.name)}</span>`
  : '';
 const retCls = returningBotIds.has(b.id) ? 'just-returned' : '';
 return `<article class="bot-card team-cluster team-envelope" id="bot-${b.id}" data-bot-id="${b.id}" data-state="${effState}" data-open="${b.open}" data-selected="${b.id===activeId}" style="${paletteStyle(b)}"><header class="bot-header"><div class="card-stage-shell"><div class="card-stage-hit" data-stage-select="${b.id}" aria-hidden="true"><div class="card-stage" data-bot-theme="${themeFor(b)}" data-away="${isAway}">${avatar(b,effState,retCls,{variant:'stage',away:isAway,idle:b.idle})}</div></div>${awayPill}${peerPill}<div class="card-actions"><button class="icon-button favorite" data-action="favorite" aria-label="${b.system?`Bot h\u1ec7 th\u1ed1ng ${b.name} \u2014 lu\u00f4n \u0111\u01b0\u1ee3c ghim`:b.favorite?`B\u1ecf ghim ${b.name}`:`Ghim ${b.name} l\u00ean \u0111\u1ea7u`}" aria-pressed="${b.favorite}"${b.system?' disabled title="Bot h\u1ec7 th\u1ed1ng \u2014 kh\u00f4ng b\u1ecf ghim \u0111\u01b0\u1ee3c"':''}>${icon('star')}</button><button class="icon-button info-button" data-action="info" aria-label="Th\u00f4ng tin bot">${icon('info')}</button></div></div><button class="bot-select team-bot-item" data-action="select" data-bot-id="${b.id}" aria-label="${sidebarActionLabel(b)}"><span class="bot-heading"><span class="bot-name conversation-name">${escapeHTML(b.name)}</span><span class="bot-role">${escapeHTML(roleText)}</span><span class="header-status"><span class="status-dot"></span><span class="header-status-label">${states[effState][1]}</span></span></span></button></header><div class="compact-preview"${groups.total?'':' hidden'}><span class="status-dot"></span><span class="preview-text">${escapeHTML(previewLine(b))}</span>${more}</div><div class="pocket team-pocket-viewport" id="pocket-${b.id}" ${b.open?'':'inert'}><div class="pocket-inner"><section class="thinking-paper" aria-label="Vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}"><div class="thinking-heading">${dots}<span class="thinking-title">${states[effState][1]}</span><span class="elapsed">${formatElapsed(b.elapsed)}</span></div><div class="thinking-scroll" tabindex="0" aria-label="Vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}"><p class="thinking-summary"></p><ol class="progress-list"></ol></div><button class="reply-action" data-action="reply"${['waiting','confused'].includes(effState)?'':' hidden'}>${icon('arrow')}Tr\u1ea3 l\u1eddi</button></section></div></div><footer class="paper-footer card-status"><span class="status-dot"></span><span class="info-label">${states[effState][1]}</span></footer></article>`;
}
function syncTaskList(b,list){
 const rows=taskRows(b);
 list.hidden=!rows.length&&!taskStatusText(b);
 const keyed=new Map($$('li',list).map(li=>[li.dataset.tkey,li]));
 const want=new Set(rows.map(r=>r.key));
 keyed.forEach((li,key)=>{if(!want.has(key))li.remove();});
 let cursor=list.firstElementChild;
 rows.forEach(r=>{
  const key=r.key;let li=keyed.get(key);
  if(!li){li=document.createElement('li');li.dataset.tkey=key;li._enter=true;
   li.addEventListener('animationend',()=>{li.classList.remove('li-enter');li._enter=false;},{once:true});}
  const waiter=r.waitingOn?` \u00b7 ch\u1edd ${(getBot(r.waitingOn)||{name:r.waitingOn}).name}`:'';
  const badge=`<span class="feed-count">${escapeHTML(r.status+waiter)}</span>`;
  const html=`<span class="step-mark">${icon('list')}</span><span>${escapeHTML(r.title)}</span>${badge}`;
  if(li._taskHtml!==html){li.innerHTML=html;li._taskHtml=html;}
  li.className='current'+(li._enter?' li-enter':'');
  if(li!==cursor)list.insertBefore(li,cursor);else cursor=cursor.nextElementSibling;
 });
}
let liveReturnFocus=null;
function ensureLiveDialog(){
 let dlg=$('#live-list-dialog');
 if(dlg)return dlg;
 dlg=document.createElement('dialog');
 dlg.id='live-list-dialog';
 dlg.className='screen-dialog';
 dlg.setAttribute('aria-labelledby','live-list-title');
 dlg.innerHTML=`<div class="dialog-heading"><div><h2 id="live-list-title">Vi\u1ec7c \u0111ang ch\u1ea1y</h2><p id="live-list-sub"></p></div><button class="icon-button dialog-close" aria-label="\u0110\u00f3ng">${icon('close')}</button></div><div class="dialog-body"><ol class="progress-list" id="live-list-rows"></ol></div>`;
 document.body.appendChild(dlg);
 $('.dialog-close',dlg).onclick=()=>dlg.close();
 dlg.addEventListener('close',()=>{if(liveReturnFocus&&document.contains(liveReturnFocus))liveReturnFocus.focus();liveReturnFocus=null;});
 dlg.addEventListener('click',e=>{if(e.target===dlg){const r=dlg.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dlg.close();}});
 return dlg;
}
function openLiveList(botId){
 const b=getBot(botId);if(!b)return false;
 const window=currentChatWindow();
 const groups=livePreviewGroups(b,window);
 const dlg=ensureLiveDialog();
 liveReturnFocus=document.activeElement;
 $('#live-list-sub',dlg).textContent=b?`${b.name} \u00b7 ${groups.total} nh\u00f3m vi\u1ec7c`:`${groups.total} nh\u00f3m`;
 const rows=$('#live-list-rows',dlg);
 rows.replaceChildren();
 if(!window){rows.innerHTML=`<li class="current"><span>${escapeHTML('Ch\u01b0a r\u00f5 c\u1eeda s\u1ed5. M\u1edf h\u1ed9p \u0111\u1ec3 xem vi\u1ec7c trong c\u1eeda s\u1ed5 chat hi\u1ec7n t\u1ea1i.')}</span></li>`;}
 else if(!groups.total){rows.innerHTML=`<li class="current"><span>${escapeHTML('Kh\u00f4ng c\u00f3 vi\u1ec7c n\u00e0o \u0111ang ch\u1ea1y.')}</span></li>`;}
 else{
  const full=new Map();
  for(const f of liveCallsForWindow(b,window)){
   const key=`${f.kind}\0${f.name}`;
   const slot=full.get(key);
   if(slot){slot.active+=1;}else full.set(key,{kind:f.kind,name:f.name,active:1});
  }
  rows.innerHTML=[...full.values()].map(g=>`<li class="current"><span class="step-mark">${icon(g.kind==='skill'?'list':'leaf')}</span><span>${escapeHTML(g.kind==='skill'?`skill ${g.name}`:g.name)}</span>${g.active>1?`<span class="feed-count">\u00d7${g.active}</span>`:''}</li>`).join('');
 }
 if(typeof dlg.showModal==='function')dlg.showModal();else dlg.open=true;
 const closer=$('.dialog-close',dlg);if(closer)closer.focus();
 return true;
}
function refreshOpenPocket(b){
 if(!b||!b.open)return null;
 return refreshBotTasks(b);
}
function refreshOpenPocketsOnWindowChange(){
 const window=currentChatWindow();
 bots.forEach(b=>{
  if(!b.open)return;
  if(b.taskWindow!==window)void refreshBotTasks(b,window);
 });
}
function formatElapsed(sec){
 const s=Math.max(0,Math.round(Number(sec)||0));
 if(!s)return '';
 if(s<60)return s+' gi\u00e2y';
 return Math.floor(s/60)+':'+String(s%60).padStart(2,'0');
}
const botPinRank=b=>(b&&(b.system||b.id==='thyca'))?2:(b&&b.favorite?1:0);
function teamDisplayOrder(){ return [...bots].sort((a,b)=>(botPinRank(b)-botPinRank(a))); }
function entryLiveRank(e){
 if(e.type==='group'){
  if(activeGroup&&activeGroup.id===e.group.id)return 3;
  const win=groupKey(e.group.id);
  const busy=e.group.bots.some(id=>{const b=getBot(id);return b&&livePreviewGroups(b,win).total>0;});
  return busy?2:0;
 }
 if(ACTIVE_HEADER_STATES.has(e.bot.state))return 2;
 if(e.bot.state==='error')return 1;
 return 0;
}
function entryActivityAt(e){ return e.type==='group'?(e.group.updatedAtMs||0):(e.bot.activityAt||0); }
function entryPinRank(e){
 if(e.type==='bot')return botPinRank(e.bot);
 return e.group.favorite?1:0;
}
function sidebarEntries(){
 const entries=[...bots.map(bot=>({type:'bot',bot})),...groups.map(group=>({type:'group',group}))];
 return entries.sort((x,y)=>{
  const pin=entryPinRank(y)-entryPinRank(x); if(pin)return pin;
  const live=entryLiveRank(y)-entryLiveRank(x); if(live)return live;
  const at=entryActivityAt(y)-entryActivityAt(x); if(at)return at;
  return 0;
 });
}
function computeFullRowIds(order){
 const full=new Set();let prev=null;
 order.forEach(b=>{
  if(b.open){if(prev){full.add(prev.id);prev=null;}}
  else if(prev){prev=null;}
  else{prev=b;}
 });
 if(prev)full.add(prev.id);
 return full;
}
function syncCardGridSpans(){
 const list=$('#team-bot-list');if(!list||!list.children.length)return;
 const order=[...list.children].map(el=>el.dataset&&el.dataset.botId?getBot(el.dataset.botId):null).filter(Boolean);
 const full=computeFullRowIds(order);
 [...list.children].forEach(el=>{
  if(!el.dataset||!el.dataset.botId)return;
  if(full.has(el.dataset.botId))el.dataset.fullRow='true';else delete el.dataset.fullRow;
 });
}
const ACTIVE_HEADER_STATES=new Set(['thinking','working','writing','waiting','confused']);
function railAvatarBtn(b){
 const activeId=activeSidebarBot();
 const expr=expressionFor(b);
 const label=(states[expr]||states[b.state]||states.idle)[1];
 const style=b.palette?paletteStyle(b):'';
 return `<button type="button" class="rail-avatar" data-action="header-select" data-bot-id="${escapeHTML(b.id)}" data-state="${escapeHTML(b.state)}" data-selected="${b.id===activeId}" style="${style}" title="${escapeHTML(b.name)} \u2014 ${escapeHTML(label)}" aria-label="${escapeHTML(b.name)}"><span class="rail-avatar-face">${avatar(b,expr)}</span><span class="rail-status-dot"></span></button>`;
}
function railGroupBtn(g){
 const cluster=groupClusterAvatarMarkup(g);
 return `<button type="button" class="rail-avatar rail-group chat-only" data-action="header-select" data-group-id="${escapeHTML(g.id)}" data-selected="${activeGroup?.id===g.id}" title="${escapeHTML(g.title)}" aria-label="${escapeHTML(g.title)}">${cluster}</button>`;
}
function renderHeaderBots(){
 const list=$('#rail-avatar-list');if(!list)return;
 const activeId=activeSidebarBot();
 const entries=sidebarEntries();
 const key=entries.map(e=>e.type==='bot'
  ? `b:${e.bot.id}:${e.bot.state}:${expressionFor(e.bot)}:${e.bot.id===activeId}:${e.bot.name}:${e.bot.color||''}:${e.bot.shape||''}:${e.bot.accessory||''}:${e.bot.theme||''}:${e.bot.favorite?'fav':''}`
  : `g:${e.group.id}:${groupDisplayBots(e.group).map(b=>b.id).join(',')}:${e.group.bots?.length||0}:${activeGroup?.id===e.group.id}:${e.group.favorite?'fav':''}`).join('|');
 if(list.dataset.renderKey===key&&list.innerHTML)return;
 list.dataset.renderKey=key;
 list.innerHTML=entries.map(e=>e.type==='bot'?railAvatarBtn(e.bot):railGroupBtn(e.group)).join('');
}
function renderTeam(){ const el = $('#team-bot-list'); if (el) { el.innerHTML = sidebarEntries().map(e=>e.type==='bot'?card(e.bot):groupRowHtml(e.group)).join(''); syncCardGridSpans(); } renderHeaderBots(); if(typeof registerLiveRigs==='function')registerLiveRigs(); if(typeof wakeEyeLoop==='function')wakeEyeLoop(); }
function orderSidebarNodes(){
 const list=$('#team-bot-list');if(!list)return false;
 const have=new Map();
 [...list.children].forEach(el=>{
  const key=el.dataset&&el.dataset.botId?`bot:${el.dataset.botId}`:el.dataset&&el.dataset.group?`group:${el.dataset.group}`:null;
  if(key)have.set(key,el);
 });
 let moved=false,cursor=list.firstElementChild;
 sidebarEntries().forEach(e=>{
  const el=have.get(e.type==='bot'?`bot:${e.bot.id}`:`group:${e.group.id}`);
  if(!el||el.parentNode!==list)return;
  if(el!==cursor){list.insertBefore(el,cursor);moved=true;}
  else cursor=cursor.nextElementSibling;
 });
 syncCardGridSpans();
 if(moved&&typeof markCtmDirty==='function')markCtmDirty();
 return true;
}
function pinCardOrder(){
 const ok=orderSidebarNodes();
 renderHeaderBots();
 return ok;
}
function updateCard(b){
 const el=$('#bot-'+b.id);if(!el)return;
 const ag=awayGroupFor(b);
 const isAway=Boolean(ag);
 const ex=typeof window!=='undefined'?window.ThycaPeerExchange:null;
 const exPartner=ex&&(b.id===ex.a||b.id===ex.b)?getBot(b.id===ex.a?ex.b:ex.a):null;
 const effState=isAway?'group':b.state;
 const isActive=ACTIVE_HEADER_STATES.has(effState);
 el.dataset.state=effState;el.dataset.open=b.open;el.dataset.selected=b.id===activeSidebarBot();
 syncCardGridSpans();
 const roleEl=$('.bot-role',el);
 if(roleEl){
  roleEl.textContent=isAway?`Đang ở nhóm · ${ag.title}`:(exPartner?`${ex.sender===b.id?'Đang gửi \u2192':'Đang nhận \u2190'} ${exPartner.name}`:(b.role||''));
 }
 const shellStage=$('.card-stage-shell',el);
 if(shellStage){
  const stageEl=$('.card-stage',shellStage);
  if(stageEl){stageEl.dataset.botTheme=themeFor(b);stageEl.dataset.away=String(isAway);}
  let awayBtn=$('.away-pill',shellStage);
  if(isAway){
   if(!awayBtn){
    awayBtn=document.createElement('button');
    awayBtn.type='button';awayBtn.className='away-pill';awayBtn.dataset.action='jump-group';
    shellStage.insertBefore(awayBtn,$('.card-actions',shellStage));
   }
   awayBtn.dataset.groupId=ag.id;
   awayBtn.setAttribute('aria-label',`Mở nhóm ${ag.title}`);
   awayBtn.textContent=`${ag.title} \u2197`;
  }else if(awayBtn){awayBtn.remove();}
  let peerEl=$('.peer-pill',shellStage);
  if(!isAway&&exPartner){
   if(!peerEl){
    peerEl=document.createElement('span');
    peerEl.className='peer-pill';
    shellStage.insertBefore(peerEl,$('.card-actions',shellStage));
   }
   peerEl.dataset.dir=ex.sender===b.id?'out':'in';
   peerEl.textContent=`${ex.sender===b.id?'\u2192':'\u2190'} ${exPartner.name}`;
  }else if(peerEl){peerEl.remove();}
 }
 $('.header-status-label',el).textContent=states[effState][1];
 $('.bot-select',el).setAttribute('aria-label',sidebarActionLabel(b));
 const status=$('.thinking-summary',el);
 const statusText=taskStatusText(b);
 status.textContent=statusText;status.hidden=!statusText;status.style.whiteSpace='nowrap';status.classList.remove('idle-comment');
 const line=previewLine(b);
 $('.compact-preview',el).hidden=true;
 $('.preview-text',el).textContent=line;
 let more=$('.live-more',el);
 const extra=previewExtraCount(b);
 if(extra>0){
  if(!more){
   more=document.createElement('button');
   more.className='live-more';more.dataset.action='live-more';more.dataset.botId=b.id;
   $('.card-status',el).appendChild(more);
  }
  more.hidden=false;
  more.textContent='+'+extra;
  more.setAttribute('aria-label',`Xem to\u00e0n b\u1ed9 ${previewExtraCount(b)+livePreviewGroups(b,currentChatWindow()).shown.length} vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}`);
  more.title='Xem to\u00e0n b\u1ed9';
 }else if(more){more.hidden=true;more.remove();}
 $('.reply-action',el).hidden=!['waiting','confused'].includes(effState);
 $('.compact-preview',el).dataset.idleComment='false';$('.preview-text',el).title=line;
 $('.elapsed',el).textContent=formatElapsed(b.elapsed);
 $('.thinking-title',el).textContent=states[effState][1];
 const infoLabel=$('.info-label',el);
 if(infoLabel){
  const bar=infoLabel.closest('footer');
  if(isActive){
   const doing=line||states[effState][1];
   const bits=[doing];
   if(statusText&&/^\d+ /.test(statusText))bits.push(statusText);
   const elap=formatElapsed(b.elapsed);
   if(elap)bits.push(elap);
   infoLabel.textContent=bits.join(' \u00b7 ');
   if(bar)bar.dataset.liveStatus='true';
  }else{
   infoLabel.textContent=states[effState][1];
   if(bar)delete bar.dataset.liveStatus;
  }
 }
 $('.pocket',el).inert=!b.open;
 syncTaskList(b,$('.progress-list',el));
 $$('.crew-avatar[data-bot-avatar="'+b.id+'"]:not(.expression-preview)').forEach(a=>{
  const isCardStage=a.closest?.('.bot-card');
  syncAvatar(a,{...b,state:effState},{away:Boolean(isCardStage&&isAway)});
  if(isCardStage&&returningBotIds.has(b.id))a.classList.add('just-returned');
 });
 syncGroupAvatars();if(typeof refreshLab==='function')refreshLab(b);renderHeaderBots();orderSidebarNodes();
}

function groupBotRank(g,b){
 if(!b)return -1;
 if(activeGroup&&activeGroup.id===g?.id&&activeGroup.speaker===b.id)return 2;
 if(!['idle','offline'].includes(b.state))return 1;
 return 0;
}
function groupDisplayBots(g){
 const sorted=(g?.bots||[]).map(id=>getBot(id)).filter(Boolean).sort((a,b)=>groupBotRank(g,b)-groupBotRank(g,a));
 return sorted.length>3?sorted.slice(0,2):sorted.slice(0,3);
}
function groupStageMembers(g){
 return (g?.bots||[]).map(id=>getBot(id)).filter(Boolean).sort((a,b)=>groupBotRank(g,b)-groupBotRank(g,a));
}
function groupClusterAvatarMarkup(g){
 const all=(g?.bots||[]).map(id=>getBot(id)).filter(Boolean);
 const shown=groupDisplayBots(g);
 const total=g?.bots?.length||0;
 const extra=total>shown.length?total-shown.length:0;
 const label=shown.map(b=>b.name).join(', ')+(extra?` +${extra}`:'')||g.title;
 const minis=shown.map(b=>`<span class="group-mini-member" data-group-member="${b.id}" style="${paletteStyle(b)}" title="${escapeHTML(b.name)}">${avatar(b)}</span>`).join('');
 const plus=extra>0?`<span class="group-mini-more" aria-label="+${extra} bot">+${extra}</span>`:'';
 return `<span class="group-cluster" data-order="${shown.map(b=>b.id).join(',')}" data-count="${total}" data-shown="${shown.length}" aria-label="${escapeHTML(label)}" title="${escapeHTML(g.title)}">${minis}${plus}</span>`;
}
function groupStatusLabel(g){
 const active=Boolean((activeGroup&&activeGroup.id===g?.id)||g?.working);
 if(active){
  const spk=activeGroup&&activeGroup.id===g?.id?activeGroup.speaker:g?.activeSpeaker;
  return spk&&getBot(spk)?`${getBot(spk).name} đang nói`:'Đang cùng làm việc';
 }
 const members=(g?.bots||[]).map(id=>getBot(id)).filter(Boolean);
 const err=members.find(b=>b.state==='error');
 if(err) return states.error[1];
 const act=members.find(b=>['thinking','working','writing'].includes(b.state));
 if(act) return `${act.name} · ${states[act.state]?.[1] || ''}`;
 const waiting=members.find(b=>['waiting','confused'].includes(b.state));
 if(waiting) return `${waiting.name} · ${states[waiting.state]?.[1] || ''}`;
 if(members.length&&members.every(b=>b.state==='offline')) return states.offline[1];
 return 'Sẵn sàng';
}
function groupStageKey(g,members,active,speakerId){
 const dark=typeof isDarkMode==='function'?isDarkMode():false;
 return `${g.id}|${dark?'d':'l'}|${active?1:0}|${speakerId||''}|${members.map(m=>`${m.id}:${typeof crewShapeFor==='function'?crewShapeFor(m).id:(m.shape||'')}:${typeof accessoryFor==='function'?accessoryFor(m):(m.accessory||'')}:${m.color||''}`).join(',')}`;
}
function groupRowHtml(g){
 const first=groupDisplayBots(g)[0];
 const selected=typeof currentChatWindow==='function'&&currentChatWindow()===groupKey(g.id);
 const active=Boolean((activeGroup&&activeGroup.id===g.id)||g.working);
 const speakerId=activeGroup&&activeGroup.id===g.id?activeGroup.speaker:(g.activeSpeaker||null);
 const members=groupStageMembers(g);
 const stageKey=groupStageKey(g,members,active,speakerId);
 return `<article class="bot-card group-card chat-only" data-group="${g.id}" data-selected="${selected}" data-coordinating="${active}" style="${first?paletteStyle(first):''}"><header class="bot-header"><div class="card-stage-shell"><div class="card-stage-hit" data-group-open="${escapeHTML(g.id)}" aria-hidden="true"><div class="card-stage" data-bot-theme="group-stage" data-stage-key="${escapeHTML(stageKey)}">${renderGroupStageAvatar(members,{groupId:g.id,speakerId,coordinating:active})}</div></div><div class="card-actions"><button class="icon-button favorite" data-action="group-favorite" aria-label="${escapeHTML(g.favorite?`Bỏ ghim nhóm ${g.title}`:`Ghim nhóm ${g.title}`)}" aria-pressed="${!!g.favorite}">${icon('star')}</button><button class="icon-button info-button" data-action="group-info" aria-label="Thông tin nhóm">${icon('info')}</button></div></div><button class="bot-select group-select" type="button" data-group-open="${escapeHTML(g.id)}" aria-label="${escapeHTML('Mở ' + g.title)}"><span class="bot-heading"><span class="bot-name conversation-name">${escapeHTML(g.title)}</span><span class="bot-role">${escapeHTML(g.subtitle)}</span></span></button></header><footer class="group-card-footer"><span class="status-dot"></span><span class="group-meta">${escapeHTML(groupStatusLabel(g))}</span></footer></article>`;
}
function openGroupInfo(id){
 const g=groups.find(x=>x.id===id);if(!g)return;
 const members=g.bots.map(x=>getBot(x)).filter(Boolean);
 $('#bot-drawer-name').textContent='Thông tin nhóm';
 $('#bot-drawer-subtitle').textContent=g.subtitle||'';
 const body=$('#bot-drawer-body');
 const first=members[0];
 if(first)paint(body,first);
 body.innerHTML=`<div class="info-lockup">${groupClusterAvatarMarkup(g)}<div><h3>${escapeHTML(g.title)}</h3><p>${escapeHTML(groupStatusLabel(g))}</p></div></div><h3 class="info-section-title">THÀNH VIÊN</h3>${members.map(b=>`<div class="group-info-member">${avatar(b)}<span class="group-info-main"><strong>${escapeHTML(b.name)}</strong><small>${escapeHTML(b.role||'')}</small></span><span class="group-info-state">${escapeHTML((states[b.state]||states.idle)[1])}</span></div>`).join('')||'<p>Chưa có thành viên.</p>'}`;
 $('#bot-drawer').showModal();
}
function renderGroups(){
 renderTeam();
 bots.forEach(b=>{ if($('#bot-'+b.id)) updateCard(b); });
 syncGroupAvatars();
}
function syncGroupAvatars(){
 let rebuilt=false;
 $$('.group-card').forEach(row=>{
  const g=groups.find(x=>x.id===row.dataset.group);
  if(!g)return;
  const active=activeGroup?.id===g.id||Boolean(g.working);
  const speakerId=activeGroup?.id===g.id?activeGroup.speaker:(g.activeSpeaker||null);
  row.dataset.coordinating=String(active);
  const stageEl=$('.card-stage',row);
  if(stageEl){
   const members=groupStageMembers(g);
   const nextKey=groupStageKey(g,members,active,speakerId);
   if(stageEl.dataset.stageKey!==nextKey||!stageEl.querySelector?.('.crew-svg')){
    stageEl.innerHTML=renderGroupStageAvatar(members,{groupId:g.id,speakerId,coordinating:active});
    stageEl.dataset.stageKey=nextKey;
    rebuilt=true;
   }
  }
  const cluster=$$('.group-cluster',row)[0];
  if(cluster){
   const shown=groupDisplayBots(g),wantKey=shown.map(b=>b.id).join(',');
   const total=g?.bots?.length||0;
   if(cluster.dataset.order!==wantKey||cluster.dataset.count!==String(total)){
    cluster.outerHTML=groupClusterAvatarMarkup(g);
    rebuilt=true;
   }
   $$('.group-mini-member',row).forEach(member=>member.dataset.speaking=String(active&&speakerId===member.dataset.groupMember));
  }
  const metaEl=$('.group-meta',row);
  if(metaEl){
   metaEl.textContent=active?(speakerId&&getBot(speakerId)?getBot(speakerId).name+' đang nói':'Đang cùng làm việc'):groupStatusLabel(g);
  }
 });
 $$('.crew-avatar:not(.expression-preview):not(.is-group-stage)').forEach(a=>{
  const b=getBot(a.dataset.botAvatar);if(!b)return;
  const active=activeGroup&&activeGroup.ids.includes(b.id)&&!['offline','error','waiting'].includes(b.state);
  if(active){
   if(a.dataset.groupClock!==String(activeGroup.start)){a.style.setProperty('--group-phase',-(Date.now()-activeGroup.start)/1000+'s');a.dataset.groupClock=String(activeGroup.start);}
   a.dataset.groupActive='true';a.dataset.speaking=String(b.id===activeGroup.speaker);
  }else{delete a.dataset.groupActive;delete a.dataset.speaking;delete a.dataset.groupClock;}
 });
 $$('.group-preview-member').forEach(btn=>{const b=getBot(btn.dataset.speaker);if(!b)return;const speaking=activeGroup?.speaker===b.id;btn.setAttribute('aria-pressed',String(speaking));btn.disabled=['error','offline'].includes(b.state);$('small',btn).textContent=speaking?'Đang nói':b.state==='offline'?'Offline':'Đang lắng nghe';});
 if(rebuilt&&typeof registerLiveRigs==='function')registerLiveRigs();
 if(typeof wakeEyeLoop==='function')wakeEyeLoop();
}
function setGroupActivity(groupId,speakerId=null){
 if(groupId===null){
  const prevIds=activeGroup?.ids?[...activeGroup.ids]:[];
  activeGroup=null;
  if(typeof window!=='undefined')window.ThycaGroupSpeaker=null;
  groups.forEach(g=>{g.working=false;g.activeSpeaker=null;});
  prevIds.forEach(id=>returningBotIds.add(id));
  prevIds.forEach(id=>{const b=getBot(id);if(b)updateCard(b);});
  syncGroupAvatars();orderSidebarNodes();
  if(prevIds.length)setTimeout(()=>{returningBotIds.clear();$$('.crew-avatar.just-returned').forEach(a=>a.classList.remove('just-returned'));},550);
  return true;
 }
 const g=groups.find(x=>x.id===groupId);
 const speaker=getBot(speakerId);
 if(!g||(speakerId!==null&&(!g.bots.includes(speakerId)||!speaker||['offline','error'].includes(speaker.state))))return false;
 groups.forEach(item=>{if(item.id!==g.id){item.working=false;item.activeSpeaker=null;}});
 g.working=true;g.activeSpeaker=speakerId;
 if(typeof window!=='undefined')window.ThycaGroupSpeaker=speakerId;
 activeGroup={id:g.id,ids:g.bots,speaker:speakerId,start:activeGroup?.id===g.id?activeGroup.start:Date.now()};
 g.bots.forEach(id=>{const b=getBot(id);if(b){clearIdle(b);updateCard(b);}});
 syncGroupAvatars();orderSidebarNodes();return true;
}
function openBot(id,open=true){
 const target=getBot(id);if(!target)return;
 if(open&&!target.open){
  bots.forEach(b=>{b.open=b.id===id;updateCard(b);});
  void refreshBotTasks(target);
 }else{
  bots.forEach(b=>{const will=b.id===id?open:false;if(b.open&&!will)noteTaskStale(b);b.open=will;updateCard(b);});
 }
 if(open)setTimeout(()=>{const el=$('#bot-'+id);if(el)el.scrollIntoView({block:'nearest',behavior:motionPaused?'auto':'smooth'});},280);
}

function openInfo(id){const b=getBot(id);$('#bot-drawer-name').textContent='Thông tin bot';$('#bot-drawer-subtitle').textContent=b.role;const body=$('#bot-drawer-body');paint(body,b);body.innerHTML=`<div class="info-lockup">${avatar(b)}<div><h3>${b.name}</h3><p>${states[b.state][1]}</p></div></div>${capabilitiesHtml(b)}<h3 class="info-section-title">NHỮNG CUỘC TRÒ CHUYỆN</h3>${b.conversations.map(s=>`<button class="info-session" data-conversation="${s[0]}" data-bot="${id}"><span>${s[1]}</span><small>${s[2]}</small></button>`).join('')}`;$('#bot-drawer').showModal();fillDrawerContext(b);}

function capabilitiesHtml(b){
 const skills=(b.capabilities||[]).map(c=>`<section class="capability"><h3>${escapeHTML(c[0])}</h3></section>`);
 const bashLabel={ask:'Hỏi trước khi chạy lệnh',always:'Luôn được phép',deny:'Không dùng bash',never:'Không dùng bash'}[b.bash]||b.bash||'';
 const model=b.model?`${b.model.id||'default'}${b.model.thinking?' · suy nghĩ '+b.model.thinking:''}`:'';
 const mem=b.memory?['đọc','ghi'].filter((_,i)=>i?b.memory.write:b.memory.read).join(' + '):'';
 const facts=[['Vai trò',b.role],['Model',model],['Bash',bashLabel],['Bộ nhớ',mem],['Lịch chạy',b.schedule],['Loại',b.system?'Bot hệ thống':'Bot tùy chỉnh']].filter(f=>f[1]);
 const tools=b.tools.length?`<div class="tool-chips">${b.tools.map(t=>`<span class="tool-chip"${t.description?` title="${escapeHTML(t.description)}"`:''}>${escapeHTML(t.name)}</span>`).join('')}</div>`:'<p class="info-copy">Không có công cụ nào.</p>';
 return `<div class="capabilities">${skills.concat(facts.map(f=>`<section class="capability"><h3>${f[0]}</h3><p>${escapeHTML(String(f[1]))}</p></section>`)).join('')}</div>`
  +`<h3 class="info-section-title">CÔNG CỤ ĐANG DÙNG</h3>${tools}`
  +`<h3 class="info-section-title">NGỮ CẢNH</h3><div class="ctx-meter" data-drawer-context><div class="ctx-bar" style="width:0"></div></div>`;
}

async function fillDrawerContext(b){
 const slot=$('#bot-drawer-body [data-drawer-context]');if(!slot)return;
 try{
  await getJson(`/api/bots/${encodeURIComponent(b.id)}/dm`);
  const ops=await getJson(`/api/bots/${encodeURIComponent(b.id)}/dm/operations`);
  if(!document.contains(slot))return;
  const c=ops.context||{},ratio=c.ratio||0,pct=Math.min(100,Math.round(ratio*100));
  const band=ratio>=0.95?'danger':ratio>=0.8?'warn':'ok';
  slot.title=`~${(c.estimated_tokens||0).toLocaleString('vi-VN')} / ${(c.context_tokens||0).toLocaleString('vi-VN')} token · ${pct}%`;
  slot.querySelector('.ctx-bar').className=`ctx-bar ctx-${band}`;
  slot.querySelector('.ctx-bar').style.width=pct+'%';
 }catch{}
}

$('#rail-avatar-list')?.addEventListener('click',e=>{if(typeof setupLocked==='function'&&setupLocked())return;const btn=e.target.closest('[data-action="header-select"]');if(!btn)return;if(btn.dataset.groupId){const g=groups.find(x=>x.id===btn.dataset.groupId);if(!g||!g.bots?.length)return;window.ThycaRouter?ThycaRouter.dispatch('loadConversation',groupKey(g.id),g.bots[0]):loadConversation(groupKey(g.id),g.bots[0]);return;}const b=getBot(btn.dataset.botId);if(!b)return;window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);});
$('#team-bot-list').addEventListener('click',e=>{const btn=e.target.closest('button');if(!btn){const stBot=e.target.closest('[data-stage-select]');if(stBot){window.ThycaRouter?ThycaRouter.dispatch('selectBot',stBot.dataset.stageSelect):selectBot(stBot.dataset.stageSelect);return;}const stGrp=e.target.closest('[data-group-open]');if(stGrp){const g=groups.find(x=>x.id===stGrp.dataset.groupOpen);if(g&&g.bots?.length)window.ThycaRouter?ThycaRouter.dispatch('loadConversation',groupKey(g.id),g.bots[0]):loadConversation(groupKey(g.id),g.bots[0]);}return;}if(btn.dataset.action==='jump-group'){const g=groups.find(x=>x.id===btn.dataset.groupId);if(g&&g.bots?.length)window.ThycaRouter?ThycaRouter.dispatch('loadConversation',groupKey(g.id),g.bots[0]):loadConversation(groupKey(g.id),g.bots[0]);return;}const grow=e.target.closest('[data-group]');if(grow){const g=groups.find(x=>x.id===grow.dataset.group);if(!g)return;if(btn.dataset.action==='group-favorite'){g.favorite=!g.favorite;setGroupFavorite(g.id,g.favorite);btn.setAttribute('aria-pressed',g.favorite);btn.setAttribute('aria-label',g.favorite?`Bỏ ghim nhóm ${g.title}`:`Ghim nhóm ${g.title}`);toast(g.favorite?`Đã ghim nhóm ${g.title}.`:`Đã bỏ ghim nhóm ${g.title}.`);pinCardOrder();return;}if(btn.dataset.action==='group-info'){openGroupInfo(g.id);return;}if(g.bots?.length)window.ThycaRouter?ThycaRouter.dispatch('loadConversation',groupKey(g.id),g.bots[0]):loadConversation(groupKey(g.id),g.bots[0]);return;}const cardEl=btn.closest('.bot-card');if(!cardEl)return;const b=getBot(cardEl.dataset.botId);if(!b)return;switch(btn.dataset.action){case 'select':window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);break;case 'reply':window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);document.querySelector('#message')?.focus?.();break;case 'live-more':openLiveList(b.id);break;case 'favorite':if(b.system)break;b.favorite=!b.favorite;setBotFavorite(b.id,b.favorite);btn.setAttribute('aria-pressed',b.favorite);btn.setAttribute('aria-label',b.favorite?`Bỏ ghim ${b.name}`:`Ghim ${b.name} lên đầu`);toast(b.favorite?`Đã ghim ${b.name} lên đầu.`:`Đã bỏ ghim ${b.name}.`);pinCardOrder();break;case 'info':case 'capabilities':openInfo(b.id);break;}});

$('#bot-drawer-body').addEventListener('click',e=>{const btn=e.target.closest('[data-conversation]');if(!btn)return;const b=getBot(btn.dataset.bot);const s=b.conversations.find(x=>x[0]===btn.dataset.conversation);window.ThycaRouter?ThycaRouter.dispatch('loadConversation',s[0],b.id):loadConversation(s[0],b.id);$('#bot-drawer').close();});
const teamSharedCreate=$('#team-shared-create');
if(teamSharedCreate)teamSharedCreate.onclick=()=>{$('#group-options').innerHTML=bots.map(b=>`<label class="check-row"><input type="checkbox" name="bots" value="${b.id}" checked><span class="check-avatar" style="${paletteStyle(b)}">${avatar(b)}</span><span style="color:${b.palette.ink}">${b.name}</span><small style="color:${b.palette.muted}">${b.role}</small></label>`).join('');$('#group-dialog').showModal();};
$('#group-form').onsubmit=async e=>{e.preventDefault();const ids=$$('input[name=bots]:checked',e.currentTarget).map(x=>x.value);if(ids.length<2){toast('Chọn ít nhất hai bot cho hội thoại chung.');return;}
 try{const groupName=($('#group-name')?.value||'').trim();const payload={bots:ids};if(groupName)payload.title=groupName;const detail=await getJson('/api/groups',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  $('#group-dialog').close();$('#group-form').reset();await refreshConversations();renderGroups();loadConversation(groupKey(detail.group_id),ids[0]);toast('Đã tạo hội thoại chung.');}
 catch(err){toast('Không tạo được hội thoại: '+err.message);}};
