// Bot sidebar: team cards, pockets, group rows, group dialogs.
'use strict';
let activeGroup=null;

function activeSidebarBot(){const router=window.ThycaRouter;return router?.currentPage?.()?router.highlightedBot():selectedBot;}
function sidebarActionLabel(b){const router=window.ThycaRouter;return router?.currentPage?.()?router.sidebarLabel(b.id,b.name,states[expressionFor(b)][1]):`Trò chuyện với ${b.name}`;}
function previewLine(b){const whisper=b.state==='idle'&&b.idle.comment?'“'+b.idle.comment+'”':'';return whisper||String(b.summary||'').split('\n')[0];}
function card(b){const activeId=activeSidebarBot();return `<article class="bot-card team-cluster team-envelope" id="bot-${b.id}" data-bot-id="${b.id}" data-state="${b.state}" data-open="${b.open}" data-selected="${b.id===activeId}" style="${paletteStyle(b)}"><header class="bot-header"><button class="bot-select team-bot-item" data-action="select" data-bot-id="${b.id}" aria-label="${sidebarActionLabel(b)}">${avatar(b)}<span class="bot-heading"><span class="bot-name session-name">${b.name}</span><span class="bot-role">${b.role}</span><span class="header-status"><span class="status-dot"></span><span class="header-status-label">${states[b.state][1]}</span></span></span></button><div class="card-actions"><button class="icon-button favorite" data-action="favorite" aria-label="Yêu thích ${b.name}" aria-pressed="${b.favorite}">${icon('star')}</button><button class="icon-button collapse-button" data-action="collapse" aria-label="Thu hoặc mở ${b.name}" aria-controls="pocket-${b.id}" aria-expanded="${b.open}">${icon('chevron')}</button></div></header><div class="compact-preview"><span class="status-dot"></span><span class="preview-text">${previewLine(b)}</span></div><div class="pocket team-pocket-viewport" id="pocket-${b.id}" ${b.open?'':'inert'}><div class="pocket-inner"><section class="thinking-paper" aria-label="Thinking của ${b.name}"><div class="thinking-heading">${dots}<span class="thinking-title">Thinking</span><span class="elapsed">${b.elapsed} giây</span></div><div class="thinking-scroll" tabindex="0" aria-label="Tiến trình của ${b.name}"><p class="thinking-summary"></p><ol class="progress-list" hidden></ol><p class="thought-extra" hidden></p></div><button class="reply-action" data-action="reply"${['waiting','confused'].includes(b.state)?'':' hidden'}>${icon('arrow')}Trả lời</button><footer class="paper-footer"><button data-action="info">${icon('info')}Thông tin bot</button><button data-action="capabilities">${icon('list')}Khả năng</button></footer></section></div></div></article>`;}
/* Keyed reconcile: exiting rows render first (fade-out), then the last 3
   live rows. Existing <li> nodes are updated in place so their animations
   don't restart on every stream event; new nodes get .li-enter once. */
function syncFeedList(b,list){
 const feed=b.feed||[];
 const alive=feed.filter(f=>!f.exiting),vis=new Set(alive.slice(-3));
 const render=feed.filter(f=>f.exiting||vis.has(f));
 list.hidden=!render.length;
 const keyed=new Map($$('li',list).map(li=>[li.dataset.eid,li])),want=new Set(render.map(f=>'f'+f.eid));
 keyed.forEach((li,eid)=>{if(!want.has(eid))li.remove();});
 let cursor=list.firstElementChild;
 render.forEach(f=>{
  const eid='f'+f.eid;let li=keyed.get(eid);
  if(!li){li=document.createElement('li');li.dataset.eid=eid;li._enter=true;
   li.addEventListener('animationend',()=>{li.classList.remove('li-enter');li._enter=false;},{once:true});}
  const html=`<span class="step-mark">${f.done?icon('check'):''}</span><span>${escapeHTML(f.text)}</span>`;
  if(li._feedHtml!==html){li.innerHTML=html;li._feedHtml=html;}
  li.className=(f.exiting?'li-exit ':'')+(f.done?'complete':'current')+(f.cls?' '+f.cls:'')+(li._enter?' li-enter':'');
  if(li!==cursor)list.insertBefore(li,cursor);else cursor=cursor.nextElementSibling;
 });
}

