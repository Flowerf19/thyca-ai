// Ink-drop avatar engine: state metadata, 2.5D drop markup, face render, state adapter.
'use strict';
const states={
 idle:['Nghỉ','Sẵn sàng','Thở nhẹ · chớp mắt'],
 waiting:['Đang chờ','Chờ bạn','Nhìn xuống · tay buông nhẹ'],
 thinking:['Đang nghĩ','Đang suy nghĩ','Nhìn lên · nhích từng nấc'],
 writing:['Đang viết','Đang viết','Soạn nhạc theo nhịp chữ'],
 working:['Chạy việc','Đang làm việc','Tay gõ nhịp · nhún nhẹ'],
 done:['Xong','Đã hoàn tất','Mắt cười · giơ tay ✦'],
 error:['Lỗi','Có lỗi xảy ra','Mắt X · tay rũ, rối'],
 offline:['Offline','Mất kết nối','Nhắm mắt · đứng yên'],
 mention:['Được gọi @','Bạn gọi mình?','Giật mình trong 0,5 giây'],
 confused:['Bối rối','Cần làm rõ','Nghiêng đầu · dấu hỏi'],
 group:['Phối hợp','Đang phối hợp','Cùng nhịp · sáng khi nói']
};
const primaryStates=['idle','waiting','thinking','writing','working','done','error','offline'];
const extraStates=['mention','confused','group'];
const stateAliases={listening:'waiting',resting:'idle'};
const stateSummaries={
 idle:'Mình vẫn ở đây,\nkhi bạn cần.',waiting:'Mình đang chờ.\nBạn cứ viết tiếp nhé.',
 thinking:'Đang tổng hợp yêu cầu\nvà sắp xếp các bước thực hiện…',writing:'Mình đang viết\ncâu trả lời cho bạn…',
 working:'Đang kiểm tra từng phần\nvà đối chiếu kết quả.',done:'Xong rồi.\nMọi thứ đã sẵn sàng để bạn xem.',
 error:'Có lỗi khi thực hiện.\nBạn có thể thử lại lượt này.',offline:'Tạm mất kết nối.\nMình sẽ trở lại khi kết nối được khôi phục.',
 mention:'Bạn gọi mình à?',confused:'Mình cần bạn làm rõ\nmột chút trước khi tiếp tục.',group:'Mỗi người một phần,\ncùng hoàn thiện câu trả lời.'
};
const stateDetails={
 idle:'Chớp mắt theo nhịp riêng, thở rất nhẹ. Giọt mực đứng yên, hai tay buông nhẹ.',
 waiting:'Mắt hướng xuống ô nhập. Hạt mực vệ tinh dừng tại vị trí hiện tại, chấm sáng nhấp nháy chậm.',
 thinking:'Ánh mắt liếc lên, đầu hơi nghiêng, một tay nhấc nhẹ. Hạt vệ tinh tiến từng nấc, có khoảng nghỉ giữa các nhịp.',
 writing:'Hai tay gõ nhịp theo chữ mới. Hai nốt trên khuông nhạc khẽ chuyển động theo chữ, rồi lắng lại khi ngừng viết.',
 working:'Mắt nheo tập trung. Hai tay làm việc nhanh, cả giọt mực nhún nhẹ theo nhịp.',
 done:'Nhún nhẹ, bật lên một nhịp, giơ hai tay rồi mỉm cười. Một tia ✦ nhỏ lóe lên; sau 1,6 giây bot tự về nghỉ.',
 error:'Mắt chữ X, hai tay rũ xuống và viền đỏ. Giữ trạng thái lỗi để bạn kịp nhận ra.',
 offline:'Nhắm mắt, giảm màu và độ đậm. Dừng cả hơi thở, chớp mắt, tay lẫn hạt vệ tinh.',
 mention:'Mắt mở to, giật mình nhẹ trong 0,5 giây, rồi tiếp tục trạng thái trước đó.',
 confused:'Nghiêng đầu, một bên mày nhướn lên, một tay giơ nhẹ cùng dấu hỏi nhỏ. Chờ bạn làm rõ.',
 group:'Các hạt vệ tinh dùng chung một nhịp. Người đang nói sáng lên; các bot còn lại lắng nghe.'
};
const stateTimers=new Map(),mentionTimers=new Map(),musicBoxes=new Map();
const orbitDurations={thinking:9600,writing:6400,working:1500,group:4200};

