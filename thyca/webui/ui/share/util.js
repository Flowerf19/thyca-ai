'use strict';
const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const dots = '<span class="dots" aria-hidden="true"><i></i><i></i><i></i></span>';
const escapeHTML = str => String(str).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));

function hexToRgb(hex) {
  const clean = String(hex || '#9179ec').replace('#', '').trim();
  const full = clean.length === 3 ? clean.replace(/(.)/g, '$1$1') : clean;
  const parts = full.match(/[a-f\d]{2}/gi) || ['91', '79', 'ec'];
  return parts.map((x) => parseInt(x, 16));
}

function rgbToHex(r, g, b) {
  return '#' + [r, g, b].map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
}

function mix(hex, target, amount) {
  const a = hexToRgb(hex);
  const b = hexToRgb(target);
  return rgbToHex(a[0] + (b[0] - a[0]) * amount, a[1] + (b[1] - a[1]) * amount, a[2] + (b[2] - a[2]) * amount);
}

function relLuminance(hex) {
  const [r, g, b] = hexToRgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrastRatioHex(h1, h2) {
  const l1 = relLuminance(h1);
  const l2 = relLuminance(h2);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

function ensureContrast(startHex, bgA, bgB, minRatio, targetHex) {
  for (let i = 0; i <= 40; i++) {
    const c = mix(startHex, targetHex, i / 40);
    if (contrastRatioHex(c, bgA) >= minRatio && contrastRatioHex(c, bgB) >= minRatio) {
      return c;
    }
  }
  return targetHex;
}

function isDarkMode(overrideDark) {
  if (typeof overrideDark === 'boolean') return overrideDark;
  return typeof document !== 'undefined' && document.documentElement?.dataset?.theme === 'dark';
}

function derivePalette(color, isDark) {
  const darkTheme = isDarkMode(isDark);
  const normalized = rgbToHex(...hexToRgb(color));
  if (darkTheme) {
    const pale = mix(normalized, '#24201b', 0.86);
    const paper = mix(normalized, '#2e2922', 0.78);
    const ink = ensureContrast(mix(normalized, '#f4ebd8', 0.74), pale, paper, 7.0, '#f4ebd8');
    const muted = ensureContrast(mix(normalized, '#b8ab95', 0.76), pale, paper, 4.7, '#d0c4ae');
    const mouth = ensureContrast(mix(normalized, '#140f1c', 0.56), normalized, '#ffffff', 3.4, '#1b1824');
    const darkHex = mix(normalized, '#0e0d12', 0.56);
    return {
      color: normalized,
      ink,
      dark: darkHex,
      highlight: mix(normalized, '#f5ecd9', 0.25),
      mid: mix(normalized, '#594e3f', 0.45),
      border: mix(normalized, '#473e33', 0.62),
      rule: mix(normalized, '#3b342b', 0.74),
      paper,
      pale,
      sheet: mix(normalized, '#1f1c17', 0.90),
      stamp: mix(normalized, '#4a4032', 0.68),
      mouth,
      muted,
      eye: '#fffdf7',
      'eye-edge': darkHex,
      pupil: darkHex,
      inkContrast: contrastRatioHex(ink, pale),
      mutedContrast: contrastRatioHex(muted, pale),
    };
  }
  const pale = mix(normalized, '#fcf6e8', 0.90);
  const paper = mix(normalized, '#f8f0dc', 0.80);
  const ink = ensureContrast(mix(normalized, '#171510', 0.45), pale, paper, 7.0, '#171510');
  const muted = ensureContrast(mix(normalized, '#5c5243', 0.68), pale, paper, 4.7, '#2d261c');
  const mouth = ensureContrast(mix(normalized, '#140f1c', 0.56), normalized, '#ffffff', 3.4, '#1b1824');
  const darkHex = mix(normalized, '#14171a', 0.48);
  return {
    color: normalized,
    ink,
    dark: darkHex,
    highlight: mix(normalized, '#f7ebc8', 0.33),
    mid: mix(normalized, '#d3c9a5', 0.34),
    border: mix(normalized, '#dfd2b8', 0.60),
    rule: mix(normalized, '#f2e8d4', 0.78),
    paper,
    pale,
    sheet: mix(normalized, '#fff8e9', 0.96),
    stamp: mix(normalized, '#ebd29a', 0.72),
    mouth,
    muted,
    eye: '#fffdf7',
    'eye-edge': darkHex,
    pupil: darkHex,
    inkContrast: contrastRatioHex(ink, pale),
    mutedContrast: contrastRatioHex(muted, pale),
  };
}

const paletteStyle = (b) => Object.entries(b.palette)
  .filter(([, v]) => typeof v === 'string' && v.startsWith('#'))
  .map(([k, v]) => `--bot-${k}:${v}`)
  .join(';');

function paint(el, b) {
  if (!el || !b?.palette) return;
  Object.entries(b.palette).forEach(([k, v]) => {
    if (typeof v === 'string' && v.startsWith('#')) el.style.setProperty('--bot-' + k, v);
  });
}

const SHELL_PAGES = [
  ['chat', 'Trò chuyện', '../chat/index.html', 'nav-chat'],
  ['memories', 'Nhật ký', '../memories/index.html', 'nav-memories'],
  ['profile', 'Hồ sơ', '../profile/index.html', 'nav-profile'],
  ['provider', 'Provider', '../provider/index.html', 'nav-provider'],
  ['dashboard', 'Sử dụng', '../dashboard/index.html', 'nav-dashboard'],
];
const pageLinksMarkup = current => SHELL_PAGES.map(([id, label, href, sym]) =>
  `<a href="${href}" data-page-link="${id}" title="${label}"${id === current ? ' class="is-current" aria-current="page"' : ''}><svg class="icon" aria-hidden="true"><use href="#i-${sym}"/></svg><span class="nav-label">${label}</span></a>`).join('');

$$('[data-page-nav]').forEach(el => { el.innerHTML = pageLinksMarkup(el.dataset.pageNav); });
