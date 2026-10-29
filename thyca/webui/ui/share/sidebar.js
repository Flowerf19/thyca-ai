// Bot sidebar: team cards, pockets, group rows, group dialogs.
'use strict';
let activeGroup=null;

function activeSidebarBot(){const router=window.ThycaRouter;return router?.currentPage?.()?router.highlightedBot():selectedBot;}
function sidebarActionLabel(b){const router=window.ThycaRouter;return router?.currentPage?.()?router.sidebarLabel(b.id,b.name,states[expressionFor(b)][1]):`Trò chuyện với ${b.name}`;}
/* Compact activity line: live tool/skill calls for the exact window only.
   Simulated idle comments never render here — the line stays empty
   unless real activity is running. Avatar animations and the expression
   preview are untouched (avatars.js / idle.js own those layers). */
function previewLine(b){
 const groups=livePreviewGroups(b,currentChatWindow());
 if(!groups.total)return '';
 // Skill rows keep their kind prefix from the actual event semantics, so a
 // skill 'read' never masquerades as a tool 'read' in the compact line.
 return groups.shown.map(g=>`${g.kind==='skill'?'skill ':''}${g.name}${g.active>1?` \u00d7${g.active}`:''}`).join(', ');
}
function previewExtraCount(b){
 return livePreviewGroups(b,currentChatWindow()).extra;
}
/* Pocket status line for the task pocket. Only factual backend states:
   loading / empty / unavailable / error / live count. Never a generic
   'thinking' summary — the heading already names the factual state. */
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
 if(!tasks.length)return 'Kh\u00f4ng c\u00f3 vi\u1ec7c n\u00e0o \u0111ang ch\u1ea1y.';
 return `${tasks.length} vi\u1ec7c \u0111ang ch\u1ea1y`;
}
/* Task rows for the exact window: one row per open runtime unit the
   backend derived for this bot+window (task_open/delegate opens, pending
   envelopes, minus completion records). Completed work and the
   saved-memory catalog never appear — the cache is replaced per fetch.
   Live tool/skill calls are NOT rows here; they feed only the collapsed
   preview and the separate +N full list. */
function taskRows(b){
 const window=currentChatWindow();
 if(!window||b.taskWindow!==window)return [];
 const cache=b.taskCache;
 if(!cache||cache.status!=='ok')return [];
 const tasks=(cache.payload&&cache.payload.tasks)||[];
 // Every unit renders (no silent truncation); identical units keep
 // distinct occurrence-aware identities so reconcile preserves each.
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
 return `<article class="bot-card team-cluster team-envelope" id="bot-${b.id}" data-bot-id="${b.id}" data-state="${b.state}" data-open="${b.open}" data-selected="${b.id===activeId}" style="${paletteStyle(b)}"><header class="bot-header"><button class="bot-select team-bot-item" data-action="select" data-bot-id="${b.id}" aria-label="${sidebarActionLabel(b)}">${avatar(b)}<span class="bot-heading"><span class="bot-name session-name">${b.name}</span><span class="bot-role">${b.role}</span><span class="header-status"><span class="status-dot"></span><span class="header-status-label">${states[b.state][1]}</span></span></span></button><div class="card-actions"><button class="icon-button favorite" data-action="favorite" aria-label="${b.system?`Bot h\u1ec7 th\u1ed1ng ${b.name} \u2014 lu\u00f4n \u0111\u01b0\u1ee3c ghim`:b.favorite?`B\u1ecf ghim ${b.name}`:`Ghim ${b.name} l\u00ean \u0111\u1ea7u`}" aria-pressed="${b.favorite}"${b.system?' disabled title="Bot h\u1ec7 th\u1ed1ng \u2014 kh\u00f4ng b\u1ecf ghim \u0111\u01b0\u1ee3c"':''}>${icon('star')}</button><button class="icon-button collapse-button" data-action="collapse" aria-label="Thu ho\u1eb7c m\u1edf ${b.name}" aria-controls="pocket-${b.id}" aria-expanded="${b.open}">${icon('chevron')}</button></div></header><div class="compact-preview"><span class="status-dot"></span><span class="preview-text">${escapeHTML(previewLine(b))}</span>${more}</div><div class="pocket team-pocket-viewport" id="pocket-${b.id}" ${b.open?'':'inert'}><div class="pocket-inner"><section class="thinking-paper" aria-label="Vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}"><div class="thinking-heading">${dots}<span class="thinking-title">${states[b.state][1]}</span><span class="elapsed">${b.elapsed} gi\u00e2y</span></div><div class="thinking-scroll" tabindex="0" aria-label="Vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}"><p class="thinking-summary"></p><ol class="progress-list"></ol></div><button class="reply-action" data-action="reply"${['waiting','confused'].includes(b.state)?'':' hidden'}>${icon('arrow')}Tr\u1ea3 l\u1eddi</button><footer class="paper-footer"><button data-action="info">${icon('info')}Th\u00f4ng tin bot</button><button data-action="capabilities">${icon('list')}Kh\u1ea3 n\u0103ng</button></footer></section></div></div></article>`;
}
/* Keyed reconcile for the task pocket: one row per live kind+name group.
   Existing <li> nodes update in place; rows that disappear are removed.
   No exiting ghosts, no invented thinking rows — only fetched tasks. */
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
/* Full live list for the +N button — SEPARATE from the task pocket.
   A lazily created dialog lists every live group for the exact window
   with \u00d7N counts. Touch and keyboard accessible: the +N button
   opens it, Escape or the close button dismisses, focus returns to +N. */
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
/* Event-driven pocket refresh while open: stream events and window
   switches call this — never a timer. No fetch happens here when the
   pocket is closed, so roster/select/send stay disclosure-free. */
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
/* Sidebar display order: pinned (starred) bots first — system Thyca is
   always starred — with stable roster order inside each group. The
   underlying `bots` array is never reordered. */