// Ink-drop bodies are data, not markup: one silhouette per bot id, picked
// deterministically so a bot keeps its shape across rename/reload. avatar()
// emits a single namespaced <path> plus <use> layers, so the d appears once
// per avatar and every generated id stays unique per instance. The 2.5D
// depth planes are: contact shadow, body, depth (rim/groove, --well-*),
// shine (specular, --hi-*), droplet hands, orbiting satellite, then face.
const DROP_SHAPES=[
 // tall flame droplet
 'M37 3C39.2 10.8 45.8 16.8 50 24.8C55 34.6 54 46.6 46.2 52.8C37.4 59.2 22.4 58.4 15.2 50.2C8.2 42.2 10 30.2 16.8 21.2C22.8 13.2 30.6 7 37 3Z',
 // squat dome
 'M15.2 53.2C7.4 46.4 8.8 32.4 16.8 23.2C23 15.8 31.2 12.2 39.2 15C49 18.4 56 28.2 54.2 40C52.6 50.8 43.4 56 32 56.2C24.2 56.3 18.4 55.8 15.2 53.2Z',
 // double-lobe cloud
 'M13.2 52.2C6.4 46.2 7 35.2 14 28.8C18.2 20 27.2 14.2 34 17.2C40 10.2 50 13.2 54 21.8C59 27.8 58.2 39 52.2 45.8C45.4 53.6 36.2 56 27.2 55.2C21.2 54.8 16.2 54.6 13.2 52.2Z'
];
const DROP_NAMES=['tall','squat','cloud'];
const DROP_BY_ID={thyca:0,march7th:1,coder:2,researcher:1};
function dropShapeFor(b){
 const i=DROP_BY_ID[b.id]??Math.abs([...b.id].reduce((n,c)=>n+c.charCodeAt(0),0))%DROP_SHAPES.length;
 return {name:DROP_NAMES[i],d:DROP_SHAPES[i]};
}
const DROP_GRAIN_HI='M17 24h.1M24 16h.1M42 14h.1M50 31h.1M45 49h.1M25 52h.1M14 40h.1M36 20h.1';
const DROP_GRAIN_LO='M20 30h.1M46 22h.1M51 42h.1M30 54h.1M13 45h.1M39 48h.1';
const DROP_HAND_L='M13 44C14 47 17 49 16.8 52C16.6 54.8 13.2 56 10.8 54.2C8.6 52.5 9.2 49 11 46.8C11.8 45.8 12.5 45 13 44Z';
const DROP_HAND_R='M51 44C50 47 47 49 47.2 52C47.4 54.8 50.8 56 53.2 54.2C55.4 52.5 54.8 49 53 46.8C52.2 45.8 51.5 45 51 44Z';
function dropMarkup(b,uid){
 const {d}=dropShapeFor(b);
 return `<svg class="base" viewBox="0 0 64 64"><defs><path id="${uid}-body" d="${d}"/><radialGradient id="${uid}-grad" cx="0.32" cy="0.22" r="0.9"><stop offset="0" style="stop-color:var(--bot-highlight)"/><stop offset="0.42" style="stop-color:var(--bot-color)"/><stop offset="1" style="stop-color:var(--bot-dark)"/></radialGradient><clipPath id="${uid}-clip"><use href="#${uid}-body"/></clipPath></defs><ellipse class="drop-contact" cx="32" cy="57.6" rx="19" ry="2.7"/><use href="#${uid}-body" class="drop-body" fill="url(#${uid}-grad)"/><g clip-path="url(#${uid}-clip)"><use href="#${uid}-body" fill="url(#dropShade)"/><use href="#${uid}-body" fill="url(#dropHl)"/><ellipse class="drop-spec-soft" cx="23" cy="18" rx="14" ry="9" transform="rotate(-28 23 18)"/><g class="drop-depth"><path class="drop-rim" d="M50.2 27.5C53.2 36.5 51.4 45.8 44.8 51.2"/><path class="drop-base" d="M16.8 48.8C23.6 54.6 38.8 55.2 46.6 49.8"/><path class="drop-groove-lo" transform="translate(.8 .65)" d="M13.8 37.5C13 28 16.8 19.2 24.4 13.6"/><path class="drop-groove-hi" d="M13.8 37.5C13 28 16.8 19.2 24.4 13.6"/><path class="drop-grain-lo" d="${DROP_GRAIN_LO}"/><path class="drop-grain-hi" d="${DROP_GRAIN_HI}"/></g><g class="drop-shine"><path class="drop-spec-hard" d="M17.5 26C19.5 16.5 26.5 9.5 34.5 7.6"/><circle class="drop-spec-dot" cx="18.6" cy="29.4" r="1.05"/></g></g><use href="#${uid}-body" class="drop-edge"/><g class="drop-hands"><g class="drop-hand drop-hand-l"><path d="${DROP_HAND_L}" fill="url(#${uid}-grad)"/><ellipse class="drop-hand-spec" cx="12.2" cy="48.6" rx="1.1" ry="1.9" transform="rotate(-18 12.2 48.6)"/></g><g class="drop-hand drop-hand-r"><path d="${DROP_HAND_R}" fill="url(#${uid}-grad)"/><ellipse class="drop-hand-spec" cx="51.8" cy="48.6" rx="1.1" ry="1.9" transform="rotate(18 51.8 48.6)"/></g></g><circle class="drop-orbit" cx="32" cy="32" r="23.2"/></svg>`;
}

