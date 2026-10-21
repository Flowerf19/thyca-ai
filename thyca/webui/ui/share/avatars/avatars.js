// Seal avatar engine: state metadata, wax markup, face render, state adapter.
'use strict';
const states={
 idle:['Nghỉ','Sẵn sàng','Thở nhẹ · chớp mắt'],
 waiting:['Đang chờ','Chờ bạn','Nhìn xuống · vành dừng'],
 thinking:['Đang nghĩ','Đang suy nghĩ','Nhìn lên · nhích từng nấc'],
 writing:['Đang viết','Đang viết','Soạn nhạc theo nhịp chữ'],
 working:['Chạy việc','Đang làm việc','Vành nhanh · nhún nhẹ'],
 done:['Xong','Đã hoàn tất','Mắt cười · một tia ✦'],
 error:['Lỗi','Có lỗi xảy ra','Mắt X · vành đỏ, rối'],
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
 idle:'Chớp mắt theo nhịp riêng, thở rất nhẹ. Vành dấu đứng yên.',
 waiting:'Mắt hướng xuống ô nhập. Vành dừng tại vị trí hiện tại, chấm sáng nhấp nháy chậm.',
 thinking:'Ánh mắt liếc lên, đầu hơi nghiêng. Vành dấu tiến từng nấc, có khoảng nghỉ giữa các nhịp.',
 writing:'Vành quay đều như giữ nhịp. Hai nốt trên khuông nhạc khẽ chuyển động theo chữ mới, rồi lắng lại khi ngừng viết.',
 working:'Mắt nheo tập trung. Vành quay nhanh hơn, cả con dấu nhún nhẹ theo nhịp làm việc.',
 done:'Nhún nhẹ, bật lên một nhịp rồi mỉm cười. Một tia ✦ nhỏ lóe lên; sau 1,6 giây bot tự về nghỉ.',
 error:'Mắt chữ X, đường vành rối và viền đỏ. Giữ trạng thái lỗi để bạn kịp nhận ra.',
 offline:'Nhắm mắt, giảm màu và độ đậm. Dừng cả hơi thở, chớp mắt lẫn chuyển động của vành.',
 mention:'Mắt mở to, giật mình nhẹ trong 0,5 giây, rồi tiếp tục trạng thái trước đó.',
 confused:'Nghiêng đầu, một bên mày nhướn lên cùng dấu hỏi nhỏ. Chờ bạn làm rõ.',
 group:'Các vành dấu dùng chung một nhịp. Người đang nói sáng lên; các bot còn lại lắng nghe.'
};
const stateTimers=new Map(),mentionTimers=new Map(),musicTimers=new Map();
const orbitDurations={thinking:9600,writing:6400,working:1500,group:4200};