function teamDisplayOrder(){ return [...bots].sort((a,b)=>(b.favorite-a.favorite)); }
function renderTeam(){ const el = $('#team-bot-list'); if (el) el.innerHTML = teamDisplayOrder().map(card).join(''); }
/* Reorder existing card nodes after a pin toggle: appendChild moves the
   node, so pocket/task/animation identity survives (no full rerender). */
function pinCardOrder(){
 const list=$('#team-bot-list');if(!list)return false;
 const have=new Map();
 [...list.children].forEach(el=>{const id=el.dataset&&el.dataset.botId;if(id&&!have.has(id))have.set(id,el);});
 teamDisplayOrder().forEach(b=>{const el=have.get(b.id);if(el)list.appendChild(el);});
 return true;
}
function updateCard(b){
 const el=$('#bot-'+b.id);if(!el)return;
 el.dataset.state=b.state;el.dataset.open=b.open;el.dataset.selected=b.id===activeSidebarBot();
 $('.header-status-label',el).textContent=states[expressionFor(b)][1];
 $('.bot-select',el).setAttribute('aria-label',sidebarActionLabel(b));
 const status=$('.thinking-summary',el);
 const statusText=taskStatusText(b);
 status.textContent=statusText;status.hidden=!statusText;status.style.whiteSpace='nowrap';status.classList.remove('idle-comment');
 const line=previewLine(b);
 $('.preview-text',el).textContent=line;
 let more=$('.live-more',el);
 const extra=previewExtraCount(b);
 if(extra>0){
  if(!more){
   more=document.createElement('button');
   more.className='live-more';more.dataset.action='live-more';more.dataset.botId=b.id;
   $('.compact-preview',el).appendChild(more);
  }
  more.hidden=false;
  more.textContent='+'+extra;
  more.setAttribute('aria-label',`Xem to\u00e0n b\u1ed9 ${previewExtraCount(b)+livePreviewGroups(b,currentChatWindow()).shown.length} vi\u1ec7c \u0111ang ch\u1ea1y c\u1ee7a ${b.name}`);
  more.title='Xem to\u00e0n b\u1ed9';
 }else if(more){more.hidden=true;more.remove();}
 $('.reply-action',el).hidden=!['waiting','confused'].includes(b.state);
 $('.compact-preview',el).dataset.idleComment='false';$('.preview-text',el).title=line;
 $('.elapsed',el).textContent=b.elapsed?b.elapsed+' gi\u00e2y':'';
 $('.thinking-title',el).textContent=states[b.state][1];
 $('.collapse-button',el).setAttribute('aria-expanded',b.open);
 $('.pocket',el).inert=!b.open;
 syncTaskList(b,$('.progress-list',el));
 $$('.seal-avatar[data-bot-avatar="'+b.id+'"]:not(.expression-preview)').forEach(a=>syncAvatar(a,b));
 syncGroupAvatars();refreshLab(b);
}