const dropState = state => stateAliases[state]||state;
function expressionFor(b){return b.mentioned?'mention':b.state;}
function orbitAngle(b){
 const duration=orbitDurations[b.state];if(!duration)return b.orbitAngle;
 let turns=(Date.now()-b.orbitAt)/duration;
 if(b.state==='thinking')turns=Math.floor(turns*12)/12;
 return (b.orbitAngle+turns*360)%360;
}
function orbitStyle(b){return `--ring-start:${b.orbitAngle}deg;--ring-end:${b.orbitAngle+360}deg;--ring-delay:${-(Date.now()-b.orbitAt)/1000}s`;}
function sealFace(uid){return `<span class="seal-face"><svg viewBox="0 0 64 64"><defs><clipPath id="${uid}-eye-clip"><ellipse cx="25.5" cy="34.5" rx="3.9" ry="6.2"/><ellipse cx="39" cy="32" rx="3.9" ry="6.2"/></clipPath></defs><g class="seal-features"><g class="seal-blink"><g class="seal-eye-open"><ellipse class="seal-sock eye-left" cx="25.5" cy="34.5" rx="3.9" ry="6.2"/><ellipse class="seal-sock" cx="39" cy="32" rx="3.9" ry="6.2"/><g clip-path="url(#${uid}-eye-clip)"><g class="seal-pupils"><ellipse class="seal-glint eye-left" cx="26.5" cy="32.2" rx=".95" ry="1.7"/><ellipse class="seal-glint" cx="40" cy="29.7" rx=".95" ry="1.7"/></g><path class="seal-idle-lids" d="M18 23H32V33Q25.5 31 18 33ZM32 20H46V30.5Q39 28.5 32 30.5Z"/><path class="seal-lid" d="M18 24 33 32V22H18ZM31 28 46 24V19H31Z"/></g></g><g class="seal-eye-closed seal-eye-happy" fill="none" stroke="var(--bot-pupil)" stroke-width="2.9" stroke-linecap="round"><path d="M21 35Q25.5 28 30 35M34.5 32.5Q39 25.5 43.5 32.5"/></g><g class="seal-eye-closed seal-eye-rest" fill="none" stroke="var(--bot-pupil)" stroke-width="2.6" stroke-linecap="round"><path d="M21 33Q25.5 39 30 33M34.5 30.5Q39 36.5 43.5 30.5"/></g></g><path class="seal-idle-wink" d="M21 34Q25.5 29 30 34" fill="none" stroke="var(--bot-pupil)" stroke-width="2.8" stroke-linecap="round"/><ellipse class="seal-mouth-hum" cx="33" cy="45" rx="1.5" ry="2.1" fill="none" stroke="var(--bot-pupil)" stroke-width="1.5"/><g class="seal-eye-error" fill="none" stroke="var(--bot-pupil)" stroke-width="2.5" stroke-linecap="round"><path d="m22 30 7 8m0-8-7 8m13-10 7 8m0-8-7 8"/></g><g class="seal-brow" fill="none" stroke-width="1.5" stroke-linecap="round"><g class="brow-left"><path class="brow-shadow" transform="translate(0 .8)" d="M19.5 24.8C22.2 21.2 27.8 21 30.6 23.8"/><path d="M19.5 24.8C22.2 21.2 27.8 21 30.6 23.8"/></g><g class="brow-right"><path class="brow-shadow" transform="translate(0 .8)" d="M33.9 21.3C36.7 18.7 42.3 18.9 44.6 21.8"/><path d="M33.9 21.3C36.7 18.7 42.3 18.9 44.6 21.8"/></g></g><path class="seal-mouth seal-mouth-smile" d="M29 44Q33 48 37 43" fill="none" stroke="var(--bot-pupil)" stroke-width="1.7" stroke-linecap="round"/><path class="seal-mouth seal-mouth-worried" d="M29.5 46Q33 43 36.5 45" fill="none" stroke="var(--bot-pupil)" stroke-width="1.5" stroke-linecap="round"/><g class="seal-mouth-rest"><path class="mouth-rest-hi" transform="translate(0 .8)" d="M28.8 44.6Q32.4 47.4 36 44.2"/><path d="M28.8 44.6Q32.4 47.4 36 44.2"/></g></g><g class="seal-music"><path class="seal-music-staff" d="M47 10h16M47 13h16M47 16h16M47 19h16M47 22h16"/><g class="seal-score-note score-note-a" fill="var(--bot-stamp)" stroke="var(--bot-dark)" stroke-width=".45"><ellipse cx="50.5" cy="20" rx="2.15" ry="1.35" transform="rotate(-20 50.5 20)"/><path d="M52.5 20V10c.5 1.8 3.2 1.6 2.7 4" fill="none" stroke="var(--bot-ink)" stroke-width="1.2" stroke-linecap="round"/></g><g class="seal-score-note score-note-b" fill="var(--bot-stamp)" stroke="var(--bot-dark)" stroke-width=".45"><ellipse cx="58.2" cy="16.5" rx="2.15" ry="1.35" transform="rotate(-20 58.2 16.5)"/><path d="M60.2 16.5V6.5" fill="none" stroke="var(--bot-ink)" stroke-width="1.2" stroke-linecap="round"/></g></g><circle class="seal-wait-dot" cx="33" cy="47" r="1.8" fill="var(--bot-eye)"/><g class="seal-question" fill="none" stroke="var(--bot-ink)" stroke-width="1.8" stroke-linecap="round"><path d="M54 11c0-5 7-5 7-.5 0 3-3.5 2.5-3.5 5"/><circle cx="57.5" cy="20" r=".8" fill="var(--bot-ink)" stroke="none"/></g><g class="seal-alert" stroke="var(--bot-mid)" stroke-width="2" stroke-linecap="round"><path d="m52 10 2-5m3 9 5-1M14 15l-3-3"/></g><path class="seal-tangle" d="M16 45c-5 5 3 9 6 3s-4-10-5-3 12 9 14 4-9-5-8 0 13 7 18 1" fill="none" stroke="var(--bot-eye-edge)" stroke-width="1" stroke-linecap="round"/><g class="seal-idle-note" fill="none" stroke="var(--bot-mid)" stroke-width="1.5" stroke-linecap="round"><path d="M55 18V8l6-1v9M55 9l6-1"/><ellipse cx="53.4" cy="18.5" rx="2" ry="1.3" fill="var(--bot-mid)"/><ellipse cx="59.4" cy="16.5" rx="2" ry="1.3" fill="var(--bot-mid)"/></g><path class="seal-idle-z" d="M53 12h6l-6 7h6m0-15h4l-4 5h4" fill="none" stroke="var(--bot-mid)" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><g class="seal-idle-cloud" fill="var(--bot-mid)"><circle cx="54" cy="17" r="1.2"/><circle cx="59" cy="11" r="2.2"/></g><path class="seal-idle-star" d="m55 5 1.8 5.2L62 12l-5.2 1.8L55 19l-1.8-5.2L48 12l5.2-1.8Z" fill="var(--bot-mid)"/><g class="seal-done-star" fill="var(--bot-mid)"><path d="m55 5 1.8 5.2L62 12l-5.2 1.8L55 19l-1.8-5.2L48 12l5.2-1.8Z"/><path d="m10 16 .8 2.2L13 19l-2.2.8L10 22l-.8-2.2L7 19l2.2-.8Z"/></g><path class="seal-done-cheeks" d="m21 41 3 1m17-4 3-1" fill="none" stroke="var(--bot-eye-edge)" stroke-width="1.4" stroke-linecap="round"/><g class="seal-thought-marks" stroke="var(--bot-mid)" fill="none" stroke-width="2.4" stroke-linecap="round"><path d="m55 12 2-5m1.5 9 4-2"/></g><g class="seal-joy-marks" stroke="var(--bot-mid)" fill="none" stroke-width="1.8" stroke-linecap="round"><path d="m54 12 2-4m1 9 5-1M12 20l-3-2"/></g></svg></span>`;}

