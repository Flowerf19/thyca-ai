'use strict';
const states = {
  idle: ['Nghỉ', 'Sẵn sàng', 'Thở nhẹ · chớp mắt'],
  waiting: ['Đang chờ', 'Chờ bạn', 'Nhìn xuống · nhô thấp'],
  thinking: ['Đang nghĩ', 'Đang suy nghĩ', 'Nghiêng đầu · mắt nhìn lên'],
  writing: ['Đang viết', 'Đang viết', 'Ghi chép theo nhịp chữ'],
  working: ['Chạy việc', 'Đang làm việc', 'Đạo cụ nhún nhịp · xoay viền'],
  done: ['Xong', 'Đã hoàn tất', 'Mắt cười ^ ^ · sao lóe sáng ✦'],
  error: ['Lỗi', 'Có lỗi xảy ra', 'Mắt X · lắc nhẹ, viền đỏ'],
  offline: ['Offline', 'Mất kết nối', 'Nhắm mắt · đứng yên'],
  mention: ['Được gọi @', 'Bạn gọi mình?', 'Giật mình trong 0,5 giây'],
  confused: ['Bối rối', 'Cần làm rõ', 'Nghiêng đầu · dấu hỏi'],
  group: ['Phối hợp', 'Đang phối hợp', 'Cùng nhịp · sáng khi nói'],
};
const STATES = states;
const primaryStates = ['idle', 'waiting', 'thinking', 'writing', 'working', 'done', 'error', 'offline'];
const extraStates = ['mention', 'confused', 'group'];
const stateAliases = {
  listening: 'waiting',
  resting: 'idle',
};
const internalStateAliases = {
  ...stateAliases,
  running: 'working',
  busy: 'working',
  streaming: 'writing',
  pause: 'waiting',
  paused: 'waiting',
  stuck: 'confused',
  stuck_retry: 'error',
  clarify: 'confused',
  ask_user: 'confused',
  private_followup: 'confused',
  disconnected: 'offline',
};
const stateSummaries = {
  idle: 'Sẵn sàng khi bạn cần',
  thinking: 'Đang suy nghĩ câu trả lời…',
  working: 'Đang xử lý công cụ…',
  writing: 'Đang viết phản hồi…',
  waiting: 'Đang chờ bước kế tiếp…',
  done: 'Hoàn tất lượt này',
  error: 'Gặp sự cố khi chạy',
  confused: 'Cần bạn làm rõ thêm',
  mention: 'Vừa được gọi tên',
  offline: 'Chưa kết nối',
  group: 'Đang trao đổi trong nhóm',
};
const stateDetails = {
  idle: 'Thở nhẹ, chớp mắt và thi thoảng đảo nhìn xung quanh.',
  thinking: 'Nghiêng đầu, mắt hướng lên khi sắp xếp ý tưởng.',
  working: 'Nhịp nảy năng động cùng vòng xoay tiến độ.',
  writing: 'Mắt đọc theo từng nhịp chữ gửi ra.',
  waiting: 'Tạm dừng nhìn xuống chờ tín hiệu kế tiếp.',
  done: 'Mắt cong vui vẻ và sao nhỏ lóe sáng.',
  error: 'Mắt X báo sự cố và viền đỏ cần kiểm tra.',
  confused: 'Nghiêng nhẹ cùng dấu hỏi chờ bạn chọn hướng.',
  mention: 'Phản xạ bật nhẹ khi được nhắc @tên.',
  offline: 'Tạm nghỉ với hai đường mắt khép lại.',
  group: 'Phối hợp nhịp nhàng cùng các thành viên trong nhóm.',
};
const orbitDurations = { thinking: 8000, working: 1500, writing: 4500 };
const ALL_THEMES = ['starlight', 'sunrise', 'atelier', 'bauhaus', 'horizon', 'recital', 'classic'];
const THEME_FAMILIES = [
  { id: 'starlight', name: 'Đêm trăng', desc: 'Trăng kem & đồi tím', iconPath: '<path d="M16 4a7 7 0 1 0 4 12.7A6 6 0 0 1 16 4Z"/>' },
  { id: 'sunrise', name: 'Nắng ban mai', desc: 'Mặt trời đất & mây', iconPath: '<circle cx="12" cy="13" r="4"/><path d="M12 4v2M5 13H3M21 13h-2M6.5 7.5 5 6M17.5 7.5 19 6M4 18h16"/>' },
  { id: 'atelier', name: 'Thư phòng', desc: 'Mái vòm & giá sách', iconPath: '<path d="M4 19V7a2 2 0 0 1 2-2h5v14H6a2 2 0 0 1-2-2Zm16 0V7a2 2 0 0 0-2-2h-5v14h5a2 2 0 0 0 2-2Z"/>' },
  { id: 'bauhaus', name: 'Bauhaus', desc: 'Khối màu kiến trúc', iconPath: '<rect x="4" y="4" width="7" height="7" rx="1"/><circle cx="16.5" cy="7.5" r="3.5"/><polygon points="12 13 18 20 6 20"/>' },
  { id: 'horizon', name: 'Chân trời', desc: 'Sóng biển & la bàn', iconPath: '<path d="M3 16c2.5-2 4.5-2 7 0s4.5 2 7 0 2.5-1.5 4 0M3 20c2.5-2 4.5-2 7 0s4.5 2 7 0"/><circle cx="12" cy="8" r="3"/>' },
  { id: 'recital', name: 'Đĩa than', desc: 'Sân khấu & giai điệu', iconPath: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/>' },
  { id: 'classic', name: 'Giấy mộc', desc: 'Nền kem tối giản', iconPath: '<rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 9h6M9 13h6"/>' },
];
const THEME_IDS = THEME_FAMILIES.map(f => f.id);
const FAMILY_BY_ID = Object.fromEntries(THEME_FAMILIES.map(f => [f.id, f]));
const THEME_META = Object.fromEntries(THEME_FAMILIES.map(f => [f.id, { label: f.name, family: f.id }]));
const CREW = [
  {
    id: 'pillar',
    name: 'Pillar',
    shortLabel: 'Trụ cao 2 râu',
    vibrantHex: '#9179ec',
    paperHex: '#7d68b8',
    defaultTheme: 'starlight',
    flowerAnchor: [81, 41, 18],
    eyes: [[53.4, 63.3, 9.8], [74.8, 58.7, 11.1]],
    mouth: { kind: 'tooth', cx: 62.5, cy: 83.8, rx: 7.0, ry: 5.7 },
    bodySvg: () => `
      <line x1="51" y1="33.3" x2="42.8" y2="19.4" stroke="var(--bot-color)" stroke-width="6.6" stroke-linecap="round"/>
      <line x1="64.1" y1="30.9" x2="62.5" y2="12" stroke="var(--bot-color)" stroke-width="6.6" stroke-linecap="round"/>
      <path class="char-body-shape crew-body" d="M31.3 57.1 Q31.3 28.4 60 28.4 Q88.7 28.4 88.7 57.1 V135 H31.3 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `
      <line class="away-silhouette-ant" x1="51" y1="31" x2="42.8" y2="19.4"/>
      <line class="away-silhouette-ant" x1="64.1" y1="29" x2="62.5" y2="12"/>
      <path class="away-silhouette-shape crew-silhouette" d="M31.3 57.1 Q31.3 28.4 60 28.4 Q88.7 28.4 88.7 57.1 V122 H31.3 Z"/>`,
  },
  {
    id: 'dome',
    name: 'Dome',
    shortLabel: 'Vòm tròn rộng',
    vibrantHex: '#f2ba3a',
    paperHex: '#cf9a34',
    defaultTheme: 'sunrise',
    flowerAnchor: [84, 34, 20],
    eyes: [[49.4, 56.2, 10.3], [72.2, 51.6, 11.0]],
    mouth: { kind: 'smile', cx: 60.8, cy: 76.2, d: 'M54.7 74.4 Q60.8 80.2 66.8 74.4' },
    bodySvg: () => `<path class="char-body-shape crew-body" d="M17.4 128.4 V71.4 Q17.4 22 60 22 Q102.6 22 102.6 71.4 V128.4 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M17.4 122 V71.4 Q17.4 22 60 22 Q102.6 22 102.6 71.4 V122 Z"/>`,
  },
  {
    id: 'cloud',
    name: 'Cloud',
    shortLabel: 'Đám mây bồng bềnh',
    vibrantHex: '#ee6574',
    paperHex: '#c85c66',
    defaultTheme: 'atelier',
    flowerAnchor: [48, 45, -14],
    eyes: [[51.5, 67.0, 10.2], [74.8, 62.6, 11.1]],
    mouth: { kind: 'tooth', cx: 64.0, cy: 88.2, rx: 7.2, ry: 5.9 },
    bodySvg: () => `<path class="char-body-shape crew-body" d="M23.5 101.8 C12 94.1 11 74.9 23.5 65.3 C24.5 50 38.9 41.3 52.3 47.1 C61 33.6 82.1 35.6 89.8 50 C103.2 52.8 109.9 69.2 102.2 83.6 C106.1 95.1 96.5 107.6 81.1 109.5 C61.9 112.4 37 109.5 23.5 101.8 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M23.5 101.8 C12 94.1 11 74.9 23.5 65.3 C24.5 50 38.9 41.3 52.3 47.1 C61 33.6 82.1 35.6 89.8 50 C103.2 52.8 109.9 69.2 102.2 83.6 C106.1 95.1 96.5 107.6 81.1 109.5 C61.9 112.4 37 109.5 23.5 101.8 Z"/>`,
  },
  {
    id: 'puff',
    name: 'Puff',
    shortLabel: 'Bông xù có chân',
    vibrantHex: '#45bfa8',
    paperHex: '#3e9c8a',
    defaultTheme: 'bauhaus',
    flowerAnchor: [81, 41, 16],
    eyes: [[49.9, 60.2, 10.6], [72.8, 55.8, 11.4]],
    mouth: { kind: 'tooth', cx: 63.1, cy: 82.2, rx: 7.5, ry: 6.2 },
    bodySvg: () => `
      <path class="char-body-shape crew-body" d="M44.6 120 V102 A6.6 6.6 0 0 1 57.8 102 V120 Z" fill="var(--bot-color)"/>
      <path class="char-body-shape crew-body" d="M66.6 120 V102 A6.6 6.6 0 0 1 79.8 102 V120 Z" fill="var(--bot-color)"/>
      <g class="char-body-shape crew-body" fill="var(--bot-color)">
        <circle cx="37.6" cy="61" r="20.2"/>
        <circle cx="58.7" cy="46.1" r="22"/>
        <circle cx="81.6" cy="56.6" r="20.2"/>
        <circle cx="86.8" cy="79.5" r="18.5"/>
        <circle cx="61.3" cy="83.9" r="24.6"/>
        <circle cx="33.2" cy="82.2" r="18.5"/>
        <circle cx="60.9" cy="69" r="24.2"/>
      </g>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M36 100 C16 100 12 80 20 68 C14 52 28 38 42 41 C46 23 72 22 79 39 C95 36 106 52 99 67 C109 78 102 100 83 99 L80 100 V119 H67 V107 Q62 109 58 107 V119 H45 V100 Z"/>`,
  },
  {
    id: 'orb',
    name: 'Orb',
    shortLabel: 'Khối tròn 2 râu',
    vibrantHex: '#4591eb',
    paperHex: '#447db5',
    defaultTheme: 'horizon',
    flowerAnchor: [84, 47, 22],
    eyes: [[48.7, 64.5, 10.3], [72.2, 59.8, 11.3]],
    mouth: { kind: 'tooth', cx: 62.8, cy: 89.0, rx: 7.0, ry: 6.1 },
    bodySvg: () => `
      <line x1="49.7" y1="38.2" x2="42.1" y2="21.3" stroke="var(--bot-color)" stroke-width="6.8" stroke-linecap="round"/>
      <line x1="66.6" y1="37.3" x2="71.3" y2="19.4" stroke="var(--bot-color)" stroke-width="6.8" stroke-linecap="round"/>
      <path class="char-body-shape crew-body" d="M45 120 V100.3 A6.6 6.6 0 0 1 58.1 100.3 V120 Z" fill="var(--bot-color)"/>
      <path class="char-body-shape crew-body" d="M63.8 120 V100.3 A6.6 6.6 0 0 1 76.9 100.3 V120 Z" fill="var(--bot-color)"/>
      <circle class="char-body-shape crew-body" cx="60" cy="71.1" r="35.7" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `
      <line class="away-silhouette-ant" x1="49.7" y1="36" x2="42.1" y2="21.3"/>
      <line class="away-silhouette-ant" x1="66.6" y1="35" x2="71.3" y2="19.4"/>
      <path class="away-silhouette-shape crew-silhouette" d="M45 103.5 A35.7 35.7 0 1 1 76.9 102.5 V119 H63.8 V106.6 Q60.9 107.2 58.1 106.6 V119 H45 Z"/>`,
  },
  {
    id: 'arch',
    name: 'Arch',
    shortLabel: 'Vòm mềm chân vòm',
    vibrantHex: '#ee8dc0',
    paperHex: '#c26c97',
    defaultTheme: 'recital',
    flowerAnchor: [81, 40, 18],
    eyes: [[49.7, 63.6, 11.3], [76.0, 58.9, 12.2]],
    mouth: { kind: 'tooth', cx: 63.8, cy: 86.2, rx: 8.0, ry: 6.6 },
    bodySvg: () => `<path class="char-body-shape crew-body" d="M15.8 120 V67.4 Q15.8 28.8 55.3 28.8 Q93.8 28.8 98.5 71.1 L104.2 120 H73.2 Q73.2 105 62.8 105 Q52.5 105 52.5 120 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M15.8 119 V67.4 Q15.8 28.8 55.3 28.8 Q93.8 28.8 98.5 71.1 L104.2 119 H73.2 Q73.2 105 62.8 105 Q52.5 105 52.5 119 Z"/>`,
  },
  {
    id: 'drop',
    name: 'Drop',
    shortLabel: 'Giọt mềm',
    vibrantHex: '#e68646',
    paperHex: '#c4753d',
    defaultTheme: 'sunrise',
    flowerAnchor: [77, 47, 22],
    eyes: [[49.2, 71.5, 9.8], [71.5, 68.8, 10.7]],
    mouth: { kind: 'tooth', cx: 61.0, cy: 91.5, rx: 6.8, ry: 5.6 },
    bodySvg: () => `<path class="char-body-shape crew-body" d="M60 18 C72 34 94 56 93 81 C92 104 78 115 60 115 C42 115 28 104 27 81 C26 56 48 34 60 18 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M60 18 C72 34 94 56 93 81 C92 104 78 115 60 115 C42 115 28 104 27 81 C26 56 48 34 60 18 Z"/>`,
  },
  {
    id: 'squircle',
    name: 'Squircle',
    shortLabel: 'Vuông bo nghiêng',
    vibrantHex: '#d4735a',
    paperHex: '#a65a42',
    defaultTheme: 'atelier',
    flowerAnchor: [83, 35, 16],
    eyes: [[48.5, 63.0, 10.2], [72.0, 59.5, 11.2]],
    mouth: { kind: 'tooth', cx: 61.2, cy: 83.5, rx: 7.4, ry: 6.0 },
    bodySvg: () => `<rect class="char-body-shape crew-body" x="24" y="30" width="72" height="84" rx="22" transform="rotate(-5 60 72)" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<rect class="away-silhouette-shape crew-silhouette" x="24" y="30" width="72" height="84" rx="22" transform="rotate(-5 60 72)"/>`,
  },
  {
    id: 'pup',
    name: 'Pup',
    shortLabel: 'Tròn tai cụp',
    vibrantHex: '#9c7352',
    paperHex: '#7d5b40',
    defaultTheme: 'horizon',
    flowerAnchor: [78, 41, 18],
    eyes: [[48.8, 67.5, 10.2], [72.2, 64.0, 11.1]],
    mouth: { kind: 'smile', cx: 60.5, cy: 86.2, d: 'M53.8 84.6 Q60.5 90.4 67.2 84.6' },
    bodySvg: () => `
      <ellipse class="char-body-shape crew-body" cx="25" cy="64" rx="10" ry="20" transform="rotate(20 25 64)" fill="var(--bot-dark)"/>
      <ellipse class="char-body-shape crew-body" cx="95" cy="64" rx="10" ry="20" transform="rotate(-20 95 64)" fill="var(--bot-dark)"/>
      <circle class="char-body-shape crew-body" cx="60" cy="72" r="36" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `
      <ellipse class="away-silhouette-shape crew-silhouette" cx="25" cy="64" rx="10" ry="20" transform="rotate(20 25 64)"/>
      <ellipse class="away-silhouette-shape crew-silhouette" cx="95" cy="64" rx="10" ry="20" transform="rotate(-20 95 64)"/>
      <circle class="away-silhouette-shape crew-silhouette" cx="60" cy="72" r="36"/>`,
  },
  {
    id: 'cone',
    name: 'Cone',
    shortLabel: 'Chóp tam giác bo',
    vibrantHex: '#8d72b8',
    paperHex: '#765898',
    defaultTheme: 'starlight',
    flowerAnchor: [75, 43, 20],
    eyes: [[49.5, 66.5, 9.6], [71.2, 64.0, 10.4]],
    mouth: { kind: 'tooth', cx: 60.8, cy: 86.5, rx: 6.8, ry: 5.6 },
    bodySvg: () => `<path class="char-body-shape crew-body" d="M51 22 Q60 12 69 22 L100 104 Q104 116 90 116 H30 Q16 116 20 104 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M51 22 Q60 12 69 22 L100 104 Q104 116 90 116 H30 Q16 116 20 104 Z"/>`,
  },
  {
    id: 'star',
    name: 'Star',
    shortLabel: 'Sao bo 5 cánh',
    vibrantHex: '#f0b842',
    paperHex: '#c8922f',
    defaultTheme: 'recital',
    flowerAnchor: [80, 50, 20],
    eyes: [[49.2, 67.5, 9.6], [71.4, 65.0, 10.5]],
    mouth: { kind: 'smile', cx: 60.5, cy: 85.2, d: 'M54.2 83.6 Q60.5 89.2 66.8 83.6' },
    bodySvg: () => `<path class="char-body-shape crew-body" d="M51.5 38.3 Q60 28 68.5 38.3 Q77 48.5 89.4 53.5 Q101.8 58.4 94.7 69.7 Q87.6 81 86.8 94.3 Q85.9 107.6 73 104.3 Q60 101 47 104.3 Q34.1 107.6 33.3 94.3 Q32.4 81 25.3 69.7 Q18.2 58.4 30.6 53.5 Q43 48.5 51.5 38.3 Z" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<path class="away-silhouette-shape crew-silhouette" d="M51.5 38.3 Q60 28 68.5 38.3 Q77 48.5 89.4 53.5 Q101.8 58.4 94.7 69.7 Q87.6 81 86.8 94.3 Q85.9 107.6 73 104.3 Q60 101 47 104.3 Q34.1 107.6 33.3 94.3 Q32.4 81 25.3 69.7 Q18.2 58.4 30.6 53.5 Q43 48.5 51.5 38.3 Z"/>`,
  },
  {
    id: 'capsule',
    name: 'Capsule',
    shortLabel: 'Viên nang tròn',
    vibrantHex: '#5ca879',
    paperHex: '#3f7d58',
    defaultTheme: 'bauhaus',
    flowerAnchor: [84, 46, 18],
    eyes: [[47.5, 67.5, 10.4], [72.5, 64.8, 11.3]],
    mouth: { kind: 'tooth', cx: 60.8, cy: 88.0, rx: 7.4, ry: 6.0 },
    bodySvg: () => `<rect class="char-body-shape crew-body" x="15" y="42" width="90" height="68" rx="34" transform="rotate(-4 60 76)" fill="var(--bot-color)"/>`,
    silhouetteSvg: () => `<rect class="away-silhouette-shape crew-silhouette" x="15" y="42" width="90" height="68" rx="34" transform="rotate(-4 60 76)"/>`,
  },
];
const CREW_IDS = CREW.map(c => c.id);
const CREW_BY_ID = Object.fromEntries(CREW.map(c => [c.id, c]));
const DEFAULT_CREW_BY_BOT = {
  thyca: 'pillar',
  march7th: 'dome',
  scribe: 'cloud',
  coder: 'puff',
  researcher: 'orb',
  designer: 'arch',
};
const DEFAULT_THEME_BY_BOT = {
  thyca: 'starlight',
  march7th: 'sunrise',
  scribe: 'atelier',
  coder: 'bauhaus',
  researcher: 'horizon',
  designer: 'recital',
};
const ACCESSORIES = [
  { id: 'none', name: 'Không' },
  { id: 'glasses', name: 'Kính tròn' },
  { id: 'shades', name: 'Kính râm' },
  { id: 'flower', name: 'Hoa cài' },
  { id: 'blush', name: 'Má hồng' },
  { id: 'bandaid', name: 'Sticker má' },
];
const ACCESSORY_IDS = ACCESSORIES.map(a => a.id);
const ACCESSORY_BY_ID = Object.fromEntries(ACCESSORIES.map(a => [a.id, a]));
const VIBRANT_SWATCHES = CREW.slice(0, 6).map(c => c.vibrantHex);
const PAPER_SWATCHES = CREW.slice(0, 6).map(c => c.paperHex);
const SWATCHES = [...VIBRANT_SWATCHES, ...PAPER_SWATCHES];
let crewUidCounter = 0;
function stableHash(str) {
  let h = 2166136261;
  const s = String(str || 'thyca');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
function isDarkMode() {
  return typeof document !== 'undefined' && document.documentElement?.dataset?.theme === 'dark';
}
function crewShapeFor(b) {
  if (b && b.shape && CREW_BY_ID[b.shape]) return CREW_BY_ID[b.shape];
  if (b && b.crew && CREW_BY_ID[b.crew]) return CREW_BY_ID[b.crew];
  const id = String(b?.id || b?.name || '');
  if (DEFAULT_CREW_BY_BOT[id]) return CREW_BY_ID[DEFAULT_CREW_BY_BOT[id]];
  return CREW[stableHash(id) % CREW.length];
}
function themeFor(b) {
  if (b && b.theme && FAMILY_BY_ID[b.theme]) return b.theme;
  const id = String(b?.id || b?.name || '');
  if (DEFAULT_THEME_BY_BOT[id]) return DEFAULT_THEME_BY_BOT[id];
  return ALL_THEMES[stableHash(id) % ALL_THEMES.length];
}
function themeLabel(themeId) {
  return (THEME_META[themeId] || THEME_META.classic).label;
}
function themeIcon(themeId) {
  const meta = THEME_META[themeId] || THEME_META.classic;
  const fam = FAMILY_BY_ID[meta.family] || THEME_FAMILIES[0];
  return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${fam.iconPath}</svg>`;
}
function accessoryFor(b) {
  if (b && b.accessory && ACCESSORY_BY_ID[b.accessory]) return b.accessory;
  return 'none';
}
function normalizeState(st) {
  if (!st) return 'idle';
  const raw = String(st).toLowerCase();
  const mapped = internalStateAliases[raw] || raw;
  return states[mapped] ? mapped : 'idle';
}
function headAccessorySvg(accId, crew) {
  if (accId === 'flower') {
    const [fx, fy, rot] = crew.flowerAnchor || [80, 40, 18];
    return `<g class="char-acc char-acc-head" transform="translate(${fx} ${fy}) rotate(${rot})"><g fill="#fff8ea" stroke="#c89e64" stroke-width="0.9"><circle cx="0" cy="-5.2" r="3.4"/><circle cx="5" cy="-1.6" r="3.4"/><circle cx="3.1" cy="4.2" r="3.4"/><circle cx="-3.1" cy="4.2" r="3.4"/><circle cx="-5" cy="-1.6" r="3.4"/></g><circle cx="0" cy="0" r="3.1" fill="#eab342"/></g>`;
  }
  return '';
}
function faceAccessorySvg(accId, crew) {
  if (!accId || accId === 'none') return '';
  const [e1, e2] = crew.eyes;
  const dx = e2[0] - e1[0];
  const dy = e2[1] - e1[1];
  const dist = Math.hypot(dx, dy) || 1;
  const ux = dx / dist;
  const uy = dy / dist;
  const angle = (Math.atan2(dy, dx) * 180 / Math.PI).toFixed(1);
  if (accId === 'glasses') {
    const r1 = e1[2] + 1.8;
    const r2 = e2[2] + 1.8;
    const bx1 = (e1[0] + ux * r1).toFixed(1);
    const by1 = (e1[1] + uy * r1).toFixed(1);
    const bx2 = (e2[0] - ux * r2).toFixed(1);
    const by2 = (e2[1] - uy * r2).toFixed(1);
    return `<g class="char-acc char-acc-face" fill="#fffdf624" stroke="#b98327" stroke-width="2.1" stroke-linecap="round"><circle cx="${e1[0]}" cy="${e1[1]}" r="${r1.toFixed(1)}"/><circle cx="${e2[0]}" cy="${e2[1]}" r="${r2.toFixed(1)}"/><line x1="${bx1}" y1="${by1}" x2="${bx2}" y2="${by2}"/></g>`;
  }
  if (accId === 'shades') {
    const mx = ((e1[0] + e2[0]) / 2).toFixed(1);
    const my = ((e1[1] + e2[1]) / 2).toFixed(1);
    const half = dist / 2;
    const topY = -(Math.max(e1[2], e2[2]) + 0.6);
    const xL = -(half + e1[2] + 1.5);
    const xR = half + e2[2] + 1.5;
    const b1 = e1[2] * 0.94;
    const b2 = e2[2] * 0.94;
    const gap = 1.9;
    const dLeft = `M${xL.toFixed(1)} ${(topY + 2.4).toFixed(1)} Q${xL.toFixed(1)} ${topY.toFixed(1)} ${(xL + 3.2).toFixed(1)} ${topY.toFixed(1)} H${(-gap - 1.6).toFixed(1)} Q${(-gap).toFixed(1)} ${topY.toFixed(1)} ${(-gap).toFixed(1)} ${(topY + 2.6).toFixed(1)} V${(b1 - 5.2).toFixed(1)} Q${(-gap).toFixed(1)} ${b1.toFixed(1)} ${(-gap - 6.2).toFixed(1)} ${b1.toFixed(1)} H${(xL + 6.4).toFixed(1)} Q${xL.toFixed(1)} ${b1.toFixed(1)} ${xL.toFixed(1)} ${(b1 - 5.4).toFixed(1)} Z`;
    const dRight = `M${gap.toFixed(1)} ${(topY + 2.6).toFixed(1)} Q${gap.toFixed(1)} ${topY.toFixed(1)} ${(gap + 1.6).toFixed(1)} ${topY.toFixed(1)} H${(xR - 3.2).toFixed(1)} Q${xR.toFixed(1)} ${topY.toFixed(1)} ${xR.toFixed(1)} ${(topY + 2.4).toFixed(1)} V${(b2 - 5.4).toFixed(1)} Q${xR.toFixed(1)} ${b2.toFixed(1)} ${(xR - 6.4).toFixed(1)} ${b2.toFixed(1)} H${(gap + 6.2).toFixed(1)} Q${gap.toFixed(1)} ${b2.toFixed(1)} ${gap.toFixed(1)} ${(b2 - 5.2).toFixed(1)} Z`;
    const s1x = (-half - 3.8).toFixed(1);
    const s2x = (half - 3.6).toFixed(1);
    const sy1 = (topY + 3.2).toFixed(1);
    const sy2 = (topY + 8.6).toFixed(1);
    return `<g class="char-acc char-acc-face" transform="translate(${mx} ${my}) rotate(${angle})"><line x1="${(xL - 1.1).toFixed(1)}" y1="${(topY + 2.2).toFixed(1)}" x2="${(xR + 1.1).toFixed(1)}" y2="${(topY + 2.2).toFixed(1)}" stroke="#16141f" stroke-width="2.4" stroke-linecap="round"/><path d="${dLeft}" fill="#1f1c29" fill-opacity="0.88" stroke="#16141f" stroke-width="1.8" stroke-linejoin="round"/><path d="${dRight}" fill="#1f1c29" fill-opacity="0.88" stroke="#16141f" stroke-width="1.8" stroke-linejoin="round"/><line x1="${s1x}" y1="${sy2}" x2="${(-half + 1.2).toFixed(1)}" y2="${sy1}" stroke="#ffffff" stroke-opacity="0.46" stroke-width="1.7" stroke-linecap="round"/><line x1="${s2x}" y1="${sy2}" x2="${(half + 1.4).toFixed(1)}" y2="${sy1}" stroke="#ffffff" stroke-opacity="0.46" stroke-width="1.7" stroke-linecap="round"/></g>`;
  }
  if (accId === 'blush') {
    const lx = (e1[0] - e1[2] * 0.42).toFixed(1);
    const ly = (e1[1] + e1[2] * 1.18).toFixed(1);
    const rx = (e2[0] + e2[2] * 0.34).toFixed(1);
    const ry = (e2[1] + e2[2] * 1.16).toFixed(1);
    return `<g class="char-acc char-acc-face" fill="#df4b62" fill-opacity="0.38"><ellipse cx="${lx}" cy="${ly}" rx="4.8" ry="2.6" transform="rotate(${angle} ${lx} ${ly})"/><ellipse cx="${rx}" cy="${ry}" rx="4.8" ry="2.6" transform="rotate(${angle} ${rx} ${ry})"/></g>`;
  }
  if (accId === 'bandaid') {
    const bx = (e2[0] + e2[2] * 0.14).toFixed(1);
    const by = (e2[1] + e2[2] * 1.24).toFixed(1);
    return `<g class="char-acc char-acc-face" transform="translate(${bx} ${by}) rotate(-14)"><rect x="-6.2" y="-2.7" width="12.4" height="5.4" rx="2.5" fill="#f9ecd4" stroke="#bfa074" stroke-width="1"/><rect x="-2.1" y="-2.1" width="4.2" height="4.2" rx="0.8" fill="#ecd5ae"/><circle cx="-4.1" cy="0" r="0.6" fill="#bfa074"/><circle cx="4.1" cy="0" r="0.6" fill="#bfa074"/></g>`;
  }
  return '';
}
function stageSceneSvg(themeId, isDark) {
  if (themeId === 'classic') {
    return `<g class="theme-scene-art"><rect x="0" y="69" width="188" height="9" fill="${isDark ? '#383129' : '#ebdfc9'}"/></g>`;
  }
  if (themeId === 'starlight') {
    return `<g class="theme-scene-art"><circle cx="31" cy="24" r="15" fill="${isDark ? '#54487a' : '#63578a'}" opacity="0.34"/><path d="M31.5 13.5 A10.8 10.8 0 1 0 41.2 29.5 A8.5 8.5 0 1 1 31.5 13.5 Z" fill="#f6e6be"/><path d="M148 16l2 5.2 5.2 2-5.2 2-2 5.2-2-5.2-5.2-2 5.2-2Z" fill="#f7e5b0"/><path d="M168 31l1.2 3.2 3.2 1.2-3.2 1.2-1.2 3.2-1.2-3.2-3.2-1.2 3.2-1.2Z" fill="#dfd7fa" opacity="0.88"/><circle cx="136" cy="28" r="1.8" fill="#f7e5b0" opacity="0.75"/><circle cx="54" cy="18" r="1.5" fill="#dfd7fa" opacity="0.65"/><path d="M0 57 Q42 42 92 56 T188 53 V78 H0 Z" fill="${isDark ? '#2c2544' : '#3f365e'}"/><path d="M0 66 Q58 55 116 67 T188 63 V78 H0 Z" fill="${isDark ? '#1d1830' : '#2c2646'}"/></g>`;
  }
  if (themeId === 'sunrise') {
    if (isDark) {
      return `<g class="theme-scene-art"><circle cx="32" cy="27" r="18" fill="#b86b3e" opacity="0.36"/><circle cx="32" cy="27" r="12" fill="#e08952"/><g fill="#c99e76" opacity="0.5"><circle cx="146" cy="25" r="8"/><circle cx="157" cy="21" r="10.5"/><circle cx="169" cy="25" r="8"/><rect x="142" y="25" width="31" height="8" rx="4"/></g><path d="M0 59 Q48 45 98 58 T188 54 V78 H0 Z" fill="#523526"/><path d="M0 68 Q62 56 128 68 T188 64 V78 H0 Z" fill="#3a251b"/></g>`;
    }
    return `<g class="theme-scene-art"><circle cx="32" cy="27" r="18" fill="#f6cb9c" opacity="0.65"/><circle cx="32" cy="27" r="12" fill="#e68954"/><g fill="#fffbf2"><circle cx="146" cy="25" r="8"/><circle cx="157" cy="21" r="10.5"/><circle cx="169" cy="25" r="8"/><rect x="142" y="25" width="31" height="8" rx="4"/></g><path d="M0 59 Q48 45 98 58 T188 54 V78 H0 Z" fill="#efcba0"/><path d="M0 68 Q62 56 128 68 T188 64 V78 H0 Z" fill="#dfa979"/></g>`;
  }
  if (themeId === 'atelier') {
    if (isDark) {
      return `<g class="theme-scene-art"><path d="M44 74 V38 Q44 6 94 6 Q144 6 144 38 V74 Z" fill="#3c2f24"/><path d="M48 74 V39 Q48 11 94 11 Q140 11 140 39 V74 Z" fill="none" stroke="#5b4736" stroke-width="1.4"/><g><rect x="8" y="34" width="8" height="36" rx="1.5" fill="#9c6a4e"/><rect x="18" y="42" width="7" height="28" rx="1.5" fill="#5b755b"/><rect x="27" y="28" width="9" height="42" rx="1.5" fill="#b38d57"/><rect x="152" y="36" width="8" height="34" rx="1.5" fill="#5b755b"/><rect x="162" y="28" width="9" height="42" rx="1.5" fill="#9e5e50"/><rect x="173" y="42" width="7" height="28" rx="1.5" fill="#b38d57"/></g><circle cx="161" cy="20" r="13" fill="#e5b05c" opacity="0.16"/><path d="M161 0v11" stroke="#8c7253" stroke-width="1.6"/><path d="M153 16a8 6 0 0 1 16 0Z" fill="#dfa550"/><rect x="0" y="69" width="188" height="9" fill="#463628"/><rect x="0" y="69" width="188" height="2" fill="#5e4937"/></g>`;
    }
    return `<g class="theme-scene-art"><path d="M44 74 V38 Q44 6 94 6 Q144 6 144 38 V74 Z" fill="#fffaf0"/><path d="M48 74 V39 Q48 11 94 11 Q140 11 140 39 V74 Z" fill="none" stroke="#e4cfb3" stroke-width="1.4"/><g><rect x="8" y="34" width="8" height="36" rx="1.5" fill="#c78d6a"/><rect x="18" y="42" width="7" height="28" rx="1.5" fill="#7c967a"/><rect x="27" y="28" width="9" height="42" rx="1.5" fill="#d9b277"/><rect x="152" y="36" width="8" height="34" rx="1.5" fill="#7c967a"/><rect x="162" y="28" width="9" height="42" rx="1.5" fill="#bf7b6a"/><rect x="173" y="42" width="7" height="28" rx="1.5" fill="#d9b277"/></g><path d="M161 0v11" stroke="#9e7f5a" stroke-width="1.6"/><path d="M153 16a8 6 0 0 1 16 0Z" fill="#d59a4e"/><rect x="0" y="69" width="188" height="9" fill="#dfc4a0"/><rect x="0" y="69" width="188" height="2" fill="#caa97f"/></g>`;
  }
  if (themeId === 'bauhaus') {
    if (isDark) {
      return `<g class="theme-scene-art"><path d="M0 71 V30 A34 34 0 0 1 34 64 V71 Z" fill="#b36545"/><rect x="10" y="12" width="18" height="18" rx="3" fill="#c49c4b"/><circle cx="38" cy="24" r="6.5" fill="#5e877a" opacity="0.88"/><rect x="150" y="20" width="28" height="51" rx="3" fill="#466158"/><rect x="138" y="42" width="24" height="29" rx="3" fill="#b89042"/><circle cx="164" cy="34" r="7" fill="#2a2722"/><path d="M156 62 L176 62 L166 46 Z" fill="#82ad9f" opacity="0.72"/><rect x="0" y="70" width="188" height="8" fill="#3a352e"/></g>`;
    }
    return `<g class="theme-scene-art"><path d="M0 71 V30 A34 34 0 0 1 34 64 V71 Z" fill="#dc8a63"/><rect x="10" y="12" width="18" height="18" rx="3" fill="#e5be67"/><circle cx="38" cy="24" r="6.5" fill="#769d91" opacity="0.8"/><rect x="150" y="20" width="28" height="51" rx="3" fill="#b4cec5"/><rect x="138" y="42" width="24" height="29" rx="3" fill="#e5be67"/><circle cx="164" cy="34" r="7" fill="#f7f2e6"/><path d="M156 62 L176 62 L166 46 Z" fill="#52766b" opacity="0.72"/><rect x="0" y="70" width="188" height="8" fill="#dfd5bf"/></g>`;
  }
  if (themeId === 'horizon') {
    if (isDark) {
      return `<g class="theme-scene-art"><circle cx="30" cy="26" r="14" fill="#c79752" opacity="0.3"/><circle cx="30" cy="26" r="9.5" fill="#e3b162"/><path d="M30 19 L32.2 26 L30 33 L27.8 26 Z" fill="#fff3d6" opacity="0.88"/><g fill="#7d95ab" opacity="0.45"><circle cx="148" cy="24" r="7.5"/><circle cx="159" cy="20" r="9.5"/><circle cx="170" cy="24" r="7"/><rect x="145" y="24" width="28" height="7" rx="3.5"/></g><path d="M0 57 Q24 49 48 57 T96 57 T144 57 T188 57 V78 H0 Z" fill="#364e66"/><path d="M0 66 Q24 59 48 66 T96 66 T144 66 T188 66 V78 H0 Z" fill="#25384b"/></g>`;
    }
    return `<g class="theme-scene-art"><circle cx="30" cy="26" r="14" fill="#f4d8a8" opacity="0.65"/><circle cx="30" cy="26" r="9.5" fill="#ebb76a"/><path d="M30 19 L32.2 26 L30 33 L27.8 26 Z" fill="#fffaf0" opacity="0.88"/><g fill="#fffdf8" opacity="0.9"><circle cx="148" cy="24" r="7.5"/><circle cx="159" cy="20" r="9.5"/><circle cx="170" cy="24" r="7"/><rect x="145" y="24" width="28" height="7" rx="3.5"/></g><path d="M0 57 Q24 49 48 57 T96 57 T144 57 T188 57 V78 H0 Z" fill="#bad2e3"/><path d="M0 66 Q24 59 48 66 T96 66 T144 66 T188 66 V78 H0 Z" fill="#8fb5d1"/></g>`;
  }
  if (themeId === 'recital') {
    if (isDark) {
      return `<g class="theme-scene-art"><path d="M0 0 H44 C36 22 24 38 0 46 Z" fill="#573445"/><path d="M0 0 H26 C20 16 12 28 0 34 Z" fill="#462837"/><g><circle cx="156" cy="48" r="24" fill="#18141c"/><circle cx="156" cy="48" r="18" fill="none" stroke="#42364a" stroke-width="1.2"/><circle cx="156" cy="48" r="12.5" fill="none" stroke="#42364a" stroke-width="1.1"/><circle cx="156" cy="48" r="7.5" fill="#e6bc7a"/><circle cx="156" cy="48" r="2.2" fill="#18141c"/><rect x="158" y="20" width="30" height="52" rx="3" fill="#4a3641"/><rect x="163" y="26" width="18" height="4" rx="2" fill="#c783a3"/><rect x="163" y="34" width="13" height="3" rx="1.5" fill="#8a6f7d"/></g><g fill="#de8eb3"><circle cx="24" cy="44" r="3.4"/><circle cx="36" cy="40" r="3.4"/><path d="M26.8 44 V29 L38.8 26 V40" fill="none" stroke="#de8eb3" stroke-width="2.2" stroke-linecap="round"/></g><rect x="0" y="70" width="188" height="8" fill="#472d39"/></g>`;
    }
    return `<g class="theme-scene-art"><path d="M0 0 H44 C36 22 24 38 0 46 Z" fill="#e9c4d3"/><path d="M0 0 H26 C20 16 12 28 0 34 Z" fill="#dca9be"/><g><circle cx="156" cy="48" r="24" fill="#3f3644"/><circle cx="156" cy="48" r="18" fill="none" stroke="#5b4e63" stroke-width="1.2"/><circle cx="156" cy="48" r="12.5" fill="none" stroke="#5b4e63" stroke-width="1.1"/><circle cx="156" cy="48" r="7.5" fill="#f0c888"/><circle cx="156" cy="48" r="2.2" fill="#3f3644"/><rect x="158" y="20" width="30" height="52" rx="3" fill="#fff8ef"/><rect x="163" y="26" width="18" height="4" rx="2" fill="#e3a6c0"/><rect x="163" y="34" width="13" height="3" rx="1.5" fill="#d9c6b4"/></g><g fill="#b85c87"><circle cx="24" cy="44" r="3.4"/><circle cx="36" cy="40" r="3.4"/><path d="M26.8 44 V29 L38.8 26 V40" fill="none" stroke="#b85c87" stroke-width="2.2" stroke-linecap="round"/></g><rect x="0" y="70" width="188" height="8" fill="#e8cbd4"/></g>`;
  }
  return '';
}
function circleSkySvg(themeId, isDark) {
  if (themeId === 'classic') return '';
  if (themeId === 'starlight') {
    return `<g class="theme-scene-art"><path d="M30 16.5 A10.5 10.5 0 1 0 39.5 32 A8.2 8.2 0 1 1 30 16.5 Z" fill="#f6e6be"/><path d="M89 20l1.8 4.6 4.6 1.8-4.6 1.8-1.8 4.6-1.8-4.6-4.6-1.8 4.6-1.8Z" fill="#f7e5b0"/><path d="M0 90 Q35 76 70 88 T120 84 V120 H0 Z" fill="${isDark ? '#1d1830' : '#2c2646'}"/></g>`;
  }
  if (themeId === 'sunrise') {
    return `<g class="theme-scene-art"><circle cx="28" cy="28" r="11.5" fill="${isDark ? '#e08952' : '#e68954'}"/><path d="M0 90 Q40 76 80 88 T120 84 V120 H0 Z" fill="${isDark ? '#3a251b' : '#dfa979'}"/></g>`;
  }
  if (themeId === 'atelier') {
    return `<g class="theme-scene-art"><path d="M22 114 V50 Q22 14 60 14 Q98 14 98 50 V114 Z" fill="${isDark ? '#3c2f24' : '#fffaf0'}"/><rect x="0" y="96" width="120" height="24" fill="${isDark ? '#463628' : '#dfc4a0'}"/></g>`;
  }
  if (themeId === 'bauhaus') {
    return `<g class="theme-scene-art"><path d="M0 98 V50 A34 34 0 0 1 34 84 V98 Z" fill="${isDark ? '#b36545' : '#dc8a63'}"/><rect x="84" y="20" width="22" height="22" rx="3" fill="${isDark ? '#c49c4b' : '#e5be67'}"/><rect x="0" y="96" width="120" height="24" fill="${isDark ? '#3a352e' : '#dfd5bf'}"/></g>`;
  }
  if (themeId === 'horizon') {
    return `<g class="theme-scene-art"><circle cx="29" cy="28" r="9.5" fill="${isDark ? '#e3b162' : '#ebb76a'}"/><path d="M0 86 Q28 76 56 86 T120 86 V120 H0 Z" fill="${isDark ? '#364e66' : '#bad2e3'}"/><path d="M0 96 Q28 88 56 96 T120 96 V120 H0 Z" fill="${isDark ? '#25384b' : '#8fb5d1'}"/></g>`;
  }
  if (themeId === 'recital') {
    return `<g class="theme-scene-art"><circle cx="94" cy="40" r="18" fill="${isDark ? '#18141c' : '#3f3644'}"/><circle cx="94" cy="40" r="6" fill="${isDark ? '#e6bc7a' : '#f0c888'}"/><rect x="0" y="96" width="120" height="24" fill="${isDark ? '#472d39' : '#e8cbd4'}"/></g>`;
  }
  return '';
}
function circleWellFill(themeId, uid, isDark) {
  const fills = isDark ? {
    starlight: `url(#${uid}-gal)`,
    sunrise: '#432e23',
    atelier: '#32261e',
    bauhaus: '#2b2823',
    horizon: '#23313f',
    recital: '#35222c',
    classic: '#2b2620',
  } : {
    starlight: `url(#${uid}-gal)`,
    sunrise: '#fbe9ca',
    atelier: '#f4e4cd',
    bauhaus: '#f1eadb',
    horizon: '#e7f0f7',
    recital: '#f8e9ed',
    classic: '#f7efe0',
  };
  return fills[themeId] || (isDark ? '#2b2620' : '#f7efe0');
}
function buildEyesAndMouth(crew, uid, accId = 'none') {
  const eyesMarkup = crew.eyes.map(([cx, cy, r], idx) => {
    const side = idx === 0 ? 'eye-left' : 'eye-right';
    const clipId = `${uid}-e${idx}`;
    const pr = (0.42 * r).toFixed(2);
    const hlX = (cx - 0.13 * r).toFixed(2);
    const hlY = (cy - 0.14 * r).toFixed(2);
    const hlR = (0.13 * r).toFixed(2);
    const lx1 = (cx - 0.64 * r).toFixed(2);
    const lx2 = (cx + 0.64 * r).toFixed(2);
    const ly = (cy + 0.1 * r).toFixed(2);
    const lq = (cy + 0.58 * r).toFixed(2);
    const hy = (cy + 0.18 * r).toFixed(2);
    const hq = (cy - 0.56 * r).toFixed(2);
    const ex = (0.36 * r).toFixed(2);
    return `<g class="eye-unit ${side}" data-eye-cx="${cx}" data-eye-cy="${cy}" data-eye-r="${r}"><clipPath id="${clipId}"><circle cx="${cx}" cy="${cy}" r="${r}"/></clipPath><circle class="eye-sclera" cx="${cx}" cy="${cy}" r="${r}" fill="#ffffff"/><g class="eye-pupil-group"><circle cx="${cx}" cy="${cy}" r="${pr}" fill="#171620"/><circle cx="${hlX}" cy="${hlY}" r="${hlR}" fill="#ffffff"/></g><g class="eye-expr eye-error" fill="none" stroke="#1b1824" stroke-width="2.6" stroke-linecap="round"><path d="M${(cx - ex).toFixed(1)} ${(cy - ex).toFixed(1)}L${(cx + +ex).toFixed(1)} ${(cy + +ex).toFixed(1)}"/><path d="M${(cx + +ex).toFixed(1)} ${(cy - ex).toFixed(1)}L${(cx - ex).toFixed(1)} ${(cy + +ex).toFixed(1)}"/></g><g clip-path="url(#${clipId})"><rect class="eye-lid" x="${(cx - r - 0.5).toFixed(1)}" y="${(cy - r - 0.5).toFixed(1)}" width="${(2 * r + 1).toFixed(1)}" height="${(2 * r + 1).toFixed(1)}" fill="var(--bot-color)"/></g><path class="eye-lash" d="M${lx1} ${ly} Q${cx} ${lq} ${lx2} ${ly}" fill="none" stroke="#1d1a26" stroke-width="2.1" stroke-linecap="round"/><path class="eye-expr eye-happy" d="M${lx1} ${hy} Q${cx} ${hq} ${lx2} ${hy}" fill="none" stroke="#1d1a26" stroke-width="2.3" stroke-linecap="round"/></g>`;
  }).join('');
  const m = crew.mouth;
  let normalMouth = '';
  if (m.kind === 'smile') {
    normalMouth = `<path class="mouth-normal" d="${m.d}" fill="none" stroke="#1d1a26" stroke-width="2.8" stroke-linecap="round"/>`;
  } else {
    const tx = (m.cx - m.rx * 0.36).toFixed(1);
    const ty = (m.cy - m.ry).toFixed(1);
    const tw = (m.rx * 0.68).toFixed(1);
    const th = (m.ry * 0.64).toFixed(1);
    normalMouth = `<g class="mouth-normal"><ellipse cx="${m.cx}" cy="${m.cy}" rx="${m.rx}" ry="${m.ry}" fill="var(--bot-mouth)"/><rect x="${tx}" y="${ty}" width="${tw}" height="${th}" rx="1.1" fill="#ffffff"/></g>`;
  }
  const altMouths = `<circle class="mouth-alt mouth-think" cx="${m.cx}" cy="${m.cy}" r="3.4" fill="var(--bot-mouth)"/><ellipse class="mouth-alt mouth-surprised" cx="${m.cx}" cy="${m.cy}" rx="4.4" ry="5.6" fill="var(--bot-mouth)"/><path class="mouth-alt mouth-sad" d="M${(m.cx - 5.8).toFixed(1)} ${(m.cy + 2.4).toFixed(1)} Q${m.cx} ${(m.cy - 3.6).toFixed(1)} ${(m.cx + 5.8).toFixed(1)} ${(m.cy + 2.4).toFixed(1)}" fill="none" stroke="#1d1a26" stroke-width="2.4" stroke-linecap="round"/><path class="mouth-alt mouth-confused" d="M${(m.cx - 5.4).toFixed(1)} ${(m.cy + 1.2).toFixed(1)} Q${(m.cx - 1.5).toFixed(1)} ${(m.cy - 1.8).toFixed(1)} ${(m.cx + 1.5).toFixed(1)} ${(m.cy + 1.2).toFixed(1)} T${(m.cx + 5.8).toFixed(1)} ${(m.cy - 0.8).toFixed(1)}" fill="none" stroke="#1d1a26" stroke-width="2.2" stroke-linecap="round"/>`;
  const faceCx = ((crew.eyes[0][0] + crew.eyes[1][0]) / 2).toFixed(1);
  const faceCy = ((crew.eyes[0][1] + crew.eyes[1][1]) / 2).toFixed(1);
  return `<g class="char-root">${crew.bodySvg()}${headAccessorySvg(accId, crew)}<g class="char-face-shake"><g class="char-face" data-face-cx="${faceCx}" data-face-cy="${faceCy}">${eyesMarkup}${normalMouth}${altMouths}${faceAccessorySvg(accId, crew)}</g></g></g>`;
}
function botIdleMode(b) {
  if (!b || !b.idle) return '';
  return typeof b.idle === 'string' ? b.idle : (b.idle.active || '');
}
function expressionFor(b) {
  return b?.mentioned ? 'mention' : normalizeState(b?.state);
}
function orbitAngle(b) {
  const duration = orbitDurations[b?.state];
  if (!duration) return b?.orbitAngle || 0;
  let turns = (Date.now() - (b?.orbitAt || Date.now())) / duration;
  if (b.state === 'thinking') turns = Math.floor(turns * 12) / 12;
  return ((b?.orbitAngle || 0) + turns * 360) % 360;
}
function orbitStyle(b) {
  const angle = b?.orbitAngle || 0;
  const at = b?.orbitAt || Date.now();
  const id = String(b?.id || 'bot');
  const h = stableHash(id);
  const phase = (b?.phase !== undefined ? b.phase : -((h % 20) * 0.25)).toFixed(2) + 's';
  const ringDelay = -(Date.now() - at) / 1000 + 's';
  const breathe = (4.4 + (crewUidCounter % 3) * 0.55).toFixed(2) + 's';
  return `--ring-start:${angle}deg;--ring-end:${angle + 360}deg;--ring-delay:${ringDelay};--phase:${phase};--breathe:${breathe}`;
}
function avatarSvgMarkup(b, state = b?.state, opts = {}) {
  const bot = b || { id: 'thyca', name: 'Thyca', color: '#9179ec', state: 'idle' };
  const c = CREW_BY_ID[opts.crew] || crewShapeFor(bot);
  const theme = opts.theme && FAMILY_BY_ID[opts.theme] ? opts.theme : themeFor(bot);
  const acc = opts.accessory && ACCESSORY_BY_ID[opts.accessory] ? opts.accessory : accessoryFor(bot);
  const variant = opts.variant || 'circle';
  const dark = isDarkMode();
  const away = !!opts.away;
  crewUidCounter++;
  const uid = `cr${crewUidCounter}`;
  const faceMarkup = away
    ? `<g class="away-silhouette">${c.silhouetteSvg()}</g>`
    : buildEyesAndMouth(c, uid, acc);
  const accentStroke = theme === 'starlight' || dark ? '#f7e3a1' : '#8f5a22';
  const dotFill = theme === 'starlight' || dark ? '#f7e3a1' : 'var(--bot-dark)';
  const shadowFill = dark ? '#09080c' : (theme === 'starlight' ? '#121024' : 'var(--bot-dark)');
  const shadowOpacity = dark ? '0.32' : '0.16';
  if (variant === 'stage') {
    const memberAttr = bot.id ? ` data-member-id="${escapeHTML(bot.id)}"` : '';
    return `<svg class="crew-svg base" viewBox="0 0 188 78" role="presentation">${stageSceneSvg(theme, dark)}${away ? '' : `<ellipse cx="94" cy="76.5" rx="33" ry="4.2" fill="${shadowFill}" opacity="${shadowOpacity}"/>`}<g class="char-box" data-crew-char="${c.id}"${memberAttr} transform="translate(50.8 -1) scale(0.72)">${faceMarkup}</g><g class="state-think-dots" fill="${dotFill}"><circle cx="136" cy="24" r="2.2"/><circle cx="144" cy="18" r="2.8"/><circle cx="154" cy="13" r="3.4"/></g><path class="state-spark" d="M30 14l2.1 5.4 5.4 2.1-5.4 2.1-2.1 5.4-2.1-5.4-5.4-2.1 5.4-2.1Z" fill="#e59d24"/><g class="state-question" fill="none" stroke="${accentStroke}" stroke-width="2.2" stroke-linecap="round"><path d="M148 16a5.4 5.4 0 1 1 5.8 8.3c-1.8.8-2.5 1.8-2.5 3.4"/><circle cx="151.3" cy="32.8" r="1.4" fill="${accentStroke}" stroke="none"/></g></svg>`;
  }
  const galaxyDef = theme === 'starlight'
    ? `<radialGradient id="${uid}-gal" cx="0.35" cy="0.28" r="0.78"><stop offset="0" stop-color="${dark ? '#3b335c' : '#4e4470'}"/><stop offset="1" stop-color="${dark ? '#1e1a32' : '#312c4d'}"/></radialGradient>`
    : '';
  const memberAttr = bot.id ? ` data-member-id="${escapeHTML(bot.id)}"` : '';
  return `<svg class="crew-svg base" viewBox="0 0 120 120" role="presentation"><defs><clipPath id="${uid}-clip"><circle cx="60" cy="60" r="53.5"/></clipPath>${galaxyDef}</defs><circle class="well-bg circle-well-bg" cx="60" cy="60" r="54" fill="${circleWellFill(theme, uid, dark)}"/><g clip-path="url(#${uid}-clip)">${circleSkySvg(theme, dark)}<g class="char-box" data-crew-char="${c.id}"${memberAttr} transform="translate(6 5) scale(0.9)">${faceMarkup}</g><g class="state-think-dots" fill="${dotFill}"><circle cx="84" cy="32" r="2.4"/><circle cx="92" cy="24" r="3.1"/><circle cx="101" cy="18" r="3.6"/></g><path class="state-spark" d="M26 22l2.3 6 6 2.3-6 2.3-2.3 6-2.3-6-6-2.3 6-2.3Z" fill="#e59d24"/><g class="state-question" fill="none" stroke="${accentStroke}" stroke-width="2.5" stroke-linecap="round"><path d="M88 22a5.8 5.8 0 1 1 6.2 8.8c-1.9.8-2.6 2-2.6 3.7"/><circle cx="91.6" cy="39.5" r="1.5" fill="${accentStroke}" stroke="none"/></g></g><circle class="frame-ring" cx="60" cy="60" r="54" fill="none" stroke="${theme === 'starlight' ? '#7a6ca8' : 'var(--bot-color)'}" stroke-width="${away ? '2.8' : '3.6'}"${away ? ' stroke-dasharray="6 5" opacity="0.72"' : ''}/><circle class="orbit-arc" cx="60" cy="60" r="54"/></svg>`;
}
function avatar(b, state = b?.state, extra = '', opts = {}) {
  const bot = b || { id: 'thyca', name: 'Thyca', color: '#9179ec', state: 'idle' };
  const st = normalizeState(state || bot.state);
  const preview = Boolean(opts.preview || opts.away || String(extra).includes('expression-preview'));
  const expr = opts.expression ? normalizeState(opts.expression) : (preview ? st : expressionFor(bot));
  const idle = opts.idle !== undefined ? (typeof opts.idle === 'string' ? opts.idle : (opts.idle?.active || '')) : (!preview && st === 'idle' ? botIdleMode(bot) : '');
  const c = CREW_BY_ID[opts.crew] || crewShapeFor(bot);
  const theme = opts.theme && FAMILY_BY_ID[opts.theme] ? opts.theme : themeFor(bot);
  const acc = opts.accessory && ACCESSORY_BY_ID[opts.accessory] ? opts.accessory : accessoryFor(bot);
  const variant = opts.variant || 'circle';
  const away = !!opts.away;
  const speaking = opts.speaking;
  const sizeCls = variant === 'circle' ? `is-circle ${opts.sizeClass || 'size-md'}` : 'is-stage';
  const cls = ['crew-avatar', `variant-${variant}`, sizeCls, preview && !String(extra).includes('expression-preview') ? 'expression-preview' : '', extra || ''].filter(Boolean).join(' ');
  const pal = bot.palette || (bot.color && typeof derivePalette === 'function' ? derivePalette(bot.color, isDarkMode()) : derivePalette(c.vibrantHex, isDarkMode()));
  const pStyle = pal && typeof paletteStyle === 'function' ? paletteStyle({ palette: pal }) : '';
  const selAttr = typeof selectedBot !== 'undefined' ? ` data-selected="${bot.id === selectedBot}"` : '';
  const idleAttr = idle ? ` data-idle="${escapeHTML(idle)}"` : '';
  const speakAttr = speaking !== undefined ? ` data-speaking="${speaking}"` : '';
  const awayAttr = away ? ' data-away="group"' : ' data-away="false"';
  const attrs = `class="${cls}" data-bot-avatar="${escapeHTML(bot.id || 'bot')}" data-crew-shape="${c.id}" data-crew-char="${c.id}" data-bot-theme="${theme}" data-theme-family="${theme}" data-accessory="${acc}" data-state="${st}" data-expression="${expr}"${idleAttr}${selAttr}${speakAttr}${awayAttr} style="${pStyle};${orbitStyle(bot)}"`;
  const svg = avatarSvgMarkup(bot, st, { ...opts, preview });
  return `<span ${attrs} aria-hidden="true">${svg}</span>`;
}
const GROUP_STAGE_SLOTS = {
  1: [{ x: 64, y: 4, s: 0.68, row: 'front' }],
  2: [{ x: 24, y: 6, s: 0.64, row: 'front' }, { x: 88, y: 6, s: 0.64, row: 'front' }],
  3: [{ x: 60, y: -3, s: 0.56, row: 'back' }, { x: 14, y: 12, s: 0.58, row: 'front' }, { x: 104, y: 12, s: 0.58, row: 'front' }],
  4: [{ x: 37, y: -2, s: 0.50, row: 'back' }, { x: 89, y: -2, s: 0.50, row: 'back' }, { x: 10, y: 17, s: 0.52, row: 'front' }, { x: 116, y: 17, s: 0.52, row: 'front' }],
  5: [{ x: 35, y: -1, s: 0.48, row: 'back' }, { x: 95, y: -1, s: 0.48, row: 'back' }, { x: 5, y: 21, s: 0.48, row: 'front' }, { x: 65, y: 21, s: 0.48, row: 'front' }, { x: 125, y: 21, s: 0.48, row: 'front' }],
  6: [{ x: 15, y: 1, s: 0.45, row: 'back' }, { x: 67, y: 1, s: 0.45, row: 'back' }, { x: 119, y: 1, s: 0.45, row: 'back' }, { x: 22, y: 25, s: 0.45, row: 'front' }, { x: 67, y: 25, s: 0.45, row: 'front' }, { x: 112, y: 25, s: 0.45, row: 'front' }],
};
function renderGroupStageAvatar(members, opts = {}) {
  const list = Array.isArray(members) ? members.filter(Boolean) : [];
  const dark = isDarkMode();
  crewUidCounter++;
  const uid = `gr${crewUidCounter}`;
  const shown = list.slice(0, 6);
  const slots = GROUP_STAGE_SLOTS[shown.length] || GROUP_STAGE_SLOTS[6];
  const coordinating = Boolean(opts.coordinating || opts.active);
  const state = coordinating ? 'group' : 'idle';
  const speakerId = opts.speakerId || null;
  const hasSpeaker = Boolean(speakerId && shown.some(x => x.id === speakerId));
  const shadowFill = dark ? '#090c09' : '#4a573a';
  const shadowOpacity = dark ? '0.36' : '0.18';
  const buildMemberLayer = (m, i) => {
    const c = crewShapeFor(m);
    const acc = accessoryFor(m);
    const pal = m.palette || (typeof derivePalette === 'function' ? derivePalette(m.color || c.vibrantHex, dark) : null);
    const pStyle = pal && typeof paletteStyle === 'function' ? paletteStyle({ palette: pal }) : '';
    const slot = slots[i] || slots[0];
    const slotCx = (slot.x + 60 * slot.s).toFixed(1);
    const speakAttr = coordinating
      ? (hasSpeaker ? ` data-speaking="${speakerId === m.id}"` : ' data-working-together="true"')
      : '';
    const faceMarkup = buildEyesAndMouth(c, `${uid}-m${i}`, acc);
    const footY = Math.min(slot.row === 'back' ? 56.5 : 76.2, slot.y + 112 * slot.s).toFixed(1);
    const shadowRx = (26 * slot.s).toFixed(1);
    const phaseVal = m.phase !== undefined ? m.phase : -1.2 - i * 0.7;
    return `<ellipse cx="${slotCx}" cy="${footY}" rx="${shadowRx}" ry="2.8" fill="${shadowFill}" opacity="${shadowOpacity}"/><g class="char-box" data-member-id="${escapeHTML(m.id)}" data-crew-char="${c.id}" data-slot-x="${slotCx}"${speakAttr} style="${pStyle};--phase:${phaseVal}s;--breathe:${(4.4 + (i % 3) * 0.45).toFixed(2)}s;--wave-delay:${(i * 0.06).toFixed(2)}s" transform="translate(${slot.x} ${slot.y}) scale(${slot.s})">${faceMarkup}</g>`;
  };
  const backLayers = shown.map((m, i) => ((slots[i] || slots[0]).row === 'back' ? buildMemberLayer(m, i) : '')).join('');
  const frontLayers = shown.map((m, i) => ((slots[i] || slots[0]).row !== 'back' ? buildMemberLayer(m, i) : '')).join('');
  const wallArchFill = dark ? '#283329' : '#faf6eb';
  const wallArchStroke = dark ? '#3a4a3c' : '#dfd4bc';
  const lampGlow = dark ? '#e8c47a' : '#f2d089';
  const backStepFill = dark ? '#324133' : '#d6e0c5';
  const backStepTop = dark ? '#465a48' : '#c0cfab';
  const frontStepFill = dark ? '#253126' : '#c0cfa9';
  const frontStepTop = dark ? '#394c3b' : '#a6ba8d';
  const extraCount = list.length > 6 ? `<g class="group-stage-overflow"><rect x="150" y="8" width="30" height="16" rx="8" fill="${dark ? '#233128' : '#ffffffd9'}" stroke="${dark ? '#487863' : '#315d4c'}" stroke-width="1.2"/><text x="165" y="19" text-anchor="middle" font-size="9" font-weight="700" fill="${dark ? '#9be0c1' : '#315d4c'}">+${list.length - 6}</text></g>` : '';
  return `<span class="crew-avatar variant-stage is-stage is-group-stage" data-group-stage="${escapeHTML(opts.groupId || 'group')}" data-state="${state}" data-expression="${state}" data-speaker="${escapeHTML(speakerId || '')}" aria-hidden="true"><svg class="crew-svg base" viewBox="0 0 188 78" role="presentation"><g class="theme-scene-art"><path d="M18 47 V32 Q18 5 94 5 Q170 5 170 32 V47 Z" fill="${wallArchFill}" stroke="${wallArchStroke}" stroke-width="1.2"/><ellipse cx="94" cy="14" rx="46" ry="12" fill="${lampGlow}" opacity="${dark ? '0.12' : '0.24'}"/></g><g class="group-tier-back"><rect x="0" y="45" width="188" height="14" fill="${backStepFill}"/><rect x="0" y="45" width="188" height="2" fill="${backStepTop}"/></g>${backLayers}<g class="group-tier-front"><rect x="0" y="58" width="188" height="20" fill="${frontStepFill}"/><rect x="0" y="58" width="188" height="2.2" fill="${frontStepTop}"/></g>${frontLayers}${extraCount}</svg></span>`;
}
function syncAvatar(el, b, opts = {}) {
  if (!el || !b) return;
  const st = normalizeState(b.state);
  const c = crewShapeFor(b);
  const theme = themeFor(b);
  const acc = accessoryFor(b);
  const away = !!(opts.away !== undefined ? opts.away : b.away);
  const changedState = el.dataset.state !== st;
  const needRebuild =
    !el.innerHTML?.includes('crew-svg') ||
    el.dataset.crewShape !== c.id ||
    el.dataset.themeFamily !== theme ||
    el.dataset.accessory !== acc ||
    el.dataset.away !== String(away ? 'group' : 'false');
  el.dataset.state = st;
  el.dataset.expression = opts.expression ? normalizeState(opts.expression) : expressionFor(b);
  if (typeof selectedBot !== 'undefined') el.dataset.selected = String(b.id === selectedBot);
  if (opts.speaking !== undefined) el.dataset.speaking = String(opts.speaking);
  else delete el.dataset.speaking;
  if (changedState) {
    el.style?.setProperty?.('--ring-start', (b.orbitAngle || 0) + 'deg');
    el.style?.setProperty?.('--ring-end', ((b.orbitAngle || 0) + 360) + 'deg');
    el.style?.setProperty?.('--ring-delay', -(Date.now() - (b.orbitAt || Date.now())) / 1000 + 's');
    el.classList?.remove?.('blink');
  }
  if (['offline', 'error', 'done'].includes(st) || b.mentioned) el.classList?.remove?.('blink');
  const activeIdle = (typeof idleEligible === 'function' ? idleEligible(b) : st === 'idle') ? botIdleMode(b) : '';
  const nextIdle = opts.idle !== undefined ? (typeof opts.idle === 'string' ? opts.idle : (opts.idle?.active || '')) : activeIdle;
  if (nextIdle) el.dataset.idle = nextIdle;
  else delete el.dataset.idle;
  if (needRebuild) {
    const variant = el.classList?.contains?.('variant-stage') || el.classList?.contains?.('is-stage') ? 'stage' : 'circle';
    el.innerHTML = avatarSvgMarkup(b, st, { variant, away, preview: el.classList?.contains?.('expression-preview') });
    el.dataset.crewShape = c.id;
    el.dataset.crewChar = c.id;
    el.dataset.botTheme = theme;
    el.dataset.themeFamily = theme;
    el.dataset.accessory = acc;
    el.dataset.away = away ? 'group' : 'false';
  }
  if (!el.classList?.contains?.('expression-preview')) {
    if (needRebuild || !liveRigs.some(r => r.host === el)) registerLiveRigs();
    wakeEyeLoop();
  }
}
const stateTimers = new Map();
const mentionTimers = new Map();
const musicBoxes = new Map();
const streamBoxes = new Map();
const MUSIC_BEAT_MS = 200;
const MUSIC_DECAY = 8;
const MUSIC_QUIET_BEATS = 2;
function clearMusic(id) {
  const box = musicBoxes.get(id);
  if (box) {
    clearInterval(box.timer);
    musicBoxes.delete(id);
  }
  const sb = streamBoxes.get(id);
  if (sb?.timer) clearTimeout(sb.timer);
  streamBoxes.delete(id);
  if (typeof document !== 'undefined' && document.querySelectorAll) {
    document.querySelectorAll(`.crew-avatar[data-bot-avatar="${id}"]:not(.expression-preview)`).forEach(a => {
      delete a.dataset.streaming;
      a.style?.removeProperty?.('--writing-gaze');
    });
  }
  wakeEyeLoop();
}
function paintMusicBeat(id, seed) {
  if (typeof document !== 'undefined' && document.querySelectorAll) {
    document.querySelectorAll(`.crew-avatar[data-bot-avatar="${id}"]:not(.expression-preview)`).forEach(a => {
      a.dataset.streaming = 'true';
      a.style?.setProperty?.('--writing-gaze', ((seed % 9) / 4 - 1) + 'px');
    });
  }
}
function musicBeat(id) {
  const box = musicBoxes.get(id);
  if (!box) return;
  if (box.energy > 0) {
    box.energy = Math.max(0, box.energy - MUSIC_DECAY);
    paintMusicBeat(id, box.seed);
    box.quiet = 0;
  } else if (++box.quiet >= MUSIC_QUIET_BEATS) {
    clearMusic(id);
  }
}
function notifyText(id, chunk) {
  const b = typeof getBot === 'function' ? getBot(id) : null;
  if (!b || b.state !== 'writing' || !String(chunk || '').trim()) return false;
  const text = String(chunk);
  const seed = [...text].reduce((n, c) => n + c.charCodeAt(0), 0);
  let box = musicBoxes.get(id);
  if (!box) {
    box = { energy: 0, seed: 0, quiet: 0, timer: 0 };
    musicBoxes.set(id, box);
  }
  box.energy += text.length;
  box.seed += seed;
  box.quiet = 0;
  if (!box.timer) {
    paintMusicBeat(id, box.seed);
    box.timer = setInterval(() => musicBeat(id), MUSIC_BEAT_MS);
  }
  let sb = streamBoxes.get(id);
  if (!sb) {
    sb = { step: 0, gazeX: -3.2, timer: 0 };
    streamBoxes.set(id, sb);
  }
  sb.step += text.length;
  sb.gazeX = -3.4 + ((sb.step % 28) / 28) * 6.8;
  if (sb.timer) clearTimeout(sb.timer);
  sb.timer = setTimeout(() => {
    const cur = streamBoxes.get(id);
    if (cur) {
      cur.gazeX = 0;
      wakeEyeLoop();
    }
  }, 550);
  wakeEyeLoop();
  return true;
}
function setState(id, st, summary, elapsed) {
  const b = typeof getBot === 'function' ? getBot(id) : null;
  const mapped = internalStateAliases[st] || st;
  if (!b || !states[mapped]) return false;
  if (mapped === 'mention') return mentionBot(id);
  if (mapped !== 'idle') {
    if (typeof clearIdle === 'function') clearIdle(b);
  } else if (b.state !== 'idle') {
    if (typeof clearIdle === 'function') clearIdle(b);
    if (b.idle && typeof b.idle === 'object') b.idle.since = Date.now();
  }
  if (['error', 'offline'].includes(mapped) && typeof lab !== 'undefined' && lab.botId === id && lab.mode && typeof stopLab === 'function') {
    stopLab(false);
  }
  if (['error', 'offline'].includes(mapped) && typeof runs !== 'undefined' && runs) {
    const t = [...runs.values()].find(x => x.botId === id);
    if (t && typeof cancelTurn === 'function') {
      cancelTurn(
        t,
        mapped === 'offline' ? 'Kết nối đã ngắt. Phần đã viết được giữ lại.' : 'Lượt trả lời gặp lỗi. Bạn có thể thử lại.',
        mapped === 'offline' ? 'mất kết nối' : 'gặp lỗi',
      );
    }
  }
  clearTimeout(stateTimers.get(id));
  clearTimeout(mentionTimers.get(id));
  b.mentioned = false;
  const changed = b.state !== mapped;
  const angle = orbitAngle(b);
  b.revision = (b.revision || 0) + 1;
  b.state = mapped;
  if (changed) {
    b.orbitAngle = mapped === 'waiting' && angle === 0 ? 135 : angle;
    b.orbitAt = Date.now();
  }
  b.summary = summary !== undefined ? String(summary) : '';
  if (elapsed !== undefined) b.elapsed = elapsed;
  if (mapped !== 'writing') clearMusic(id);
  if (['offline', 'error'].includes(mapped) && typeof activeGroup !== 'undefined' && activeGroup?.speaker === id) {
    activeGroup.speaker = null;
  }
  if (typeof updateCard === 'function') {
    updateCard(b);
  } else if (typeof document !== 'undefined' && document.querySelectorAll) {
    document.querySelectorAll(`.crew-avatar[data-bot-avatar="${id}"]:not(.expression-preview)`).forEach(el => syncAvatar(el, b));
  }
  if (['error', 'offline'].includes(mapped) && typeof announce === 'function') {
    announce(b.name + ': ' + states[mapped][1]);
  }
  if (mapped === 'done') {
    const revision = b.revision;
    stateTimers.set(id, setTimeout(() => {
      if (b.state === 'done' && b.revision === revision) setState(id, 'idle', 'Xong rồi. Mình vẫn ở đây\nkhi bạn cần thêm.', 0);
    }, 1600));
  }
  return true;
}
function mentionBot(id) {
  const b = typeof getBot === 'function' ? getBot(id) : null;
  if (!b || ['offline', 'error'].includes(b.state)) return false;
  clearTimeout(mentionTimers.get(id));
  if (typeof clearIdle === 'function') clearIdle(b);
  b.mentioned = true;
  if (typeof updateCard === 'function') updateCard(b);
  mentionTimers.set(id, setTimeout(() => {
    b.mentioned = false;
    if (typeof updateCard === 'function') updateCard(b);
  }, 500));
  if (typeof announce === 'function') announce(b.name + ': Bạn gọi mình?');
  return true;
}
let liveRigs = [];
const rigStateByBox = typeof WeakMap === 'function' ? new WeakMap() : null;
let rafId = null;
let ctmDirty = true;
const _win = typeof window !== 'undefined' ? window : {};
if (!_win.ThycaPointer) _win.ThycaPointer = { clientX: (_win.innerWidth || 1000) * 0.55, clientY: (_win.innerHeight || 700) * 0.42 };
function markCtmDirty() {
  ctmDirty = true;
  wakeEyeLoop();
}
function registerLiveRigs() {
  const prevByBox = new Map(liveRigs.map(r => [r.box, r]));
  liveRigs = [];
  ctmDirty = true;
  if (typeof document === 'undefined' || !document.querySelectorAll) return;
  document.querySelectorAll('.crew-avatar:not(.expression-preview)').forEach(host => {
    const svg = host.querySelector ? host.querySelector('svg.crew-svg') : null;
    if (!svg) return;
    const svgPoint = typeof svg.createSVGPoint === 'function' ? svg.createSVGPoint() : null;
    host.querySelectorAll('.char-box').forEach(box => {
      const face = box.querySelector ? box.querySelector('.char-face') : null;
      if (!face) return;
      const prev = prevByBox.get(box) || rigStateByBox?.get(box) || null;
      const faceCx = parseFloat(face.dataset?.faceCx || '60');
      const faceCy = parseFloat(face.dataset?.faceCy || '60');
      const eyes = [...(box.querySelectorAll ? box.querySelectorAll('.eye-unit') : [])].map((unit, idx) => ({
        cx: parseFloat(unit.dataset?.eyeCx || '60'),
        cy: parseFloat(unit.dataset?.eyeCy || '60'),
        r: parseFloat(unit.dataset?.eyeR || '10'),
        pupil: unit.querySelector('.eye-pupil-group'),
        x: prev?.eyes?.[idx]?.x ?? 0,
        y: prev?.eyes?.[idx]?.y ?? 0,
      })).filter(e => e.pupil);
      const rig = {
        host,
        svg,
        box,
        memberId: box.dataset?.memberId || host.dataset?.botAvatar,
        face,
        faceCx,
        faceCy,
        fx: prev ? prev.fx : 0,
        fy: prev ? prev.fy : 0,
        eyes,
        invCtm: null,
        visible: false,
        svgPoint,
      };
      rigStateByBox?.set(box, rig);
      liveRigs.push(rig);
    });
  });
}
function refreshRigMatrices() {
  for (const rig of liveRigs) {
    if (!rig.host.isConnected || typeof rig.svg.getBoundingClientRect !== 'function') {
      rig.visible = false;
      rig.invCtm = null;
      continue;
    }
    const rect = rig.svg.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) {
      rig.visible = false;
      rig.invCtm = null;
      continue;
    }
    rig.visible = true;
    const ctm = typeof rig.box.getScreenCTM === 'function'
      ? rig.box.getScreenCTM()
      : (typeof rig.svg.getScreenCTM === 'function' ? rig.svg.getScreenCTM() : null);
    try {
      rig.invCtm = ctm ? ctm.inverse() : null;
    } catch {
      rig.invCtm = null;
    }
  }
  ctmDirty = false;
}
function clampVec(dx, dy, maxR) {
  const dist = Math.hypot(dx, dy) || 1;
  const scale = Math.min(1, maxR / dist);
  return [dx * scale, dy * scale];
}
function stateGazeOverride(rig, now) {
  const st = rig.host.dataset?.state;
  const idle = rig.host.dataset?.idle;
  const botId = rig.memberId || rig.host.dataset?.botAvatar;
  if (_win.ThycaIsShy) return { face: [-7.2, 0], eye: [-4.6, 0] };
  if (rig.host.dataset?.peerFace === 'left') return { face: [3.8, 0.2], eye: [4.6, 0.2] };
  if (rig.host.dataset?.peerFace === 'right') return { face: [-3.8, 0.2], eye: [-4.6, 0.2] };
  if (st === 'offline' || st === 'done' || st === 'error') return { face: [0, 0], eye: [0, 0] };
  if (st === 'thinking') return { face: [-2.4, -3.2], eye: [-3.2, -3.6] };
  if (st === 'waiting') return { face: [0, 3.0], eye: [0, 3.6] };
  if (st === 'writing') {
    const box = streamBoxes.get(botId);
    if (box) return { face: [box.gazeX * 0.45, 2.2], eye: [box.gazeX, 2.8], continuous: true };
    return { face: [0.6, 2.0], eye: [1.0, 2.5] };
  }
  const ex = _win.ThycaPeerExchange;
  if (ex && !rig.host.classList?.contains?.('is-group-stage') && (botId === ex.a || botId === ex.b) && typeof document !== 'undefined') {
    const partnerId = botId === ex.a ? ex.b : ex.a;
    const partnerEl = document.querySelector(`.bot-card[data-bot-id="${partnerId}"]`);
    if (partnerEl) {
      const r = partnerEl.getBoundingClientRect();
      if (r.width && r.height) return { targetClient: [r.left + r.width / 2, r.top + r.height / 2] };
    }
  }
  if (st === 'working') {
    if (_win.ThycaHoverCardId === botId) return null;
    return { face: [0.6, 2.6], eye: [1.0, 3.5] };
  }
  if (st === 'group' && typeof document !== 'undefined') {
    const speakerId = _win.ThycaGroupSpeaker || null;
    if (rig.host.classList?.contains?.('is-group-stage')) {
      const hasSpeaker = Boolean(speakerId && rig.host.querySelector(`.char-box[data-member-id="${speakerId}"]`));
      if (!hasSpeaker) {
        if (_win.ThycaHoverCardId && _win.ThycaHoverCardId === rig.host.dataset?.groupStage) return null;
        const slotX = parseFloat(rig.box.dataset?.slotX || '94');
        const dx = Math.max(-1, Math.min(1, (94 - slotX) / 42));
        return { face: [dx * 2.8, 2.4], eye: [dx * 4.2, 3.2] };
      }
      const speakerBox = rig.host.querySelector(`.char-box[data-member-id="${speakerId}"]`);
      if (speakerBox && botId !== speakerId) {
        const r = speakerBox.getBoundingClientRect();
        if (r.width && r.height) return { targetClient: [r.left + r.width / 2, r.top + r.height / 2] };
      }
      return null;
    }
    const speakerEl = speakerId ? document.querySelector(`.bot-card[data-bot-id="${speakerId}"]`) : null;
    if (speakerEl && botId !== speakerId) {
      const r = speakerEl.getBoundingClientRect();
      if (r.width && r.height) return { targetClient: [r.left + r.width / 2, r.top + r.height / 2] };
    }
  }
  if (_win.ThycaChatFocused && botId === (typeof activeId !== 'undefined' ? activeId : null) && st === 'idle' && typeof document !== 'undefined') {
    const chatInput = document.getElementById('message');
    if (chatInput) {
      const r = chatInput.getBoundingClientRect();
      if (r.width && r.height) return { targetClient: [r.left + r.width * 0.35, r.top + r.height / 2] };
    }
  }
  if (st === 'idle' && idle) {
    if (idle === 'dream') return { face: [2.8, -3.4], eye: [3.6, -4.0] };
    if (idle === 'look') {
      const phase = Math.sin(now / 900);
      return { face: [phase * 3.2, Math.cos(now / 1300) * 1.4], eye: [phase * 4.4, Math.cos(now / 1300) * 2.0], continuous: true };
    }
    if (idle === 'peek') return { face: [4.2, 0.4], eye: [4.8, 0.4] };
    if (idle === 'sleepy') return { face: [0, 2.5], eye: [0, 2.5] };
  }
  return null;
}
function isMotionPaused() {
  if (_win.ThycaPaused) return true;
  if (typeof document !== 'undefined' && document.hidden) return true;
  if (typeof document !== 'undefined' && document.documentElement?.dataset?.reduceMotion === 'true') return true;
  if (typeof _win.matchMedia === 'function' && _win.matchMedia('(prefers-reduced-motion: reduce)').matches) return true;
  return false;
}
function wakeEyeLoop() {
  if (rafId !== null) return;
  if (typeof requestAnimationFrame !== 'function' || isMotionPaused()) return;
  rafId = requestAnimationFrame(tickEyeLoop);
}
function tickEyeLoop(now) {
  rafId = null;
  if (isMotionPaused()) return;
  if (ctmDirty) refreshRigMatrices();
  let keepRunning = false;
  const ptr = _win.ThycaPointer || { clientX: 500, clientY: 300 };
  for (const rig of liveRigs) {
    if (!rig.visible || !rig.svgPoint || !rig.host.isConnected) continue;
    const override = stateGazeOverride(rig, now);
    if (override && override.continuous) keepRunning = true;
    let targetFaceX = 0;
    let targetFaceY = 0;
    let localPtrX = null;
    let localPtrY = null;
    if (override && override.face) {
      targetFaceX = override.face[0];
      targetFaceY = override.face[1];
    } else if (rig.invCtm) {
      rig.svgPoint.x = override && override.targetClient ? override.targetClient[0] : ptr.clientX;
      rig.svgPoint.y = override && override.targetClient ? override.targetClient[1] : ptr.clientY;
      const pt = rig.svgPoint.matrixTransform(rig.invCtm);
      localPtrX = pt.x;
      localPtrY = pt.y;
      const dx = localPtrX - rig.faceCx;
      const dy = localPtrY - rig.faceCy;
      const [fx, fy] = clampVec(dx, dy, Math.min(4.6, Math.hypot(dx, dy) / 22));
      targetFaceX = fx;
      targetFaceY = fy;
    }
    const dfx = targetFaceX - rig.fx;
    const dfy = targetFaceY - rig.fy;
    if (Math.abs(dfx) > 0.04 || Math.abs(dfy) > 0.04) {
      rig.fx += dfx * 0.14;
      rig.fy += dfy * 0.14;
      keepRunning = true;
    } else {
      rig.fx = targetFaceX;
      rig.fy = targetFaceY;
    }
    rig.face.setAttribute('transform', `translate(${rig.fx.toFixed(2)} ${rig.fy.toFixed(2)})`);
    for (const eye of rig.eyes) {
      let tx = 0;
      let ty = 0;
      if (override && override.eye) [tx, ty] = clampVec(override.eye[0], override.eye[1], 0.46 * eye.r);
      else if (localPtrX !== null) [tx, ty] = clampVec(localPtrX - eye.cx, localPtrY - eye.cy, 0.46 * eye.r);
      const dex = tx - eye.x;
      const dey = ty - eye.y;
      if (Math.abs(dex) > 0.04 || Math.abs(dey) > 0.04) {
        eye.x += dex * 0.24;
        eye.y += dey * 0.24;
        keepRunning = true;
      } else {
        eye.x = tx;
        eye.y = ty;
      }
      eye.pupil.setAttribute('transform', `translate(${eye.x.toFixed(2)} ${eye.y.toFixed(2)})`);
    }
  }
  if (keepRunning) rafId = requestAnimationFrame(tickEyeLoop);
}
function setAvatarShy(flag) {
  _win.ThycaIsShy = !!flag;
  if (typeof document !== 'undefined') {
    document.body?.classList?.toggle?.('is-shy', _win.ThycaIsShy);
    if (!_win.ThycaIsShy) document.body?.classList?.remove?.('is-reveal');
    document.querySelectorAll?.('.crew-avatar:not(.expression-preview)').forEach(el => {
      el.classList.toggle('is-shy', _win.ThycaIsShy);
      if (!_win.ThycaIsShy) el.classList.remove('is-reveal');
    });
  }
  wakeEyeLoop();
}
function setAvatarReveal(flag) {
  const show = !!flag && !!_win.ThycaIsShy;
  if (typeof document !== 'undefined') {
    document.body?.classList?.toggle?.('is-reveal', show);
    document.querySelectorAll?.('.crew-avatar:not(.expression-preview)').forEach(el => {
      el.classList.toggle('is-reveal', show);
    });
  }
}