function renderGroups(){
 const gl=$('#group-list');if(!gl)return;   // non-chat pages have no group section
 gl.innerHTML=groups.map(g=>`<button class="group-row" data-group="${g.id}" style="margin-bottom:9px"><span class="group-row-heading"><span class="group-title">${escapeHTML(g.title)}</span>${icon('arrow')}</span><span class="group-roster" style="--members:${g.bots.length}">${g.bots.map(id=>{const b=getBot(id);return b?`<span class="group-member" data-group-member="${id}" style="${paletteStyle(b)}">${avatar(b)}<span class="group-member-name">${b.name}</span></span>`:'';}).join('')}</span><span class="group-meta">${g.subtitle}</span></button>`).join('');
 syncGroupAvatars();
}
function syncGroupAvatars(){
 $$('.seal-avatar:not(.expression-preview)').forEach(a=>{
  const b=getBot(a.dataset.botAvatar);if(!b)return;
  const active=activeGroup&&activeGroup.ids.includes(b.id)&&!['offline','error','waiting'].includes(b.state);
  if(active){
   if(a.dataset.groupClock!==String(activeGroup.start)){a.style.setProperty('--group-phase',-(Date.now()-activeGroup.start)/1000+'s');a.dataset.groupClock=String(activeGroup.start);}
   a.dataset.groupActive='true';a.dataset.speaking=String(b.id===activeGroup.speaker);a.style.setProperty('--team-gaze',b.id===activeGroup.ids[0]?'1.2px':'-1.8px');
  }else{delete a.dataset.groupActive;delete a.dataset.speaking;delete a.dataset.groupClock;}
 });
 $$('.group-row').forEach(row=>{const g=groups.find(x=>x.id===row.dataset.group);const active=activeGroup?.id===g?.id;row.dataset.coordinating=String(active);$$('.group-member',row).forEach(member=>member.dataset.speaking=String(active&&activeGroup.speaker===member.dataset.groupMember));$('.group-meta',row).textContent=active?(activeGroup.speaker&&getBot(activeGroup.speaker)?getBot(activeGroup.speaker).name+' đang nói':'Đang cùng làm việc'):g.subtitle;});
 $$('.group-preview-member').forEach(btn=>{const b=getBot(btn.dataset.speaker);if(!b)return;const speaking=activeGroup?.speaker===b.id;btn.setAttribute('aria-pressed',String(speaking));btn.disabled=['error','offline'].includes(b.state);$('small',btn).textContent=speaking?'Đang nói':b.state==='offline'?'Offline':'Đang lắng nghe';});
}
function setGroupActivity(groupId,speakerId=null){
 if(groupId===null){activeGroup=null;syncGroupAvatars();return true;}
 const g=groups.find(x=>x.id===groupId);
 const speaker=getBot(speakerId);
 if(!g||(speakerId!==null&&(!g.bots.includes(speakerId)||!speaker||['offline','error'].includes(speaker.state))))return false;
 activeGroup={id:g.id,ids:g.bots,speaker:speakerId,start:activeGroup?.id===g.id?activeGroup.start:Date.now()};
 g.bots.forEach(id=>{const b=getBot(id);if(b&&clearIdle(b))updateCard(b);});
 syncGroupAvatars();return true;
}
/* Chevron-only disclosure: opening a pocket fetches that bot's tasks
   for the exact current window; closing invalidates any in-flight
   fetch so it can never paint stale data. Roster load, bot select,
   send and activity popovers never call this with open=true. */
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


function openInfo(id,capabilities=false){const b=getBot(id);$('#bot-drawer-name').textContent=capabilities?'Các khả năng của '+b.name:'Một chút về '+b.name;$('#bot-drawer-subtitle').textContent=b.role;const body=$('#bot-drawer-body');paint(body,b);body.innerHTML=capabilities?capabilitiesHtml(b):`<div class="info-lockup">${avatar(b)}<div><h3>${b.name}</h3><p>${states[b.state][1]}</p></div></div><h3 class="info-section-title">NHỮNG CUỘC TRÒ CHUYỆN</h3>${b.sessions.map(s=>`<button class="info-session" data-session="${s[0]}" data-bot="${id}"><span>${s[1]}</span><small>${s[2]}</small></button>`).join('')}`;$('#bot-drawer').showModal();if(capabilities)fillDrawerContext(b);}

/* Bot drawer "Khả năng": operational facts + effective tool list. */
function capabilitiesHtml(b){
 const skills=(b.capabilities||[]).map(c=>`<section class="capability"><h3>${escapeHTML(c[0])}</h3>${c[1]?`<p>${escapeHTML(c[1])}</p>`:''}</section>`);
 const bashLabel={ask:'Hỏi trước khi chạy lệnh',always:'Luôn được phép',deny:'Không dùng bash',never:'Không dùng bash'}[b.bash]||b.bash||'';
 const model=b.model?`${b.model.id||'default'}${b.model.thinking?' · suy nghĩ '+b.model.thinking:''}`:'';
 const mem=b.memory?['đọc','ghi'].filter((_,i)=>i?b.memory.write:b.memory.read).join(' + '):'';
 const facts=[['Vai trò',b.role],['Model',model],['Bash',bashLabel],['Bộ nhớ',mem],['Lịch chạy',b.schedule],['Loại',b.system?'Bot hệ thống':'Bot tùy chỉnh']].filter(f=>f[1]);
 const tools=b.tools.length?`<div class="tool-chips">${b.tools.map(t=>`<span class="tool-chip"${t.description?` title="${escapeHTML(t.description)}"`:''}>${escapeHTML(t.name)}</span>`).join('')}</div>`:'<p class="info-copy">Không có công cụ nào.</p>';
 return `<div class="capabilities">${skills.concat(facts.map(f=>`<section class="capability"><h3>${f[0]}</h3><p>${escapeHTML(String(f[1]))}</p></section>`)).join('')}</div>`
  +`<h3 class="info-section-title">CÔNG CỤ ĐANG DÙNG</h3>${tools}`
  +`<h3 class="info-section-title">NGỮ CẢNH</h3><div class="ctx-meter" data-drawer-context><div class="ctx-bar" style="width:0"></div></div>`;
}