function avatar(b,state=b.state,extra=''){
 const uid='drop'+(++avatarCounter),preview=extra.includes('expression-preview');
 const markup=dropMarkup(b,uid),shape=dropShapeFor(b).name;
 const expression=preview?state:expressionFor(b);
 return `<span class="seal-avatar mk mk-drop ${extra}" data-bot-avatar="${b.id}" data-drop-shape="${shape}" data-state="${dropState(state)}" data-expression="${expression}" ${!preview&&b.state==='idle'&&b.idle.active?`data-idle="${b.idle.active}"`:[]} data-selected="${b.id===selectedBot}" style="${paletteStyle(b)};${orbitStyle(b)};--phase:${b.phase}s;--breathe:${5.7+avatarCounter%4*.6}s" aria-hidden="true"><span class="seal-motion">${markup}${sealFace(uid)}</span></span>`;
}
function syncAvatar(a,b){
 const changed=a.dataset.state!==b.state;
 a.dataset.state=b.state;a.dataset.expression=expressionFor(b);a.dataset.selected=b.id===selectedBot;
 if(changed){a.style.setProperty('--ring-start',b.orbitAngle+'deg');a.style.setProperty('--ring-end',(b.orbitAngle+360)+'deg');a.style.setProperty('--ring-delay',-(Date.now()-b.orbitAt)/1000+'s');a.classList.remove('blink');}
 if(['offline','error','done'].includes(b.state)||b.mentioned)a.classList.remove('blink');
 if(idleEligible(b)&&b.idle.active)a.dataset.idle=b.idle.active;else delete a.dataset.idle;
}