// Exact SVG bodies and glyphs extracted from the original uploaded prototype.
const WAX_MARKUP = {"thyca": "<svg class=\"base\" viewBox=\"0 0 64 64\"><clippath id=\"wx1\"><path d=\"M57.20 32.00C57.14 36.04 56.55 40.33 54.91 44.02C53.26 47.72 50.64 52.13 47.31 54.18C43.99 56.24 38.96 56.20 34.95 56.34C30.95 56.47 27.39 55.84 23.28 54.98C19.18 54.12 12.89 54.04 10.33 51.20C7.78 48.35 8.32 42.11 7.96 37.93C7.59 33.75 7.43 30.03 8.13 26.12C8.83 22.20 9.79 17.66 12.18 14.44C14.56 11.21 18.55 8.65 22.43 6.77C26.32 4.90 31.55 2.41 35.50 3.20C39.45 3.99 42.83 8.77 46.12 11.54C49.41 14.31 53.40 16.39 55.25 19.80C57.09 23.21 57.25 27.96 57.20 32.00Z\" /></clippath><path class=\"wax-body\" d=\"M57.20 32.00C57.14 36.04 56.55 40.33 54.91 44.02C53.26 47.72 50.64 52.13 47.31 54.18C43.99 56.24 38.96 56.20 34.95 56.34C30.95 56.47 27.39 55.84 23.28 54.98C19.18 54.12 12.89 54.04 10.33 51.20C7.78 48.35 8.32 42.11 7.96 37.93C7.59 33.75 7.43 30.03 8.13 26.12C8.83 22.20 9.79 17.66 12.18 14.44C14.56 11.21 18.55 8.65 22.43 6.77C26.32 4.90 31.55 2.41 35.50 3.20C39.45 3.99 42.83 8.77 46.12 11.54C49.41 14.31 53.40 16.39 55.25 19.80C57.09 23.21 57.25 27.96 57.20 32.00Z\" /><path d=\"M57.20 32.00C57.14 36.04 56.55 40.33 54.91 44.02C53.26 47.72 50.64 52.13 47.31 54.18C43.99 56.24 38.96 56.20 34.95 56.34C30.95 56.47 27.39 55.84 23.28 54.98C19.18 54.12 12.89 54.04 10.33 51.20C7.78 48.35 8.32 42.11 7.96 37.93C7.59 33.75 7.43 30.03 8.13 26.12C8.83 22.20 9.79 17.66 12.18 14.44C14.56 11.21 18.55 8.65 22.43 6.77C26.32 4.90 31.55 2.41 35.50 3.20C39.45 3.99 42.83 8.77 46.12 11.54C49.41 14.31 53.40 16.39 55.25 19.80C57.09 23.21 57.25 27.96 57.20 32.00Z\" fill=\"url(#waxShade)\" /><path d=\"M57.20 32.00C57.14 36.04 56.55 40.33 54.91 44.02C53.26 47.72 50.64 52.13 47.31 54.18C43.99 56.24 38.96 56.20 34.95 56.34C30.95 56.47 27.39 55.84 23.28 54.98C19.18 54.12 12.89 54.04 10.33 51.20C7.78 48.35 8.32 42.11 7.96 37.93C7.59 33.75 7.43 30.03 8.13 26.12C8.83 22.20 9.79 17.66 12.18 14.44C14.56 11.21 18.55 8.65 22.43 6.77C26.32 4.90 31.55 2.41 35.50 3.20C39.45 3.99 42.83 8.77 46.12 11.54C49.41 14.31 53.40 16.39 55.25 19.80C57.09 23.21 57.25 27.96 57.20 32.00Z\" fill=\"url(#waxHl)\" /><g clip-path=\"url(#wx1)\"><rect class=\"wax-sheen\" x=\"0\" y=\"0\" width=\"64\" height=\"64\" fill=\"url(#waxSheen)\" /></g><circle class=\"wax-ring-hi\" cx=\"32\" cy=\"32\" r=\"19.5\" /><circle class=\"wax-ring\" cx=\"32\" cy=\"32\" r=\"19.5\" /><circle class=\"wax-notch\" cx=\"32\" cy=\"32\" r=\"16.4\" /><path class=\"wax-crack\" d=\"M40 7.5l-2.6 6.4 3 3.2-3.4 5.6\" /></svg><span class=\"glyph\"><svg viewBox=\"0 0 24 24\"><use href=\"#g-lens\" /></svg></span>", "coder": "<svg class=\"base\" viewBox=\"0 0 64 64\"><clippath id=\"wx2\"><path d=\"M59.85 32.00C60.19 36.33 59.84 41.80 57.77 45.52C55.70 49.25 51.18 52.10 47.43 54.36C43.69 56.61 39.50 58.45 35.29 59.06C31.07 59.67 25.77 59.80 22.13 58.03C18.48 56.26 16.37 51.67 13.42 48.46C10.47 45.25 6.00 42.68 4.45 38.79C2.90 34.90 2.76 29.12 4.12 25.13C5.49 21.13 9.61 18.02 12.61 14.82C15.61 11.63 18.30 7.87 22.11 5.93C25.93 4.00 31.27 2.60 35.49 3.22C39.71 3.84 44.06 6.92 47.43 9.64C50.81 12.36 53.66 15.82 55.73 19.54C57.80 23.27 59.51 27.67 59.85 32.00Z\" /></clippath><path class=\"wax-body\" d=\"M59.85 32.00C60.19 36.33 59.84 41.80 57.77 45.52C55.70 49.25 51.18 52.10 47.43 54.36C43.69 56.61 39.50 58.45 35.29 59.06C31.07 59.67 25.77 59.80 22.13 58.03C18.48 56.26 16.37 51.67 13.42 48.46C10.47 45.25 6.00 42.68 4.45 38.79C2.90 34.90 2.76 29.12 4.12 25.13C5.49 21.13 9.61 18.02 12.61 14.82C15.61 11.63 18.30 7.87 22.11 5.93C25.93 4.00 31.27 2.60 35.49 3.22C39.71 3.84 44.06 6.92 47.43 9.64C50.81 12.36 53.66 15.82 55.73 19.54C57.80 23.27 59.51 27.67 59.85 32.00Z\" /><path d=\"M59.85 32.00C60.19 36.33 59.84 41.80 57.77 45.52C55.70 49.25 51.18 52.10 47.43 54.36C43.69 56.61 39.50 58.45 35.29 59.06C31.07 59.67 25.77 59.80 22.13 58.03C18.48 56.26 16.37 51.67 13.42 48.46C10.47 45.25 6.00 42.68 4.45 38.79C2.90 34.90 2.76 29.12 4.12 25.13C5.49 21.13 9.61 18.02 12.61 14.82C15.61 11.63 18.30 7.87 22.11 5.93C25.93 4.00 31.27 2.60 35.49 3.22C39.71 3.84 44.06 6.92 47.43 9.64C50.81 12.36 53.66 15.82 55.73 19.54C57.80 23.27 59.51 27.67 59.85 32.00Z\" fill=\"url(#waxShade)\" /><path d=\"M59.85 32.00C60.19 36.33 59.84 41.80 57.77 45.52C55.70 49.25 51.18 52.10 47.43 54.36C43.69 56.61 39.50 58.45 35.29 59.06C31.07 59.67 25.77 59.80 22.13 58.03C18.48 56.26 16.37 51.67 13.42 48.46C10.47 45.25 6.00 42.68 4.45 38.79C2.90 34.90 2.76 29.12 4.12 25.13C5.49 21.13 9.61 18.02 12.61 14.82C15.61 11.63 18.30 7.87 22.11 5.93C25.93 4.00 31.27 2.60 35.49 3.22C39.71 3.84 44.06 6.92 47.43 9.64C50.81 12.36 53.66 15.82 55.73 19.54C57.80 23.27 59.51 27.67 59.85 32.00Z\" fill=\"url(#waxHl)\" /><g clip-path=\"url(#wx2)\"><rect class=\"wax-sheen\" x=\"0\" y=\"0\" width=\"64\" height=\"64\" fill=\"url(#waxSheen)\" /></g><circle class=\"wax-ring-hi\" cx=\"32\" cy=\"32\" r=\"19.5\" /><circle class=\"wax-ring\" cx=\"32\" cy=\"32\" r=\"19.5\" /><circle class=\"wax-notch\" cx=\"32\" cy=\"32\" r=\"16.4\" /><path class=\"wax-crack\" d=\"M40 7.5l-2.6 6.4 3 3.2-3.4 5.6\" /></svg><span class=\"glyph\"><svg class=\"fill\" viewBox=\"0 0 640 427\"><use href=\"#g-quill\" /></svg></span>", "researcher": "<svg class=\"base\" viewBox=\"0 0 64 64\"><clippath id=\"wx4\"><path d=\"M59.97 32.00C60.60 36.11 59.55 41.71 57.39 45.33C55.23 48.95 50.72 51.82 46.99 53.72C43.26 55.61 39.25 55.70 35.00 56.70C30.75 57.69 25.53 60.66 21.51 59.67C17.48 58.68 13.48 54.28 10.84 50.75C8.19 47.22 6.26 42.64 5.65 38.50C5.04 34.35 6.31 30.09 7.17 25.88C8.03 21.67 8.25 16.34 10.82 13.24C13.40 10.13 18.50 8.92 22.62 7.26C26.73 5.59 31.21 3.05 35.49 3.24C39.78 3.42 45.30 5.46 48.32 8.36C51.34 11.26 51.67 16.72 53.61 20.66C55.56 24.60 59.34 27.89 59.97 32.00Z\" /></clippath><path class=\"wax-body\" d=\"M59.97 32.00C60.60 36.11 59.55 41.71 57.39 45.33C55.23 48.95 50.72 51.82 46.99 53.72C43.26 55.61 39.25 55.70 35.00 56.70C30.75 57.69 25.53 60.66 21.51 59.67C17.48 58.68 13.48 54.28 10.84 50.75C8.19 47.22 6.26 42.64 5.65 38.50C5.04 34.35 6.31 30.09 7.17 25.88C8.03 21.67 8.25 16.34 10.82 13.24C13.40 10.13 18.50 8.92 22.62 7.26C26.73 5.59 31.21 3.05 35.49 3.24C39.78 3.42 45.30 5.46 48.32 8.36C51.34 11.26 51.67 16.72 53.61 20.66C55.56 24.60 59.34 27.89 59.97 32.00Z\" /><path d=\"M59.97 32.00C60.60 36.11 59.55 41.71 57.39 45.33C55.23 48.95 50.72 51.82 46.99 53.72C43.26 55.61 39.25 55.70 35.00 56.70C30.75 57.69 25.53 60.66 21.51 59.67C17.48 58.68 13.48 54.28 10.84 50.75C8.19 47.22 6.26 42.64 5.65 38.50C5.04 34.35 6.31 30.09 7.17 25.88C8.03 21.67 8.25 16.34 10.82 13.24C13.40 10.13 18.50 8.92 22.62 7.26C26.73 5.59 31.21 3.05 35.49 3.24C39.78 3.42 45.30 5.46 48.32 8.36C51.34 11.26 51.67 16.72 53.61 20.66C55.56 24.60 59.34 27.89 59.97 32.00Z\" fill=\"url(#waxShade)\" /><path d=\"M59.97 32.00C60.60 36.11 59.55 41.71 57.39 45.33C55.23 48.95 50.72 51.82 46.99 53.72C43.26 55.61 39.25 55.70 35.00 56.70C30.75 57.69 25.53 60.66 21.51 59.67C17.48 58.68 13.48 54.28 10.84 50.75C8.19 47.22 6.26 42.64 5.65 38.50C5.04 34.35 6.31 30.09 7.17 25.88C8.03 21.67 8.25 16.34 10.82 13.24C13.40 10.13 18.50 8.92 22.62 7.26C26.73 5.59 31.21 3.05 35.49 3.24C39.78 3.42 45.30 5.46 48.32 8.36C51.34 11.26 51.67 16.72 53.61 20.66C55.56 24.60 59.34 27.89 59.97 32.00Z\" fill=\"url(#waxHl)\" /><g clip-path=\"url(#wx4)\"><rect class=\"wax-sheen\" x=\"0\" y=\"0\" width=\"64\" height=\"64\" fill=\"url(#waxSheen)\" /></g><circle class=\"wax-ring-hi\" cx=\"32\" cy=\"32\" r=\"19.5\" /><circle class=\"wax-ring\" cx=\"32\" cy=\"32\" r=\"19.5\" /><circle class=\"wax-notch\" cx=\"32\" cy=\"32\" r=\"16.4\" /><path class=\"wax-crack\" d=\"M40 7.5l-2.6 6.4 3 3.2-3.4 5.6\" /></svg><span class=\"glyph\"><svg viewBox=\"0 0 24 24\"><use href=\"#g-code\" /></svg></span>"};

