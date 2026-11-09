// Peeking Character Medallion Avatar engine: archetypes, cream paper well, 2.5D rig, states & orbit.
'use strict';
const states={
 idle:['Nghỉ','Sẵn sàng','Thở nhẹ · chớp mắt'],
 waiting:['Đang chờ','Chờ bạn','Nhìn xuống · nhô thấp'],
 thinking:['Đang nghĩ','Đang suy nghĩ','Nghiêng đầu · mắt nhìn lên'],
 writing:['Đang viết','Đang viết','Ghi chép theo nhịp chữ'],
 working:['Chạy việc','Đang làm việc','Đạo cụ nhún nhịp · xoay viền'],
 done:['Xong','Đã hoàn tất','Mắt cười ^ ^ · sao lóe sáng ✦'],
 error:['Lỗi','Có lỗi xảy ra','Mắt X · lắc nhẹ, viền đỏ'],
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
 idle:'Chớp mắt theo nhịp riêng, thở rất nhẹ trong lòng huy chương tròn.',
 waiting:'Nhô thấp trong huy chương, ánh mắt nhìn xuống ô nhập.',
 thinking:'Nghiêng đầu suy nghĩ, ánh mắt nhìn lên, cung tròn xoay từng nấc.',
 writing:'Ánh mắt đọc qua trang giấy, đạo cụ nhún nhịp theo chữ mới.',
 working:'Nhún nhịp làm việc, đạo cụ cử động liên tục, cung tròn viền xoay nhanh.',
 done:'Nảy vui mừng, mắt cười cong và sao vàng lóe sáng; sau 1,6 giây bot tự về nghỉ.',
 error:'Lắc nhẹ, mắt chữ X và viền đỏ báo lỗi để bạn kịp nhận biết.',
 offline:'Nhắm mắt, dừng chuyển động, màu nhạt đi khi tạm ngắt kết nối.',
 mention:'Mắt mở to, giật mình nhẹ trong 0,5 giây, rồi tiếp tục trạng thái trước đó.',
 confused:'Nghiêng đầu, mắt nhìn chéo, chờ bạn làm rõ nội dung.',
 group:'Cung tròn trên viền dùng chung một nhịp. Người đang nói sáng lên; các bot còn lại lắng nghe.'
};
const stateTimers=new Map(),mentionTimers=new Map(),musicBoxes=new Map();
const orbitDurations={thinking:9600,writing:6400,working:1500,group:4200};