function renderTeam(){ const el = $('#team-bot-list'); if (el) el.innerHTML = bots.map(card).join(''); }
function updateCard(b){
 const el=$('#bot-'+b.id);if(!el)return;
 el.dataset.state=b.state;el.dataset.open=b.open;el.dataset.selected=b.id===activeSidebarBot();
 $('.header-status-label',el).textContent=states[expressionFor(b)][1];
 $('.bot-select',el).setAttribute('aria-label',sidebarActionLabel(b));
 const whisper=b.state==='idle'?b.idle.comment:'';
 const summary=$('.thinking-summary',el);summary.textContent=whisper?'“'+whisper+'”':String(b.summary||'').split('\n')[0];summary.style.whiteSpace='nowrap';summary.classList.toggle('idle-comment',!!whisper);
 $('.preview-text',el).textContent=previewLine(b);
 $('.reply-action',el).hidden=!['waiting','confused'].includes(b.state);
 $('.compact-preview',el).dataset.idleComment=String(!!whisper);$('.preview-text',el).title=whisper;
 $('.elapsed',el).textContent=b.elapsed?b.elapsed+' giây':'';
 $('.thinking-title',el).textContent=whisper?'Lúc rảnh':b.state==='thinking'?'Thinking':states[b.state][1];
 $('.collapse-button',el).setAttribute('aria-expanded',b.open);
 $('.pocket',el).inert=!b.open;
 syncFeedList(b,$('.progress-list',el));
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
function openBot(id,open=true){
 bots.forEach(b=>{b.open=b.id===id?open:false;updateCard(b)});
 if(open)setTimeout(()=>$('#bot-'+id).scrollIntoView({block:'nearest',behavior:motionPaused?'auto':'smooth'}),280);
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

$('#team-bot-list').addEventListener('click',e=>{const btn=e.target.closest('button');if(!btn)return;const b=getBot(btn.closest('.bot-card').dataset.botId);switch(btn.dataset.action){case 'select':window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);break;case 'reply':window.ThycaRouter?ThycaRouter.dispatch('selectBot',b.id):selectBot(b.id);document.querySelector('#message')?.focus?.();break;case 'collapse':openBot(b.id,!b.open);break;case 'favorite':b.favorite=!b.favorite;btn.setAttribute('aria-pressed',b.favorite);toast(b.favorite?'Đã đánh dấu '+b.name+'.':'Đã bỏ đánh dấu '+b.name+'.');break;case 'info':openInfo(b.id);break;case 'capabilities':openInfo(b.id,true);break;}});
$('#group-list')?.addEventListener('click',e=>{const row=e.target.closest('[data-group]');if(!row)return;const g=groups.find(x=>x.id===row.dataset.group);window.ThycaRouter?ThycaRouter.dispatch('loadSession',g.id,g.title,g.bots[0]):loadSession(g.id,g.title,g.bots[0]);});

$('#bot-drawer-body').addEventListener('click',e=>{const btn=e.target.closest('[data-session]');if(!btn)return;const b=getBot(btn.dataset.bot);const s=b.sessions.find(x=>x[0]===btn.dataset.session);window.ThycaRouter?ThycaRouter.dispatch('loadSession',s[0],s[1],b.id):loadSession(s[0],s[1],b.id);$('#bot-drawer').close();openBot(b.id);});
const teamSharedCreate=$('#team-shared-create');
if(teamSharedCreate)teamSharedCreate.onclick=()=>{$('#group-options').innerHTML=bots.map(b=>`<label class="check-row"><input type="checkbox" name="bots" value="${b.id}" checked><span class="check-avatar" style="${paletteStyle(b)}">${avatar(b)}</span><span style="color:${b.palette.ink}">${b.name}</span><small style="color:#92846d">${b.role}</small></label>`).join('');$('#group-dialog').showModal();};
$('#group-form').onsubmit=async e=>{e.preventDefault();const ids=$$('input[name=bots]:checked',e.currentTarget).map(x=>x.value);if(ids.length<2){toast('Chọn ít nhất hai bot cho hội thoại chung.');return;}const title=$('#group-title').value.trim();if(!title)return;
 try{const detail=await getJson('/api/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bots:ids})});  // API: POST /api/sessions {bots}
  if(title)await getJson(`/api/sessions/${encodeURIComponent(detail.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});  // API: PATCH title
  $('#group-dialog').close();$('#group-form').reset();await refreshSessions();renderGroups();loadSession(detail.id,title||detail.title,ids[0]);toast('Đã tạo hội thoại chung.');}
 catch(err){toast('Không tạo được hội thoại: '+err.message);}};