function setState(id,state,summary,elapsed){
 const b=getBot(id);state=stateAliases[state]||state;if(!b||!states[state])return false;
 if(state==='mention')return mentionBot(id);
 if(state!=='idle')clearIdle(b);else if(b.state!=='idle'){clearIdle(b);b.idle.since=Date.now();}
 if(['error','offline'].includes(state)&&lab.botId===id&&lab.mode)stopLab(false);
 if(['error','offline'].includes(state)){const t=[...runs.values()].find(x=>x.botId===id);if(t)cancelTurn(t,state==='offline'?'Kết nối đã ngắt. Phần đã viết được giữ lại.':'Lượt trả lời gặp lỗi. Bạn có thể thử lại.',state==='offline'?'mất kết nối':'gặp lỗi');}
 clearTimeout(stateTimers.get(id));clearTimeout(mentionTimers.get(id));b.mentioned=false;
 const changed=b.state!==state;const angle=orbitAngle(b);b.revision++;b.state=state;
 if(changed){b.orbitAngle=state==='waiting'&&angle===0?135:angle;b.orbitAt=Date.now();}
 if(summary!==undefined)b.summary=String(summary);else b.summary=stateSummaries[state];
 if(elapsed!==undefined)b.elapsed=elapsed;
 if(state!=='writing')clearMusic(id);
 if(['offline','error'].includes(state)&&activeGroup?.speaker===id)activeGroup.speaker=null;
 updateCard(b);
 if(['error','offline'].includes(state))announce(b.name+': '+states[state][1]);
 if(state==='done'){const revision=b.revision;stateTimers.set(id,setTimeout(()=>{if(b.state==='done'&&b.revision===revision)setState(id,'idle','Xong rồi. Mình vẫn ở đây\nkhi bạn cần thêm.',0);},1600));}
 return true;
}
function mentionBot(id){
 const b=getBot(id);if(!b||['offline','error'].includes(b.state))return false;
 clearTimeout(mentionTimers.get(id));clearIdle(b);b.mentioned=true;updateCard(b);
 mentionTimers.set(id,setTimeout(()=>{b.mentioned=false;updateCard(b);},500));
 announce(b.name+': Bạn gọi mình?');return true;
}
function clearMusic(id){
 const box=musicBoxes.get(id);if(box){clearInterval(box.timer);musicBoxes.delete(id);}
 $$('.seal-avatar[data-bot-avatar="'+id+'"]:not(.expression-preview)').forEach(a=>{delete a.dataset.streaming;['--note-a-y','--note-b-y','--note-a-alpha','--note-b-alpha'].forEach(p=>a.style.removeProperty(p));a.style.removeProperty('--writing-gaze');});
}
// Music-note damper: the first chunk beats immediately; chunks arriving
// inside a 200ms beat only add energy, and the loop spends ~2 quiet beats
// before resting — the same streaming attrs and the same clearMusic rest.
const MUSIC_BEAT_MS=200,MUSIC_DECAY=8,MUSIC_QUIET_BEATS=2;
function paintMusicBeat(id,seed){
 $$('.seal-avatar[data-bot-avatar="'+id+'"]:not(.expression-preview)').forEach(a=>{
  a.dataset.streaming='true';a.style.setProperty('--writing-gaze',((seed%9)/4-1)+'px');
  a.style.setProperty('--note-a-y',(-.4-(seed%6)*.38).toFixed(2)+'px');a.style.setProperty('--note-b-y',(-.2-((seed*3)%7)*.3).toFixed(2)+'px');a.style.setProperty('--note-a-alpha',(.6+(seed%4)*.1).toFixed(2));a.style.setProperty('--note-b-alpha',(.6+((seed+2)%4)*.1).toFixed(2));
 });
}
function musicBeat(id){
 const box=musicBoxes.get(id);if(!box)return;
 if(box.energy>0){box.energy=Math.max(0,box.energy-MUSIC_DECAY);paintMusicBeat(id,box.seed);box.quiet=0;}
 else if(++box.quiet>=MUSIC_QUIET_BEATS)clearMusic(id);
}
// Called by the same text-delta callback that reveals characters, never by an ambient animation.
function notifyText(id,chunk){
 const b=getBot(id);if(!b||b.state!=='writing'||!String(chunk||'').trim())return false;
 const text=String(chunk),seed=[...text].reduce((n,c)=>n+c.charCodeAt(0),0);
 let box=musicBoxes.get(id);
 if(!box){box={energy:0,seed:0,quiet:0,timer:0};musicBoxes.set(id,box);}
 box.energy+=text.length;box.seed+=seed;box.quiet=0;
 if(!box.timer){paintMusicBeat(id,box.seed);box.timer=setInterval(()=>musicBeat(id),MUSIC_BEAT_MS);}
 return true;
}
// 2.5D hover parallax: its own var layer, so data-state/data-idle never move.
// Face follows the pointer, the well lags, the highlight drifts opposite.
function applyParallax(seal,nx,ny){
 const cx=Math.max(-1,Math.min(1,nx||0)),cy=Math.max(-1,Math.min(1,ny||0));
 seal.style.setProperty('--par-x',(cx*4).toFixed(2)+'px');seal.style.setProperty('--par-y',(cy*4).toFixed(2)+'px');
 seal.style.setProperty('--well-x',(cx*1.75).toFixed(2)+'px');seal.style.setProperty('--well-y',(cy*1.75).toFixed(2)+'px');
 seal.style.setProperty('--hi-x',(cx*-1.5).toFixed(2)+'px');seal.style.setProperty('--hi-y',(cy*-1.5).toFixed(2)+'px');
 seal.dataset.par='1';
}
function resetParallax(seal){
 ['--par-x','--par-y','--well-x','--well-y','--hi-x','--hi-y'].forEach(p=>seal.style.removeProperty(p));
 delete seal.dataset.par;
}
function sealFromPoint(host,x,y){
 const seal=host.querySelector('.seal-avatar:not(.expression-preview)');
 if(!seal||seal.closest('.motion-paused'))return null;
 const r=seal.getBoundingClientRect();
 if(!r.width&&!r.height)return null;
 return {seal,nx:(x-(r.left+r.width/2))/(r.width/2||1),ny:(y-(r.top+r.height/2))/(r.height/2||1)};
}
const reduceMotion=typeof window!=='undefined'&&typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if(typeof document!=='undefined'&&document.addEventListener&&!reduceMotion){
 document.addEventListener('pointermove',ev=>{
  const host=ev.target&&ev.target.closest?ev.target.closest('.bot-card, .stage-portrait'):null;
  document.querySelectorAll('.seal-avatar[data-par]').forEach(s=>{if(!host||!host.contains(s))resetParallax(s);});
  if(!host)return;
  const hit=sealFromPoint(host,ev.clientX,ev.clientY);
  if(hit)applyParallax(hit.seal,hit.nx,hit.ny);
 });
 document.addEventListener('pointerout',ev=>{
  const host=ev.target&&ev.target.closest?ev.target.closest('.bot-card, .stage-portrait'):null;
  if(host&&!(ev.relatedTarget&&host.contains(ev.relatedTarget)))host.querySelectorAll('.seal-avatar[data-par]').forEach(resetParallax);
 });
}
