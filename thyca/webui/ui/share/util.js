// Shared DOM + color helpers.
'use strict';
'use strict';
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const dots = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';
const escapeHTML = str => String(str).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

function mix(hex, target, amount) {
  const a=hex.match(/[a-f\d]{2}/gi).map(x=>parseInt(x,16));
  const b=target.match(/[a-f\d]{2}/gi).map(x=>parseInt(x,16));
  return '#'+a.map((x,i)=>Math.round(x+(b[i]-x)*amount).toString(16).padStart(2,'0')).join('');
}
function derivePalette(color) {
  return {color,ink:mix(color,'171f17',.18),dark:mix(color,'0e1711',.55),highlight:mix(color,'f7ebc8',.33),mid:mix(color,'d3c9a5',.37),border:mix(color,'e8dcc3',.62),rule:mix(color,'fff6de',.83),paper:mix(color,'f8f1de',.83),pale:mix(color,'fcf6e8',.92),sheet:mix(color,'fff8e9',.96),stamp:mix(color,'ebd29a',.72),eye:mix(color,'fff2ce',.94),'eye-edge':mix(color,'f7dfad',.87),pupil:mix(color,'111b15',.80),muted:mix(color,'9b8c72',.50)};
}

const paletteStyle=b=>Object.entries(b.palette).map(([k,v])=>`--bot-${k}:${v}`).join(';');
function paint(el,b){if(el)Object.entries(b.palette).forEach(([k,v])=>el.style.setProperty('--bot-'+k,v));}

/* The single page registry — every page's header nav renders from this
   (emitShell on shell pages, [data-page-nav] hydration elsewhere). */
const SHELL_PAGES = [
  ['chat', 'Trò chuyện', '../chat/index.html'],
  ['memories', 'Nhật ký', '../memories/index.html'],
  ['profile', 'Hồ sơ', '../profile/index.html'],
  ['provider', 'Provider', '../provider/index.html'],
  ['dashboard', 'Sử dụng', '../dashboard/index.html'],
  ['trace', 'Trace', '../trace/index.html'],
  ['settings', 'Cài đặt', '../settings/index.html'],
];
const pageLinksMarkup = current => SHELL_PAGES.map(([id, label, href]) =>
  `<a href="${href}" data-page-link="${id}"${id === current ? ' class="is-current" aria-current="page"' : ''}>${label}</a>`).join('');

/* Pages with static markup (chat) carry an empty
   <nav class="page-links" data-page-nav="<id>"> — fill it the moment the
   registry exists; emitShell pages embed the links already. */
const pageNavEl = document.querySelector('[data-page-nav]');
if (pageNavEl) pageNavEl.innerHTML = pageLinksMarkup(pageNavEl.dataset.pageNav);