/* Context meter only — no text; band color mirrors context_budget.py. */
async function fillDrawerContext(b){
 const slot=$('#bot-drawer-body [data-drawer-context]');if(!slot)return;
 try{
  const dm=await getJson(`/api/bots/${encodeURIComponent(b.id)}/dm`);                                  // API: GET /api/bots/<id>/dm
  const ops=await getJson(`/api/bots/${encodeURIComponent(b.id)}/rooms/${encodeURIComponent(dm.id)}/operations`); // API: bot operations
  if(!document.contains(slot))return;
  const c=ops.context||{},ratio=c.ratio||0,pct=Math.min(100,Math.round(ratio*100));
  const band=ratio>=0.95?'danger':ratio>=0.8?'warn':'ok';
  slot.title=`~${(c.estimated_tokens||0).toLocaleString('vi-VN')} / ${(c.context_tokens||0).toLocaleString('vi-VN')} token · ${pct}%`;
  slot.querySelector('.ctx-bar').className=`ctx-bar ctx-${band}`;
  slot.querySelector('.ctx-bar').style.width=pct+'%';
 }catch{}
}

$('#team-bot-list').addEventListener('click',e=>{const btn=e.target.closest('button');if(!btn)return;const b=getBot(btn.closest('.bot-card').dataset.botId);switch(btn.dataset.action){case 'select':window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);break;case 'reply':window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);document.querySelector('#message')?.focus?.();break;case 'collapse':openBot(b.id,!b.open);break;case 'live-more':openLiveList(b.id);break;case 'favorite':if(b.system)break;b.favorite=!b.favorite;setBotFavorite(b.id,b.favorite);btn.setAttribute('aria-pressed',b.favorite);btn.setAttribute('aria-label',b.favorite?`Bỏ ghim ${b.name}`:`Ghim ${b.name} lên đầu`);toast(b.favorite?`Đã ghim ${b.name} lên đầu.`:`Đã bỏ ghim ${b.name}.`);pinCardOrder();break;case 'info':openInfo(b.id);break;case 'capabilities':openInfo(b.id,true);break;}});
$('#group-list')?.addEventListener('click',e=>{const row=e.target.closest('[data-group]');if(!row)return;const g=groups.find(x=>x.id===row.dataset.group);window.ThycaRouter?ThycaRouter.dispatch('loadSession',g.id,g.title,g.bots[0]):loadSession(g.id,g.title,g.bots[0]);});

$('#bot-drawer-body').addEventListener('click',e=>{const btn=e.target.closest('[data-session]');if(!btn)return;const b=getBot(btn.dataset.bot);const s=b.sessions.find(x=>x[0]===btn.dataset.session);window.ThycaRouter?ThycaRouter.dispatch('loadSession',s[0],s[1],b.id):loadSession(s[0],s[1],b.id);$('#bot-drawer').close();});
const teamSharedCreate=$('#team-shared-create');
if(teamSharedCreate)teamSharedCreate.onclick=()=>{$('#group-options').innerHTML=bots.map(b=>`<label class="check-row"><input type="checkbox" name="bots" value="${b.id}" checked><span class="check-avatar" style="${paletteStyle(b)}">${avatar(b)}</span><span style="color:${b.palette.ink}">${b.name}</span><small style="color:#92846d">${b.role}</small></label>`).join('');$('#group-dialog').showModal();};
$('#group-form').onsubmit=async e=>{e.preventDefault();const ids=$$('input[name=bots]:checked',e.currentTarget).map(x=>x.value);if(ids.length<2){toast('Chọn ít nhất hai bot cho hội thoại chung.');return;}const title=$('#group-title').value.trim();if(!title)return;
 try{const detail=await getJson('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bots:ids})});  // API: POST /api/sessions {bots}
  if(title)await getJson(`/api/sessions/${encodeURIComponent(detail.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});  // API: PATCH title
  $('#group-dialog').close();$('#group-form').reset();await refreshSessions();renderGroups();loadSession(detail.id,title||detail.title,ids[0]);toast('Đã tạo hội thoại chung.');}
 catch(err){toast('Không tạo được hội thoại: '+err.message);}};