// 12 Peeking Character Medallion Archetypes
const ARCHETYPES=[
 {
  id:'thyca',
  name:'Thyca',
  desc:'Khối vuông bo nghiêng · Ghi sổ & bút máy',
  hex:'#a65a42',
  eyeStyle:'light',
  role:'Trợ lý chính · Điều phối',
  renderChar:()=>({
   body:`<rect class="drop-body" x="14" y="21" width="38" height="44" rx="11" transform="rotate(-14 33 43)" fill="url(#__GRAD__)"/>`,
   eyes:`<ellipse cx="26" cy="34.5" rx="2.7" ry="4.6" transform="rotate(-14 26 34.5)" fill="var(--bot-eye)"/><ellipse cx="36.5" cy="32" rx="2.7" ry="4.6" transform="rotate(-14 36.5 32)" fill="var(--bot-eye)"/>`,
   prop:`<g class="prop-anim"><path d="M36.5 43.2L45.2 34.5L47.3 36.6L38.6 45.3Z" fill="#eab543" stroke="#4a3514" stroke-width=".75" stroke-linejoin="round"/><path d="M36.5 43.2L38.6 45.3L34.8 46.9Z" fill="#f7e3b5" stroke="#4a3514" stroke-width=".65" stroke-linejoin="round"/><path d="M35.6 45L36.6 46L34.8 46.9Z" fill="#2c2216"/><ellipse cx="43.8" cy="39.5" rx="3" ry="2.2" transform="rotate(-22 43.8 39.5)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".85"/><path d="M21.2 44.8L33 42.6L44.2 39.8L45 43.2L33.4 45.8L21.8 48Z" fill="#fffdf8" stroke="var(--bot-dark)" stroke-width=".75" stroke-linejoin="round"/><path d="M21.5 46.8L33.2 44.5L35.4 57.2L23.7 59.5Z" fill="#78422b" stroke="#381b0f" stroke-width=".85" stroke-linejoin="round"/><path d="M33.2 44.5L44.8 41.7L47 54.4L35.4 57.2Z" fill="#5e311e" stroke="#381b0f" stroke-width=".85" stroke-linejoin="round"/><path d="M33.2 44.5L35.4 57.2" stroke="#381b0f" stroke-width="1.2" stroke-linecap="round"/><rect class="fine-detail" x="37.2" y="45.8" width="4.8" height="3.2" rx=".7" transform="rotate(-14 39.6 47.4)" fill="#e5b94c"/><ellipse cx="22.2" cy="52.5" rx="3.1" ry="2.3" transform="rotate(-14 22.2 52.5)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".85"/></g>`
  })
 },
 {
  id:'researcher',
  name:'Researcher',
  desc:'Khối tròn đeo kính gọng vàng · Đọc tài liệu',
  hex:'#6f7f57',
  eyeStyle:'light',
  role:'Tra cứu & Tổng hợp',
  renderChar:()=>({
   body:`<circle class="drop-body" cx="28" cy="43" r="22.5" fill="url(#__GRAD__)"/>`,
   headgear:`<g stroke="#c48d2c" stroke-width="1.6" fill="#fffdf640" stroke-linecap="round"><rect x="15" y="18.5" width="11.5" height="7.5" rx="2.5" transform="rotate(-10 20.5 22)"/><rect x="30" y="16.2" width="11.5" height="7.5" rx="2.5" transform="rotate(-10 35.5 20)"/><path d="M26.5 21.5L30 20.8M15 22.5L9.5 29M41.5 19.5L44 25"/></g>`,
   eyes:`<ellipse cx="24" cy="36.5" rx="2.8" ry="4.5" fill="var(--bot-eye)"/><ellipse cx="34.5" cy="36" rx="2.8" ry="4.5" fill="var(--bot-eye)"/>`,
   prop:`<g class="prop-anim"><path d="M27.2 43.2L45.8 39.8L43.5 58L25 60Z" fill="#fffdf8" stroke="var(--bot-dark)" stroke-width=".75"/><path d="M28.8 45.2L47.8 41.8L45 60.5L26 62.5Z" fill="#d9c6a3" stroke="var(--bot-dark)" stroke-width=".85"/><rect class="fine-detail" x="34" y="46.5" width="8" height="3.2" rx=".8" transform="rotate(-10 38 48)" fill="#fffdf8" stroke="var(--bot-dark)" stroke-width=".5"/><ellipse cx="27.2" cy="53.5" rx="2.9" ry="2.1" transform="rotate(-10 27.2 53.5)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".85"/></g>`
  })
 },
 {
  id:'coder',
  name:'Coder',
  desc:'Viên nang nằm ngang · Sticky note hoàn tất ✓',
  hex:'#2f6b52',
  eyeStyle:'dark',
  role:'Kiểm thử & Thực thi mã',
  renderChar:()=>({
   body:`<rect class="drop-body" x="-4" y="27" width="54" height="34" rx="17" transform="rotate(-11 23 44)" fill="url(#__GRAD__)"/>`,
   eyes:`<rect x="11" y="34" width="4.8" height="11.5" rx="2.4" transform="rotate(-11 13.4 39.5)" fill="var(--bot-pupil)"/><rect x="21" y="32" width="4.8" height="11.5" rx="2.4" transform="rotate(-11 23.4 37.5)" fill="var(--bot-pupil)"/>`,
   prop:`<g class="prop-anim"><path d="M26 45L40 40.5L44.5 53L30.5 57.5Z" fill="#f2d16b" stroke="#5c4918" stroke-width=".8"/><path d="M27.5 47L41.5 42.5L46 55L32 59.5Z" fill="#fae28c" stroke="#5c4918" stroke-width=".8"/><path d="M33.2 50.2L36.2 53L41.2 46.5" fill="none" stroke="#2c2416" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></g>`
  })
 },
 {
  id:'march7th',
  name:'March 7th',
  desc:'Đám mây bồng bềnh · Nắng sớm & tách cà phê',
  hex:'#d98a54',
  eyeStyle:'dark',
  role:'Đồng hành sáng tạo',
  renderChar:()=>({
   headgear:`<g fill="#e89b32" stroke="#b56714" stroke-width=".6"><circle cx="13" cy="13" r="8.5"/><g stroke="#d98622" stroke-width="2.1" stroke-linecap="round"><path d="M24 9.5l3.5-1.2M23 15.5l3.5.8M19.5 20.5l2.2 2.8M13.5 23.5l.4 3.6M7.5 22.5l-1.5 3.2"/></g></g>`,
   body:`<path class="drop-body" d="M14 58C8 54 7.5 44 14 39C14.5 31 22 26.5 29 29.5C33.5 22.5 44.5 23.5 48.5 31C55.5 32.5 59 41 55 48.5C57 54.5 52 61 44 62C34 63.5 21 62 14 58Z" fill="url(#__GRAD__)"/>`,
   eyes:`<ellipse cx="32.5" cy="39.5" rx="2.4" ry="4.3" transform="rotate(-15 32.5 39.5)" fill="var(--bot-pupil)"/><ellipse cx="41" cy="37.2" rx="2.4" ry="4.3" transform="rotate(-15 41 37.2)" fill="var(--bot-pupil)"/>`,
   prop:`<g class="prop-anim"><path d="M39 49.5L49 46.5L51 54.5C51.5 57 49.5 59 46.5 59.8C43.5 60.5 41 59.2 40.2 56.8Z" fill="#fffdf8" stroke="#6e482b" stroke-width=".8"/><ellipse cx="44" cy="48" rx="5.1" ry="1.8" transform="rotate(-16 44 48)" fill="#c9854e" stroke="#6e482b" stroke-width=".7"/><path d="M49.5 48.5C52 48 53 51.5 50.5 52.8" fill="none" stroke="#fffdf8" stroke-width="1.6" stroke-linecap="round"/></g>`
  })
 },
 {
  id:'chronicler',
  name:'Chronicler',
  desc:'Dáng cao đội mũ Beret · Đọc nhật báo',
  hex:'#c7922e',
  eyeStyle:'dark',
  role:'Điểm tin & Nhật ký',
  renderChar:()=>({
   body:`<path class="drop-body" d="M16 61L21 24C22.5 16 41.5 16 43 24L47 61Z" fill="url(#__GRAD__)"/>`,
   headgear:`<path d="M20 20.5C19 14.5 28 11.5 36.5 13C44.5 14.5 48.5 19 45.5 23C41 21 26 20 20 20.5Z" fill="var(--bot-dark)" stroke="var(--bot-ink)" stroke-width=".8"/><ellipse cx="32.5" cy="21" rx="13" ry="2.8" transform="rotate(8 32.5 21)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".6"/>`,
   eyes:`<rect x="27" y="25.5" width="2.8" height="7.5" rx="1.4" transform="rotate(10 28.4 29)" fill="var(--bot-pupil)"/><rect x="34" y="26.8" width="2.8" height="7.5" rx="1.4" transform="rotate(10 35.4 30)" fill="var(--bot-pupil)"/>`,
   prop:`<g class="prop-anim"><path d="M18 37L30 40.5L28.5 54L16.5 50.5Z" fill="#f7f0df" stroke="var(--bot-dark)" stroke-width=".75"/><path d="M30 40.5L42 38.5L40.5 52L28.5 54Z" fill="#ebe1ca" stroke="var(--bot-dark)" stroke-width=".75"/><rect class="fine-detail" x="31.8" y="41.5" width="7.2" height="4.8" fill="#8c7d62"/><path class="fine-detail" d="M20 41l7 2M19.5 44l7 2M19 47l7 2M31.5 49l7-1" stroke="#8c7d62" stroke-width="1.1" stroke-linecap="round"/><ellipse cx="17.5" cy="45.5" rx="2.7" ry="2" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/><ellipse cx="41.2" cy="46.2" rx="2.7" ry="2" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 },
 {
  id:'scribe',
  name:'Scribe',
  desc:'Khối chóp mềm · Ôm sổ tay bìa cứng',
  hex:'#765898',
  eyeStyle:'light',
  role:'Soạn thảo & Biên tập',
  renderChar:()=>({
   body:`<path class="drop-body" d="M27 11.5C30 6.5 35 6.5 38 11.5L58 55C60 59 56 63 50 63H15C9 63 5 59 7 55Z" fill="url(#__GRAD__)"/>`,
   eyes:`<rect x="25.5" y="25" width="3.8" height="9" rx="1.9" transform="rotate(-5 27.4 29.5)" fill="var(--bot-eye)"/><rect x="33.5" y="24.2" width="3.8" height="9" rx="1.9" transform="rotate(-5 35.4 28.7)" fill="var(--bot-eye)"/>`,
   prop:`<g class="prop-anim"><path d="M34.5 41.2L43 33.4L44.9 35.4L36.4 43.2Z" fill="#e5b94c" stroke="#4a3514" stroke-width=".7" stroke-linejoin="round"/><path d="M34.5 41.2L36.4 43.2L32.6 44.8Z" fill="#f7e3b5" stroke="#4a3514" stroke-width=".6" stroke-linejoin="round"/><path d="M33.6 43L34.5 43.9L32.6 44.8Z" fill="#2c2216"/><ellipse cx="41.8" cy="38.2" rx="3" ry="2.2" transform="rotate(-18 41.8 38.2)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".85"/><rect x="25.8" y="38.2" width="14" height="16.5" rx="2" transform="rotate(10 32.8 46.5)" fill="#fffdf8" stroke="var(--bot-dark)" stroke-width=".7"/><rect x="24.8" y="40.5" width="14.5" height="16.8" rx="2.2" transform="rotate(10 32 48.9)" fill="#522947" stroke="#291222" stroke-width=".85"/><g fill="none" stroke="#e5b94c" stroke-width="1.2" stroke-linecap="round"><path d="M24.8 43l2.4.4M24.2 46.2l2.4.4M23.6 49.4l2.4.4"/></g><rect class="fine-detail" x="30.2" y="44.2" width="5.2" height="3" rx=".6" transform="rotate(10 32.8 45.7)" fill="#e5b94c"/><ellipse cx="23.8" cy="50.2" rx="3" ry="2.2" transform="rotate(10 23.8 50.2)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".85"/></g>`
  })
 },
 {
  id:'keeper',
  name:'Keeper',
  desc:'Giọt nhọn cổ điển · Cầm chổi dọn dẹp dữ liệu',
  hex:'#d4742c',
  eyeStyle:'dark',
  role:'Dọn dẹp & Bảo trì bộ nhớ',
  renderChar:()=>({
   body:`<path class="drop-body" d="M40 13C44 23 55 34 54.5 46.5C54 57 44 63 33 62.5C22 62 16.5 54 18.5 43.5C20.5 33 34 22 40 13Z" fill="url(#__GRAD__)"/>`,
   eyes:`<ellipse cx="31.5" cy="42.5" rx="2.7" ry="4.7" transform="rotate(8 31.5 42.5)" fill="var(--bot-pupil)"/><ellipse cx="41.5" cy="43.8" rx="2.7" ry="4.7" transform="rotate(8 41.5 43.8)" fill="var(--bot-pupil)"/>`,
   prop:`<g class="prop-anim"><path d="M13.5 23L18.5 49" stroke="#9e6332" stroke-width="2.2" stroke-linecap="round"/><path d="M14.5 48.5L21.5 47.5L24.5 60L12.5 60.5Z" fill="#eab646" stroke="#7d5318" stroke-width=".75"/><ellipse cx="16.5" cy="36.5" rx="2.8" ry="2.2" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/><ellipse cx="17.8" cy="42.2" rx="2.6" ry="2.0" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 },
 {
  id:'scout',
  name:'Scout',
  desc:'Khối đa giác bo · Kính râm & mảnh ghép',
  hex:'#c9564c',
  eyeStyle:'light',
  role:'Thám sát & Gỡ rối',
  renderChar:()=>({
   body:`<path class="drop-body" d="M17 31C21 24 33 21 41 25C49 29 56 38 53 49C50 59 38 63 26 62C14 61 9 52 11 42C12.5 36 14.5 34 17 31Z" fill="url(#__GRAD__)"/>`,
   headgear:`<g transform="rotate(18 35 28.5)"><path d="M20.5 28.5l3.2-.4M34.8 28h2.4M47.8 28.2l3.4.8" stroke="#d8b26e" stroke-width="1.6" stroke-linecap="round"/><ellipse cx="29.2" cy="28.5" rx="5.5" ry="3.6" fill="#231f1d" stroke="#d8b26e" stroke-width="1.35"/><ellipse cx="42.4" cy="28.5" rx="5.5" ry="3.6" fill="#231f1d" stroke="#d8b26e" stroke-width="1.35"/><path d="M26.8 27.2l2.2-1.1M40 27.2l2.2-1.1" stroke="#fff9ea99" stroke-width="1" stroke-linecap="round"/></g>`,
   eyes:`<ellipse cx="27" cy="40.5" rx="3.1" ry="5.1" transform="rotate(16 27 40.5)" fill="var(--bot-eye)"/><ellipse cx="37.5" cy="44" rx="3.1" ry="5.1" transform="rotate(16 37.5 44)" fill="var(--bot-eye)"/>`,
   prop:`<g class="prop-anim"><path d="M13 48h5a2 2 0 1 1 3 0h4v5a2 2 0 1 1 0 3v4H13v-4a2 2 0 1 0 0-3Z" transform="rotate(-15 19 54)" fill="#e3a84b" stroke="var(--bot-dark)" stroke-width=".85" stroke-linejoin="round"/><ellipse cx="14.5" cy="54" rx="2.6" ry="2" transform="rotate(-15 14.5 54)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 },
 {
  id:'designer',
  name:'Designer',
  desc:'Khối bo nghiêng · Cọ vẽ & bố cục Layout',
  hex:'#b5456e',
  eyeStyle:'light',
  role:'Thiết kế giao diện & Thẩm mỹ',
  renderChar:()=>({
   body:`<rect class="drop-body" x="11" y="25" width="38" height="40" rx="11" transform="rotate(-16 30 45)" fill="url(#__GRAD__)"/>`,
   eyes:`<rect x="21" y="34" width="3.8" height="8.5" rx="1.9" transform="rotate(-16 23 38)" fill="var(--bot-eye)"/><rect x="30" y="31.5" width="3.8" height="8.5" rx="1.9" transform="rotate(-16 32 35.5)" fill="var(--bot-eye)"/>`,
   prop:`<g class="prop-anim"><path d="M22 46.5L40.5 41.5L44.5 58L26 62.5Z" fill="#f7f0e2" stroke="var(--bot-dark)" stroke-width=".75"/><rect class="fine-detail" x="25" y="46" width="14" height="4" transform="rotate(-15 32 48)" fill="#8c7f6a"/><rect class="fine-detail" x="26.5" y="52" width="6.5" height="6" transform="rotate(-15 29.5 55)" fill="#8c7f6a"/><path d="M14.5 44.5L21.5 57.5" stroke="#423730" stroke-width="2.4" stroke-linecap="round"/><path d="M13.8 43.2L15.6 46.2" stroke="#d8d2c5" stroke-width="2.4" stroke-linecap="round"/><path d="M11.8 40C14.2 40.2 15.8 42.2 14.8 44.5C12.8 45 11.2 43 11.8 40Z" fill="#f0a8c0" stroke="#4d2635" stroke-width=".6"/><ellipse cx="18.2" cy="51.2" rx="2.7" ry="2" transform="rotate(-20 18.2 51.2)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/><ellipse cx="42.5" cy="49.5" rx="2.7" ry="2" transform="rotate(-15 42.5 49.5)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 },
 {
  id:'analyst',
  name:'Analyst',
  desc:'Đầu tròn tai cụp · Biểu đồ tăng trưởng ↗',
  hex:'#7d5638',
  eyeStyle:'light',
  role:'Thống kê Usage & Chi phí',
  renderChar:()=>({
   ears:`<ellipse cx="14.5" cy="38.5" rx="4.8" ry="9.5" transform="rotate(28 14.5 38.5)" fill="var(--bot-dark)" stroke="var(--bot-ink)" stroke-width=".75"/><ellipse cx="46.5" cy="34" rx="4.8" ry="9.5" transform="rotate(-32 46.5 34)" fill="var(--bot-dark)" stroke="var(--bot-ink)" stroke-width=".75"/>`,
   body:`<circle class="drop-body" cx="31" cy="44" r="19.5" fill="url(#__GRAD__)"/>`,
   eyes:`<ellipse cx="24.5" cy="41.5" rx="2.9" ry="4.7" transform="rotate(-12 24.5 41.5)" fill="var(--bot-eye)"/><ellipse cx="35.5" cy="39.5" rx="2.9" ry="4.7" transform="rotate(-12 35.5 39.5)" fill="var(--bot-eye)"/>`,
   prop:`<g class="prop-anim"><path d="M27 48.5L43.5 46L45 59.5L28.5 62Z" fill="#f7eedb" stroke="var(--bot-dark)" stroke-width=".75"/><path d="M30.5 57.5L35 53.5L37.5 55L42 49.5M42 49.5L38.5 49.8M42 49.5L41.8 53" fill="none" stroke="#4f8248" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><ellipse cx="27.5" cy="55" rx="2.6" ry="1.9" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/><ellipse cx="44.2" cy="52.5" rx="2.6" ry="1.9" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 },
 {
  id:'operator',
  name:'Operator',
  desc:'Dáng tròn nghiêng · Xách hộp đồ nghề đỏ',
  hex:'#8c7aa8',
  eyeStyle:'dark',
  role:'Vận hành Tool & Hệ thống',
  renderChar:()=>({
   body:`<path class="drop-body" d="M20 59C17 44 23 28 36 27C48 26 56 38 55 55L54 62H20Z" fill="url(#__GRAD__)"/>`,
   eyes:`<ellipse cx="31.5" cy="40.5" rx="2.6" ry="4.6" transform="rotate(-8 31.5 40.5)" fill="var(--bot-pupil)"/><ellipse cx="41.5" cy="39" rx="2.6" ry="4.6" transform="rotate(-8 41.5 39)" fill="var(--bot-pupil)"/>`,
   prop:`<g class="prop-anim"><path d="M18.5 48.5C19 45.5 24.5 46.5 24.5 49.5" fill="none" stroke="#b84339" stroke-width="1.8" stroke-linecap="round"/><rect x="13" y="48.5" width="16.5" height="10.5" rx="2" transform="rotate(11 21 53.5)" fill="#c94a3f" stroke="#691f19" stroke-width=".75"/><line x1="13" y1="52.5" x2="29" y2="55.5" stroke="#852720" stroke-width="1.2"/><ellipse cx="22.2" cy="47.2" rx="2.6" ry="1.9" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 },
 {
  id:'courier',
  name:'Courier',
  desc:'Dáng cao xanh cổ điển · Mũ cap & Bản tin',
  hex:'#4c7c96',
  eyeStyle:'dark',
  role:'Giao nhận A2A & Tin nhắn',
  renderChar:()=>({
   body:`<path class="drop-body" d="M18 61L24 25C26 17 44 18 45.5 26L48 61Z" fill="url(#__GRAD__)"/>`,
   headgear:`<path d="M23 21.5C23 15.5 32 13 40.5 15C48 17 51 22 47.5 25.5C43 23 29 21 23 21.5Z" fill="var(--bot-dark)" stroke="var(--bot-ink)" stroke-width=".8"/><ellipse cx="35" cy="22.5" rx="13" ry="2.8" transform="rotate(12 35 22.5)" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".6"/>`,
   eyes:`<rect x="30" y="27" width="2.7" height="7.2" rx="1.35" transform="rotate(11 31.3 30.6)" fill="var(--bot-pupil)"/><rect x="36.8" y="28.4" width="2.7" height="7.2" rx="1.35" transform="rotate(11 38.1 32)" fill="var(--bot-pupil)"/>`,
   prop:`<g class="prop-anim"><path d="M20 39L32 42.5L30.5 55.5L18.5 52Z" fill="#f7f0df" stroke="var(--bot-dark)" stroke-width=".75"/><path d="M32 42.5L44 40.5L42.5 53.5L30.5 55.5Z" fill="#e6dcc6" stroke="var(--bot-dark)" stroke-width=".75"/><path class="fine-detail" d="M22 43l7 2M21.5 46l7 2M33.5 45l7-1M33 48l7-1" stroke="#7d8c94" stroke-width="1.1" stroke-linecap="round"/><ellipse cx="19.5" cy="47" rx="2.6" ry="1.9" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/><ellipse cx="43.2" cy="48" rx="2.6" ry="1.9" fill="var(--bot-color)" stroke="var(--bot-dark)" stroke-width=".8"/></g>`
  })
 }
];

const DROP_NAMES=['thyca','researcher','coder','march7th','chronicler','scribe','keeper','scout','designer','analyst','operator','courier'];
const ARCHETYPE_BY_ID=Object.fromEntries(ARCHETYPES.map(a=>[a.id,a]));

function dropShapeFor(b){
 if(b&&b.shape&&ARCHETYPE_BY_ID[b.shape])return {...ARCHETYPE_BY_ID[b.shape],name:b.shape};
 if(b&&b.id&&ARCHETYPE_BY_ID[b.id])return {...ARCHETYPE_BY_ID[b.id],name:b.id};
 const str=String(b?.id||'thyca');
 const i=Math.abs([...str].reduce((n,c)=>n+c.charCodeAt(0),0))%ARCHETYPES.length;
 const arch=ARCHETYPES[i];
 return {...arch,name:arch.id};
}

function dropMarkup(b,uid){
 const arch=dropShapeFor(b);
 const parts=arch.renderChar();
 const earsHtml=parts.ears||'';
 const bodyHtml=(parts.body||'').replace(/__GRAD__/g,`${uid}-grad`);
 const headgearHtml=parts.headgear||'';
 const propHtml=parts.prop||'';
 const eyesHtml=parts.eyes||'';
 const strokeTone=arch.eyeStyle==='dark'?'var(--bot-pupil)':'var(--bot-eye)';

 return `<svg class="base" viewBox="0 0 64 64"><defs><clipPath id="${uid}-clip"><circle cx="32" cy="32" r="28.2"/></clipPath><linearGradient id="${uid}-grad" x1="0.2" y1="0" x2="0.8" y2="1"><stop offset="0" style="stop-color:var(--bot-highlight)"/><stop offset="0.35" style="stop-color:var(--bot-color)"/><stop offset="1" style="stop-color:var(--bot-dark)"/></linearGradient></defs><circle class="well-bg" cx="32" cy="32" r="28.5"/><g class="char-clip" clip-path="url(#${uid}-clip)"><g class="char-rig"><g class="char-parallax">${earsHtml}${bodyHtml}${headgearHtml}<g class="eyes-rig"><g class="seal-pupils"><g class="eyes-open">${eyesHtml}</g><g class="eyes-happy" style="opacity:0" fill="none" stroke="${strokeTone}" stroke-width="2.6" stroke-linecap="round"><path d="M22.5 36.5Q26 30.5 29.5 36.5"/><path d="M33.5 35Q37 29 40.5 35"/></g><g class="eyes-error" style="opacity:0" fill="none" stroke="${strokeTone}" stroke-width="2.4" stroke-linecap="round"><path d="m23 32 6 6m0-6-6 6"/><path d="m34 31 6 6m0-6-6 6"/></g><g class="eyes-rest" style="opacity:0" fill="none" stroke="${strokeTone}" stroke-width="2.2" stroke-linecap="round"><path d="M22.5 36Q26 38.5 29.5 36"/><path d="M33.5 34.5Q37 37 40.5 34.5"/></g></g></g></g><g class="prop-parallax">${propHtml}</g></g><path class="done-spark" style="opacity:0" d="m14 14 1.4 4L19.4 19.4l-4 1.4L14 24.8l-1.4-4L8.6 19.4l4-1.4Z" fill="#e09b2d"/></g><circle class="frame-ring" cx="32" cy="32" r="28.5"/><circle class="frame-ring-hi" cx="32" cy="32" r="27.3"/><circle class="orbit-arc" cx="32" cy="32" r="28.5"/></svg>`;
}

const dropState=state=>stateAliases[state]||state;
function expressionFor(b){return b?.mentioned?'mention':(b?.state||'idle');}
function orbitAngle(b){
 const duration=orbitDurations[b.state];if(!duration)return b.orbitAngle||0;
 let turns=(Date.now()-(b.orbitAt||Date.now()))/duration;
 if(b.state==='thinking')turns=Math.floor(turns*12)/12;
 return ((b.orbitAngle||0)+turns*360)%360;
}
function orbitStyle(b){
 const angle=b?.orbitAngle||0;
 const at=b?.orbitAt||Date.now();
 return `--ring-start:${angle}deg;--ring-end:${angle+360}deg;--ring-delay:${-(Date.now()-at)/1000}s`;
}

function avatar(b,state=b.state,extra=''){
 const uid='drop'+(++avatarCounter),preview=extra.includes('expression-preview');
 const shape=dropShapeFor(b).name;
 const markup=dropMarkup(b,uid);
 const expression=preview?state:expressionFor(b);
 const pal=b?.palette||(b?.color?derivePalette(b.color):(ARCHETYPE_BY_ID[b?.id]?.hex?derivePalette(ARCHETYPE_BY_ID[b.id].hex):null));
 const pStyle=pal?Object.entries(pal).map(([k,v])=>'--bot-'+k+':'+v).join(';'):'';
 const breathe=5.7+avatarCounter%4*.6;
 return `<span class="seal-avatar mk mk-drop ${extra}" data-bot-avatar="${b.id}" data-drop-shape="${shape}" data-state="${dropState(state)}" data-expression="${expression}" ${!preview&&b.state==='idle'&&b.idle?.active?`data-idle="${b.idle.active}"`:''} data-selected="${b.id===selectedBot}" style="${pStyle};${orbitStyle(b)};--phase:${b.phase||0}s;--breathe:${breathe}s" aria-hidden="true"><span class="seal-motion">${markup}</span></span>`;
}
function syncAvatar(a,b){
 const changed=a.dataset.state!==b.state;
 a.dataset.state=b.state;a.dataset.expression=expressionFor(b);a.dataset.selected=b.id===selectedBot;
 if(changed){a.style.setProperty('--ring-start',(b.orbitAngle||0)+'deg');a.style.setProperty('--ring-end',((b.orbitAngle||0)+360)+'deg');a.style.setProperty('--ring-delay',-(Date.now()-(b.orbitAt||Date.now()))/1000+'s');a.classList.remove('blink');}
 if(['offline','error','done'].includes(b.state)||b.mentioned)a.classList.remove('blink');
 if(idleEligible(b)&&b.idle?.active)a.dataset.idle=b.idle.active;else delete a.dataset.idle;
}

function setState(id,state,summary,elapsed){
 const b=getBot(id);state=stateAliases[state]||state;if(!b||!states[state])return false;
 if(state==='mention')return mentionBot(id);
 if(state!=='idle')clearIdle(b);else if(b.state!=='idle'){clearIdle(b);if(b.idle)b.idle.since=Date.now();}
 if(['error','offline'].includes(state)&&lab.botId===id&&lab.mode)stopLab(false);
 if(['error','offline'].includes(state)){const t=[...runs.values()].find(x=>x.botId===id);if(t)cancelTurn(t,state==='offline'?'Kết nối đã ngắt. Phần đã viết được giữ lại.':'Lượt trả lời gặp lỗi. Bạn có thể thử lại.',state==='offline'?'mất kết nối':'gặp lỗi');}
 clearTimeout(stateTimers.get(id));clearTimeout(mentionTimers.get(id));b.mentioned=false;
 const changed=b.state!==state;const angle=orbitAngle(b);b.revision=(b.revision||0)+1;b.state=state;
 if(changed){b.orbitAngle=state==='waiting'&&angle===0?135:angle;b.orbitAt=Date.now();}
 if(summary!==undefined)b.summary=String(summary);else b.summary='';
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
 $$('.seal-avatar[data-bot-avatar="'+id+'"]:not(.expression-preview)').forEach(a=>{delete a.dataset.streaming;a.style.removeProperty('--writing-gaze');});
}
const MUSIC_BEAT_MS=200,MUSIC_DECAY=8,MUSIC_QUIET_BEATS=2;
function paintMusicBeat(id,seed){
 $$('.seal-avatar[data-bot-avatar="'+id+'"]:not(.expression-preview)').forEach(a=>{
  a.dataset.streaming='true';a.style.setProperty('--writing-gaze',((seed%9)/4-1)+'px');
 });
}
function musicBeat(id){
 const box=musicBoxes.get(id);if(!box)return;
 if(box.energy>0){box.energy=Math.max(0,box.energy-MUSIC_DECAY);paintMusicBeat(id,box.seed);box.quiet=0;}
 else if(++box.quiet>=MUSIC_QUIET_BEATS)clearMusic(id);
}
function notifyText(id,chunk){
 const b=getBot(id);if(!b||b.state!=='writing'||!String(chunk||'').trim())return false;
 const text=String(chunk),seed=[...text].reduce((n,c)=>n+c.charCodeAt(0),0);
 let box=musicBoxes.get(id);
 if(!box){box={energy:0,seed:0,quiet:0,timer:0};musicBoxes.set(id,box);}
 box.energy+=text.length;box.seed+=seed;box.quiet=0;
 if(!box.timer){paintMusicBeat(id,box.seed);box.timer=setInterval(()=>musicBeat(id),MUSIC_BEAT_MS);}
 return true;
}
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
