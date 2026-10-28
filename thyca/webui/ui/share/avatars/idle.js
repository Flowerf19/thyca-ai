// Idle layer: ambient gestures + short comments for resting bots. Never changes work state.
'use strict';
// Ambient gestures never change the semantic work state or write chat messages.
const idleCatalog={
 look:{label:'Nhìn quanh',hint:'Trái, phải, lên, xuống',detail:'Liếc nhìn quanh, dừng ở mỗi hướng rồi trở lại. Mắt và đầu chuyển động nhẹ, không cùng nhịp với bot khác.',duration:5800,weight:30,quiet:0,lines:['Mình vẫn ở đây, cạnh ô chat này.','Để mình ngó quanh một chút nhé.','Một khoảng lặng cũng có giai điệu.']},
 peek:{label:'Tò mò',hint:'Nghiêng sang ô chat',detail:'Hơi nghiêng người, mắt ngó sang ô chat như muốn biết bạn đang nghĩ gì.',duration:4600,weight:15,quiet:0,lines:['Bạn đang nghĩ gì thế?','Mình tò mò một chút thôi.','Chỗ này còn đủ cho một ý tưởng.']},
 hum:{label:'Ngân nga',hint:'Mắt cười, một nốt nhạc',detail:'Mắt cong lại, đung đưa rất nhẹ. Một nốt nhạc bay lên rồi tan đi.',duration:5200,weight:12,quiet:10000,lines:['♪ Một nốt nhạc cho khoảng nghỉ.','Nếu câu chữ có nhạc nền thì sao nhỉ?','Hôm nay mình ngân nga một chút.']},
 dream:{label:'Mơ màng',hint:'Nhìn lên một khoảng xa',detail:'Mắt nhìn lên, đầu hơi nghiêng. Hai chấm nhỏ trôi qua như một ý nghĩ vu vơ.',duration:6000,weight:13,quiet:0,lines:['Giá mà ý tưởng mọc trên cây…','Khoảng trắng cũng là một phần bài thơ.','Một đám mây vừa đi ngang trong đầu.']},
 stretch:{label:'Vươn vai',hint:'Duỗi nhẹ rồi thả lỏng',detail:'Giọt mực duỗi lên một chút, hai tay giơ nhẹ, mắt nhắm cười rồi thả lỏng.',duration:4800,weight:8,quiet:20000,lines:['Cho mình vươn mình một chút.','Ưỡn nhẹ một cái… ổn rồi.','Nghỉ một nhịp rồi tiếp nhé.']},
 sleepy:{label:'Lim dim',hint:'Gật gù một nhịp',detail:'Sau một lúc lâu yên tĩnh, mắt lim dim và có chữ z nhỏ. Chỉ chợp mắt một nhịp, rồi tỉnh lại.',duration:6400,weight:7,quiet:60000,lines:['Cho mình lim dim một nhịp nhé.','Gọi là mình tỉnh ngay mà.','Z… à, mình vẫn ở đây nhé.']},
 wink:{label:'Nháy mắt',hint:'Một cái nháy mắt vui',detail:'Nháy một bên mắt, mỉm cười rất khẽ rồi trở về ánh nhìn bình thường.',duration:3600,weight:8,quiet:0,lines:['Suỵt, một cái nháy mắt thôi.','Có mình ở đây mà.','Nháy mắt một cái cho vui.']},
 spark:{label:'Lóe ý tưởng',hint:'Một tia ✦ rất nhỏ',detail:'Mắt sáng lên, ngước nhìn một tia nhỏ cạnh giọt mực. Một chút vui lúc chưa có việc.',duration:4200,weight:7,quiet:15000,lines:['Biết đâu ý hay đến từ lúc nghỉ.','Một tia nhỏ cho ngày hôm nay.','Còn chỗ cho một điều bất ngờ.']}
};
const idleVoice={
 thyca:{look:['Một khoảng lặng cũng có giai điệu.','Để mình ngó quanh một chút nhé.','Mình vẫn ở đây, cạnh ô chat này.'],spark:['Một ý nhỏ vừa ghé ngang.','Biết đâu câu tiếp theo sẽ thành thơ.','Một tia nhỏ cho ngày hôm nay.']},
 coder:{look:['Có ai vừa gọi mình không nhỉ?','Nghỉ một nhịp, code tính sau.','Một dấu ngoặc cũng cần có đôi.'],dream:['Nếu dấu chấm phẩy biết kể chuyện…','Khoảng trắng cũng đáng yêu đấy chứ.','Một tách trà cho khoảng nghỉ này.'],spark:['Ý tưởng hay đôi khi đến lúc nghỉ.','Một nét nhỏ, biết đâu thành điều hay.','Còn chỗ cho một điều bất ngờ.']},
 researcher:{look:['Ngoài kia có gì hay không nhỉ?','Một câu hỏi hay đang trốn ở đâu đó.','Ngó một vòng, biết đâu thấy điều hay.'],dream:['Nếu mỗi câu hỏi là một ngôi sao…','Biết đâu điều hay ở ngay bên cạnh.','Còn cả một bầu trời để tò mò.'],spark:['Một điều nhỏ cũng đủ gợi tò mò.','Biết đâu đây là đầu một câu chuyện.','Một tia nhỏ cho ngày hôm nay.']}
};
const idleOptions={enabled:true,comments:true};
const idleClock={lastCommentAt:Date.now()-15000,lastInputAt:0};
const between=(min,max)=>min+Math.random()*(max-min);
bots.forEach((b,i)=>b.idle={since:Date.now(),nextAt:Date.now()+between(3400,6200)+i*900,nextGaze:Date.now()+1200+i*850,active:null,endsAt:0,comment:'',commentUntil:0,last:null,lastGaze:-1,recentComments:[]});