const waxState = state => stateAliases[state]||state;
function expressionFor(b){return b.mentioned?'mention':b.state;}
function orbitAngle(b){
 const duration=orbitDurations[b.state];if(!duration)return b.orbitAngle;
 let turns=(Date.now()-b.orbitAt)/duration;
 if(b.state==='thinking')turns=Math.floor(turns*12)/12;
 return (b.orbitAngle+turns*360)%360;
}
function orbitStyle(b){return `--ring-start:${b.orbitAngle}deg;--ring-end:${b.orbitAngle+360}deg;--ring-delay:${-(Date.now()-b.orbitAt)/1000}s`;}
function sealFace(uid){return `<span class="seal-face"><svg viewBox="0 0 64 64"><defs><radialGradient id="${uid}-eyes"><stop offset="0" stop-color="var(--bot-eye)"/><stop offset="1" stop-color="var(--bot-eye-edge)"/></radialGradient><clipPath id="${uid}-eye-clip"><ellipse cx="25.5" cy="34.5" rx="5.3" ry="7.5"/><ellipse cx="39" cy="32" rx="5.3" ry="7.5"/></clipPath></defs><g class="seal-features"><g class="seal-blink"><g class="seal-eye-open"><ellipse class="eye-left" cx="25.5" cy="34.5" rx="5.3" ry="7.5" fill="url(#${uid}-eyes)"/><ellipse cx="39" cy="32" rx="5.3" ry="7.5" fill="url(#${uid}-eyes)"/><g clip-path="url(#${uid}-eye-clip)"><g class="seal-pupils"><ellipse class="eye-left" cx="27.7" cy="33.5" rx="2.9" ry="4.6" fill="var(--bot-pupil)"/><ellipse cx="41.2" cy="31" rx="2.9" ry="4.6" fill="var(--bot-pupil)"/><circle class="eye-left" cx="28.2" cy="31.5" r=".65" fill="var(--bot-eye)" opacity=".8"/><circle cx="41.7" cy="29" r=".65" fill="var(--bot-eye)" opacity=".8"/></g><path class="seal-idle-lids" d="M18 23H32V33Q25.5 31 18 33ZM32 20H46V30.5Q39 28.5 32 30.5Z"/><path class="seal-lid" d="M18 24 33 32V22H18ZM31 28 46 24V19H31Z"/></g></g><g class="seal-eye-closed seal-eye-happy" fill="none" stroke="var(--bot-eye)" stroke-width="2.9" stroke-linecap="round"><path d="M21 35Q25.5 28 30 35M34.5 32.5Q39 25.5 43.5 32.5"/></g><g class="seal-eye-closed seal-eye-rest" fill="none" stroke="var(--bot-eye)" stroke-width="2.6" stroke-linecap="round"><path d="M21 33Q25.5 39 30 33M34.5 30.5Q39 36.5 43.5 30.5"/></g></g><path class="seal-idle-wink" d="M21 34Q25.5 29 30 34" fill="none" stroke="var(--bot-eye)" stroke-width="2.8" stroke-linecap="round"/><ellipse class="seal-mouth-hum" cx="33" cy="45" rx="1.5" ry="2.1" fill="none" stroke="var(--bot-eye)" stroke-width="1.5"/><g class="seal-eye-error" fill="none" stroke="var(--bot-eye)" stroke-width="2.5" stroke-linecap="round"><path d="m22 30 7 8m0-8-7 8m13-10 7 8m0-8-7 8"/></g><g class="seal-brow" fill="none" stroke="var(--bot-eye)" stroke-width="1.5" stroke-linecap="round"><path d="M21 25Q25.5 25.5 29 22M35 20.5Q39 24 43 23"/></g><path class="seal-mouth seal-mouth-smile" d="M29 44Q33 48 37 43" fill="none" stroke="var(--bot-eye)" stroke-width="1.7" stroke-linecap="round"/><path class="seal-mouth seal-mouth-worried" d="M29.5 46Q33 43 36.5 45" fill="none" stroke="var(--bot-eye)" stroke-width="1.5" stroke-linecap="round"/></g><g class="seal-music"><path class="seal-music-staff" d="M47 10h16M47 13h16M47 16h16M47 19h16M47 22h16"/><g class="seal-score-note score-note-a" fill="var(--bot-stamp)" stroke="var(--bot-dark)" stroke-width=".45"><ellipse cx="50.5" cy="20" rx="2.15" ry="1.35" transform="rotate(-20 50.5 20)"/><path d="M52.5 20V10c.5 1.8 3.2 1.6 2.7 4" fill="none" stroke="var(--bot-ink)" stroke-width="1.2" stroke-linecap="round"/></g><g class="seal-score-note score-note-b" fill="var(--bot-stamp)" stroke="var(--bot-dark)" stroke-width=".45"><ellipse cx="58.2" cy="16.5" rx="2.15" ry="1.35" transform="rotate(-20 58.2 16.5)"/><path d="M60.2 16.5V6.5" fill="none" stroke="var(--bot-ink)" stroke-width="1.2" stroke-linecap="round"/></g></g><circle class="seal-wait-dot" cx="33" cy="47" r="1.8" fill="var(--bot-eye)"/><g class="seal-question" fill="none" stroke="var(--bot-ink)" stroke-width="1.8" stroke-linecap="round"><path d="M54 11c0-5 7-5 7-.5 0 3-3.5 2.5-3.5 5"/><circle cx="57.5" cy="20" r=".8" fill="var(--bot-ink)" stroke="none"/></g><g class="seal-alert" stroke="var(--bot-mid)" stroke-width="2" stroke-linecap="round"><path d="m52 10 2-5m3 9 5-1M14 15l-3-3"/></g><path class="seal-tangle" d="M16 45c-5 5 3 9 6 3s-4-10-5-3 12 9 14 4-9-5-8 0 13 7 18 1" fill="none" stroke="var(--bot-eye-edge)" stroke-width="1" stroke-linecap="round"/><g class="seal-idle-note" fill="none" stroke="var(--bot-mid)" stroke-width="1.5" stroke-linecap="round"><path d="M55 18V8l6-1v9M55 9l6-1"/><ellipse cx="53.4" cy="18.5" rx="2" ry="1.3" fill="var(--bot-mid)"/><ellipse cx="59.4" cy="16.5" rx="2" ry="1.3" fill="var(--bot-mid)"/></g><path class="seal-idle-z" d="M53 12h6l-6 7h6m0-15h4l-4 5h4" fill="none" stroke="var(--bot-mid)" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/><g class="seal-idle-cloud" fill="var(--bot-mid)"><circle cx="54" cy="17" r="1.2"/><circle cx="59" cy="11" r="2.2"/></g><path class="seal-idle-star" d="m55 5 1.8 5.2L62 12l-5.2 1.8L55 19l-1.8-5.2L48 12l5.2-1.8Z" fill="var(--bot-mid)"/><g class="seal-done-star" fill="var(--bot-mid)"><path d="m55 5 1.8 5.2L62 12l-5.2 1.8L55 19l-1.8-5.2L48 12l5.2-1.8Z"/><path d="m10 16 .8 2.2L13 19l-2.2.8L10 22l-.8-2.2L7 19l2.2-.8Z"/></g><path class="seal-done-cheeks" d="m21 41 3 1m17-4 3-1" fill="none" stroke="var(--bot-eye-edge)" stroke-width="1.4" stroke-linecap="round"/><g class="seal-thought-marks" stroke="var(--bot-mid)" fill="none" stroke-width="2.4" stroke-linecap="round"><path d="m55 12 2-5m1.5 9 4-2"/></g><g class="seal-joy-marks" stroke="var(--bot-mid)" fill="none" stroke-width="1.8" stroke-linecap="round"><path d="m54 12 2-4m1 9 5-1M12 20l-3-2"/></g></svg></span>`;}