function idleEligible(b){return b.state==='idle'&&!b.mentioned&&!activeGroup?.ids.includes(b.id);}
function paintIdle(b){
 $$('.seal-avatar[data-bot-avatar="'+b.id+'"]:not(.expression-preview)').forEach(a=>{if(idleEligible(b)&&b.idle.active)a.dataset.idle=b.idle.active;else delete a.dataset.idle;});
}
function clearIdle(b,render=false){
 const changed=!!(b.idle.active||b.idle.comment);b.idle.active=null;b.idle.endsAt=0;b.idle.comment='';b.idle.commentUntil=0;
 b.idle.nextAt=Date.now()+between(7000,13000);b.idle.nextGaze=Date.now()+between(1100,2600);
 $$('.seal-avatar[data-bot-avatar="'+b.id+'"]:not(.expression-preview)').forEach(a=>{delete a.dataset.idle;a.style.removeProperty('--gaze-x');a.style.removeProperty('--gaze-y');});
 if(render&&changed)updateCard(b);return changed;
}
function randomIdle(b,forced=false){
 const busyKinds=bots.filter(x=>x!==b).map(x=>x.idle.active);const quiet=Date.now()-Math.max(b.idle.since,idleClock.lastInputAt);
 let choices=Object.entries(idleCatalog).filter(([key,v])=>key!==b.idle.last&&!busyKinds.includes(key)&&(forced||quiet>=v.quiet));
 if(!choices.length)choices=Object.entries(idleCatalog).filter(([key])=>key!==b.idle.last);
 let value=Math.random()*choices.reduce((sum,[,v])=>sum+v.weight,0);
 return choices.find(([,v])=>(value-=v.weight)<0)?.[0]||choices[0][0];
}
function idleComment(b,key){
 const all=idleVoice[b.id]?.[key]||idleCatalog[key].lines;
 let choices=all.filter(line=>!b.idle.recentComments.includes(line));if(!choices.length)choices=all.filter(line=>line!==b.idle.recentComments.at(-1));
 const line=choices[Math.floor(Math.random()*choices.length)];b.idle.recentComments.push(line);b.idle.recentComments=b.idle.recentComments.slice(-3);return line;
}
function beginIdle(id,key=null,forced=false){
 const b=getBot(id);if(!b||!idleEligible(b)||(key&&!idleCatalog[key]))return false;
 if(!forced&&!idleOptions.enabled)return false;
 key=key||randomIdle(b,forced);clearIdle(b);const moment=idleCatalog[key],now=Date.now();
 b.idle.active=key;b.idle.last=key;b.idle.endsAt=now+moment.duration;b.idle.nextAt=b.idle.endsAt+between(8000,17000);
 if(idleOptions.comments&&(forced||(now-idleClock.lastCommentAt>10000&&now-idleClock.lastInputAt>5000&&Math.random()<.6))){
  b.idle.comment=idleComment(b,key);b.idle.commentUntil=now+Math.max(7600,moment.duration+1800);idleClock.lastCommentAt=now;
 }
 updateCard(b);return key;
}
function setIdleOptions(options={}){
 if(typeof options.enabled==='boolean')idleOptions.enabled=options.enabled;
 if(typeof options.comments==='boolean')idleOptions.comments=options.comments;
 bots.forEach(b=>{if(options.enabled===false)clearIdle(b);if(!idleOptions.comments){b.idle.comment='';b.idle.commentUntil=0;}updateCard(b);});
 $('#idle-auto-toggle').checked=idleOptions.enabled;$('#idle-comments-toggle').checked=idleOptions.comments;return {...idleOptions};
}
function idleInput(id){idleClock.lastInputAt=Date.now();const b=getBot(id);if(b&&b.state==='idle')clearIdle(b,true);}
function idleTick(){
 if(document.hidden||motionPaused)return;const now=Date.now();
 bots.forEach(b=>{
  if(!idleEligible(b)){if(b.idle.active||b.idle.comment)clearIdle(b,true);return;}
  let changed=false;
  if(b.idle.active&&now>=b.idle.endsAt){b.idle.active=null;b.idle.nextGaze=now+between(1000,2200);changed=true;}
  if(b.idle.comment&&now>=b.idle.commentUntil){b.idle.comment='';changed=true;}
  if(changed)updateCard(b);
  if(!idleOptions.enabled||($('#expressions-dialog').open&&lab.botId===b.id&&!lab.idleTab)||(lab.botId===b.id&&lab.mode&&lab.mode!=='idle-preview'))return;
  if(b.id===selectedBot&&now-idleClock.lastInputAt<6000)return;
  if(!b.idle.active&&now>=b.idle.nextAt){
   if(bots.filter(x=>x.idle.active).length>=2){b.idle.nextAt=now+between(1800,3400);return;}
   beginIdle(b.id);return;
  }
  if(!b.idle.active&&now>=b.idle.nextGaze&&!reduced.matches){
   const positions=[[-2.7,.3],[1.7,-.4],[.3,2.3],[-.8,-1.9],[0,0]];
   let index=Math.floor(Math.random()*(positions.length-1));if(index>=b.idle.lastGaze)index++;index%=positions.length;b.idle.lastGaze=index;
   const [x,y]=positions[index];$$('.seal-avatar[data-bot-avatar="'+b.id+'"]:not(.expression-preview)').forEach(a=>{a.style.setProperty('--gaze-x',x+'px');a.style.setProperty('--gaze-y',y+'px');});
   b.idle.nextGaze=now+between(2600,5100);
  }
 });
}