function avatar(b,state=b.state,extra=''){
 const uid='wax'+(++avatarCounter),preview=extra.includes('expression-preview');
 const waxKeys=Object.keys(WAX_MARKUP);
 const markup=(WAX_MARKUP[b.id]||WAX_MARKUP[waxKeys[Math.abs([...b.id].reduce((n,c)=>n+c.charCodeAt(0),0))%waxKeys.length]]).replace(/\bid="(wx\d+)"/g,(_,id)=>`id="${uid}-${id}"`).replace(/url\(#(wx\d+)\)/g,(_,id)=>`url(#${uid}-${id})`);
 const expression=preview?state:expressionFor(b);
 return `<span class="seal-avatar mk mk-wax ${extra}" data-bot-avatar="${b.id}" data-state="${waxState(state)}" data-expression="${expression}" ${!preview&&b.state==='idle'&&b.idle.active?`data-idle="${b.idle.active}"`:[]} data-selected="${b.id===selectedBot}" style="${paletteStyle(b)};${orbitStyle(b)};--phase:${b.phase}s;--breathe:${5.7+avatarCounter%4*.6}s" aria-hidden="true"><span class="seal-motion">${markup}${sealFace(uid)}</span></span>`;
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
 clearTimeout(musicTimers.get(id));
 $$('.seal-avatar[data-bot-avatar="'+id+'"]:not(.expression-preview)').forEach(a=>{delete a.dataset.streaming;['--note-a-y','--note-b-y','--note-a-alpha','--note-b-alpha'].forEach(p=>a.style.removeProperty(p));a.style.removeProperty('--writing-gaze');});
}
// Called by the same text-delta callback that reveals characters, never by an ambient animation.
function notifyText(id,chunk){
 const b=getBot(id);if(!b||b.state!=='writing'||!String(chunk||'').trim())return false;
 clearTimeout(musicTimers.get(id));const seed=[...String(chunk)].reduce((n,c)=>n+c.charCodeAt(0),0);
 $$('.seal-avatar[data-bot-avatar="'+id+'"]:not(.expression-preview)').forEach(a=>{
  a.dataset.streaming='true';a.style.setProperty('--writing-gaze',((seed%9)/4-1)+'px');
  a.style.setProperty('--note-a-y',(-.4-(seed%6)*.38).toFixed(2)+'px');a.style.setProperty('--note-b-y',(-.2-((seed*3)%7)*.3).toFixed(2)+'px');a.style.setProperty('--note-a-alpha',(.6+(seed%4)*.1).toFixed(2));a.style.setProperty('--note-b-alpha',(.6+((seed+2)%4)*.1).toFixed(2));
 });
 musicTimers.set(id,setTimeout(()=>clearMusic(id),150));return true;
}
