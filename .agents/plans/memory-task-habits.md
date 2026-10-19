---
status: in-progress
created: 2026-10-16
last_updated: 2026-10-19
---

# Memory task + habits — phase 1A backend (đã chốt) và các phase sau (mở)

## Quy ước đọc

- Mục "Quyết định đã chốt" = user đã duyệt, không bàn lại, phase 1A thi công theo.
- Mục "Phase sau (mở)" = chưa quyết chi tiết, chỉ ghi hướng để không quên.

## Quyết định đã chốt

- Memory giữ cơ chế log markdown theo ngày; Markdown là nguồn sự thật,
  SQLite là index derived. Metadata mới đi vào heading JSON
  (tiền lệ v6 `proj`/`chat`, v7 `bots`), không graph DB.
- Task dùng CHUNG một id cho một mục tiêu, nhiều bot đóng góp.
  Id task RIÊNG do hệ thống sinh (`task-` + 8 hex, `secrets.token_hex(4)`),
  không lấy id ký ức đầu, không slug do LLM đặt.
- `cont` = ký ức trước trong cùng task (memory session id).
- `status` open|done; trạng thái task = status mới nhất
  (theo timeline_day rồi line_start; không có status thì open).
  Chỉ điều phối/người mở mới đóng — quy tắc hành vi, ghi ở tool description.
- Ký ức thói quen gắn `kind="habit"` + nhãn thói quen rút gọn (`habit`).
  Mỗi bot tự ghi quan sát habit vào daily. Phần gom vào USER.md KHÔNG
  thuộc phase này (quy tắc gom đã chốt, xem phase sau).
- `related`: user đã chốt BỎ (thay bằng tra trùng → đề xuất team,
  xem phase sau). Phase này KHÔNG xoá `related_edges`/merge,
  chỉ không xây gì mới lên nó.

## Phase 1A này (in-progress)

Metadata end-to-end (đi đúng đường v6/v7):

- Heading JSON thêm 5 key khi có: `task` (`task-[0-9a-f]{8}`),
  `cont` (memory session id trước đó), `kind` (phase này chỉ `habit`),
  `status` (`open`|`done`), `habit` (nhãn rút gọn; bắt buộc khi
  kind=`habit`, cấm khi không có kind).
- Validation ở facade: task sai format → ValueError; status/cont chỉ hợp
  lệ khi có task; habit rỗng → lỗi. Ký ức cũ không key → None.
- Chunk/schema: bump v7→v8, migration non-destructive thêm 5 cột nullable
  + index trên `task`, đánh stale để reindex. Hit/store search mang trường mới.
- Writer: update/reinforce/promote giữ nguyên 5 trường khi rewrite heading.
- Sinh task id: helper cạnh `new_entry_id`; facade cấp id chưa trùng
  (kiểm tra store, retry).
- Facade + tools: `remember` nhận task/cont/kind/status/habit; tool
  `memory_remember` thêm `task`/`new_task`/`cont`/`kind`/`status`/`habit`,
  trả JSON `{"session_id","task_id"}`; `memory_search` thêm filter exact
  `task`/`kind`/`status`; `memory_update` sửa được 5 trường (omit = giữ);
  tool mới `memory_tasks(status?, date_from?, date_to?, limit?)` gom entry
  theo task (title = topic entry sớm nhất, status mới nhất, entry_count,
  first/last_day, bots union; sort last_day giảm dần; limit 20, clamp 1..50).
- Tests mới trong `tests/` theo pattern hiện có; không nới lỏng test cũ.

## Kiểm chứng Phase 1A (2026-10-16, nhánh feat/agent-status)

- Focused mới + memory/tool liên quan: `test_memory_task_habits.py` +
  `test_memory_{heading,tools,lifecycle,contract_fixes}.py` → 65 passed;
  `test_memory_{active,archived,fallback_safety,honesty,session_fixes,stats}.py` +
  `test_serve_memory_stats.py` + `test_webui_memory_edit.py` +
  `test_tool_{registry,gateway}.py` +
  `test_bots_{memory,merge,jobs,context,context_fixes}.py` + `test_cli.py`
  → 255 passed.
- Full `uv run pytest -q` → **1419 passed, 2 failed, 6 errors** (2m20s).
  Đối chiếu baseline trước sửa (1403 passed, 3 failed, 6 errors):
  fail/error còn lại y hệt baseline cũ, không liên quan memory —
  2 `test_serve_chat` (chuỗi `createLiveStatus` không còn trong app bundle)
  + 6 error `test_webui_concurrent_streams` (node harness exit 1);
  test config flaky theo thứ tự lần này pass.
- Test cũ cập nhật theo contract mới (không nới lỏng assertion):
  `test_memory_tools.py` + `test_memory_contract_fixes.py` parse JSON
  `{"session_id","task_id"}` từ `memory_remember`;
  `test_cli.py` tool count 14→15 (`memory_tasks` mới);
  `test_bots_context.py` budget 4000→4400 (schema tool mới tăng overhead
  đếm trong budget, đúng tiền lệ comment trong test).

## Phase 1B — gỡ related/merge, tra trùng → đề xuất team (done 2026-10-16)

- Bỏ `related`: xoá `thyca/memory/merge.py` (Union-Find, edges, hợp owners),
  store methods `add_related/related_of/all_related/drop_edges_of` +
  `prune_dangling_edges`, gọi `drop_edges_of` trong `facade.forget`, bảng
  `related_edges` trong `schema.sql`; mọi đường migration lên v8 DROP bảng
  này. Không Union-Find, không hợp owners, không bảng cạnh.
- Module mới `thyca/memory/dedupe.py`: `llm_review_pair` (giữ nguyên prompt/
  hành vi), `exact_duplicate_sessions` (so toàn văn session chuẩn hoá —
  không so từng leaf), `find_duplicate_candidates` (trùng cứng + lexical
  `facade.search(text[:500], limit=5)`), `pair_eligible` (cả hai daily,
  owners đều không team/legacy, không toàn-thyca, owner-set khác nhau),
  `propose_duplicates` (chạy quyền Thyca; trùng cứng đề xuất ngay, lexical
  chỉ khi verdict "trùng"; entry được đề xuất là bản cũ hơn; bot_id =
  owner đầu tiên sorted không phải thyca/team; bỏ qua khi đã có candidate
  bất kỳ trạng thái nào hoặc đã đề xuất trong pass). Không sửa/gộp/xoá
  entry, không đổi quyền đọc.
- Caller `chat_app.sweep_idle_sessions` dùng `dedupe.propose_duplicates`;
  report keys `duplicates_proposed` + `duplicate_reviews`.
- Tests: `tests/test_bots_merge.py` → `tests/test_memory_dedupe.py` (8 test);
  `test_bots_memory` bỏ test related, thay bằng forget-xoá-thường;
  migration v7-có-edge → v8 hết bảng graph, chunks giữ nguyên.
- Kiểm chứng: focused dedupe/memory/jobs + full suite (xem mục Rework).

## Phase 1C — extractor + <memory_tasks> (done 2026-10-16)

- `facade.tasks` thêm `latest_session_id` (entry mới nhất theo last_day,
  last_line, gồm cả entry hôm nay).
- `jobs.py`: `ExtractedMemory` +6 field (task/new_task-title/cont/kind/
  status/habit); `extract_from_transcript(..., open_tasks=())` với khối
  OPEN MEMORY TASKS + luật prompt (tái dùng id đang mở, new_task chỉ cho
  mục tiêu nhiều-bước mới, done chỉ khi cả mục tiêu xong, habit chỉ cho
  thói quen lặp lại + label ngôn ngữ gốc); `parse_extraction` sanitize
  từng field, validate tổ hợp hiệu dụng (new_task validate qua probe id),
  task+new_task cùng có thì bỏ cả hai; `apply_extracted(..., open_tasks=())`
  chỉ nhận task trong catalog, gom new_task theo title (strip + gộp
  whitespace + casefold, một id/nhóm, entry đầu default open), cont nối
  entry vừa ghi cùng task (rơi về latest_session_id khi thiếu).
- Caller: `_flush_bot_memory` lấy catalog trong bind_bot_id(bot) rồi truyền
  cả extract + apply; sweep room lấy catalog quyền Thyca.
- Active: `ActiveSnapshot.memory_tasks` + section `<memory_tasks>` sau
  `<open_tasks>`; helper `format_task_catalog` (tối đa 20 dòng, title ~80
  ký tự); wiring runtime callback `memory_task_index` (theo bot, tôn trọng
  memory.read), Thyca root qua `TurnScope.hot_memory_tasks`, CLI dùng một
  facade dùng chung mỗi lượt. Đếm token theo `build()` output nên tự nhiên.
- `rules.md`: hướng dẫn tái dùng id + memory_search filter task.
- Kiểm chứng: focused extractor/jobs/context/active/memory-task-habits xanh;
  full suite (xem dưới). Không habit sweep/USER.md ở phase này.

## Rework sau review (2026-10-16)

- Finding 1 (bug migration): nhánh `from_version == "6"` trong
  `schema.py` thiếu `add_task_columns(db)` → DB v6 lên thẳng 8 nhưng
  không có cột task, `CREATE INDEX chunks(task)` lỗi. Đã sửa + thêm test
  migration từ DB v6 (`_make_v6_db`: schema.sql trừ cột bots/task,
  không có `related_edges`, version='6').
- Finding 2 (catalog thiếu task hôm nay): `MemoryFacade.tasks` nay hợp
  nhất thêm entry task trong file daily hiện tại (parse qua chunker, chỉ
  daily, bỏ entry hết hạn, lọc reader đúng quy tắc store, bỏ trùng
  session_id store đã có). Thêm 3 test: thấy task cùng ngày, không đếm
  đôi sau qua ngày + reindex, bot con khác không thấy task hôm nay.
- Finding 3: plan mục phase sau viết lại theo chốt mới (related đã chốt
  bỏ; habit sweep 3 lần/3 ngày → USER.md bot, ≥2 bot → USER.md `## Habits`,
  "thường/luôn" 1 lần đủ; kênh dm/group + session nội bộ là chi tiết kỹ
  thuật; group context công khai + cursor; identity twin + hướng gia cố
  snapshot; bot nào cũng mở được task, bot mở tạm điều phối).
- Full `uv run pytest -q` sau rework → **1423 passed, 2 failed, 6 errors**
  (2m11s). Fail/error y hệt baseline Phase 1A (1419 passed, 2 failed,
  6 errors) và baseline gốc — toàn bộ là fail cũ webui streaming
  (2 `test_serve_chat` createLiveStatus + 6 error concurrent_streams).
  Không fail mới.

## Phase 1B kiểm chứng (2026-10-16)

- Full `uv run pytest -q` → **1426 passed, 2 failed, 6 errors** (2m05s).
  Fail/error y hệt mọi baseline trước (webui streaming cũ). Ròng +3 passed
  (8 test dedupe mới thay 5 test merge cũ). Không fail mới.

## Phase 1E — habit sweep cấp từng bot (done 2026-10-16, chưa global aggregation)

- `habit_explicit` end-to-end trong v8 (không bump v9): heading JSON chỉ ghi
  khi true; HeadingMeta/Chunk bool default False; SQLite
  `habit_explicit INTEGER NOT NULL DEFAULT 0`; Hit mang field; validate chỉ
  cho true khi kind=habit (+label); đọc lenient (lạ → False). Facade
  remember/update, writer rewrite-giữ, tool param boolean + description
  ("tôi thường/luôn/thích"), extractor prompt/parse/apply.
- `init_schema` gọi `ensure_v8_columns` vô điều kiện (theo tiền lệ index
  bots/task): DB v8-bản-1A thiếu cột vẫn lên được mà không cần bump version.
- Module mới `thyca/memory/habits.py`: `habit_observations` (store habit_rows
  + gộp file hôm nay, đúng visibility reader như tasks); `sweep_bot_habits`
  gom theo `Chunker.normalize(label)`, đủ điều kiện khi có 1 explicit HOẶC
  ≥3 ngày khác nhau, label hiển thị = raw label mới nhất; ghi APPEND trong
  block `habits:auto:start/end` của `bots/<id>/USER.md` (frame mới có comment
  độ-tin-thấp, file thiếu block thì thêm cuối, atomic + 0600, idempotent,
  không xoá dòng cũ).
- Trigger: `_flush_bot_memory` sau apply gọi sweep với bind_bot_id(bot),
  best-effort không hỏng flush.
- Prompt: `ActiveSnapshot.bot_user` + section `<bot_user>` sau `<user>`;
  runtime đọc `BotRegistry.bot_user_text(card)` khi memory.read (Thyca qua
  runtime cũng đọc bots/thyca); Thyca root qua `TurnScope.hot_bot_user`;
  CLI replace mỗi lượt bằng registry dùng chung. Đếm token qua build().
- Kiểm chứng: xem dưới. Global aggregation ≥2 bot vẫn là phase sau.

## Phase 1D — gia cố identity twin ở nguồn (done 2026-10-16)

- `ActiveMemory._snapshot` chỉ nạp IDENTITY.md khi reader None/"thyca";
  bot con nhận identity="" ngay từ nguồn (áp cho cả refresh/peek).
  Runtime vẫn replace `identity_text(card)` cho bot con (phòng thủ 2 lớp).
- Caller refresh/peek (run_turn, cli, chat_app meter) đều reader None →
  giữ nguyên; meter đo đúng prompt bot con thật nhận.
- Tests: snapshot reader="coder" → identity rỗng + build() sạch marker;
  reader None/"thyca" → giữ identity; 2 test identity bot con cũ xanh.
- Kiểm chứng: focused active/context/cli/task-habits/jobs xanh;
  full `uv run pytest -q` → **1441 passed, 2 failed, 6 errors** (2m08s).
  Fail/error y hệt mọi baseline (webui streaming cũ). Ròng +2 (test 1D).
  Không fail mới.

## Phase 1C kiểm chứng (2026-10-16)

- Focused (jobs/context/memory-task-habits/dedupe/cli/active/loop): xanh.
- Full `uv run pytest -q` → **1439 passed, 2 failed, 6 errors** (2m08s).
  Fail/error y hệt mọi baseline (webui streaming cũ). Ròng +13 (8 jobs +
  4 context + 1 latest_session_id). Không cập nhật test count/budget nào
  thêm (rules.md +1 dòng vừa trong slack). Không fail mới.

## Phase 1F — Thyca tổng hợp habit lên USER.md chung (done 2026-10-16)

- `habits.parse_bot_habits` (raw label trong auto-block) +
  `aggregate_team_habits` (gom theo normalize, ≥2 bot, label từ bot_id
  sorted nhỏ nhất; chỉ viết trong block `habits:auto:*` của `## Habits`;
  thiếu mục thì thêm cuối kèm comment, có mục thiếu block thì chèn dưới
  heading giữ bullet thủ công, thiếu file thì tạo tối thiểu; chống trùng
  cả auto lẫn bullet thủ công; append-only, atomic 0600, idempotent).
- Seed `user.md` thêm mục `## Habits` rỗng + comment + markers (đặt trước
  `## Name` để không phá test profile-emptiness).
- Trigger trong `_flush_bot_memory` sau sweep, best-effort.
- Không thêm section prompt mới: USER.md chung đi qua `<user>` như cũ.
- Kiểm chứng: xem dưới.

## Phase 1E kiểm chứng (2026-10-16)

- Focused (habits/task-habits/jobs/context/active/cli/registry): xanh.
- Sửa thêm 1 flake có sẵn lộ trong lượt full: test dedupe review dùng chung
  tmp cho 3 vòng lặp (limit search + chunk_id ngẫu nhiên) → cô lập tmp mỗi
  vòng, không nới assertion; 10/10 xanh.
- Full `uv run pytest -q` → **1456 passed, 2 failed, 6 errors** (2m08s).
  Fail/error y hệt baseline (webui streaming cũ). Ròng +15 (11 habits +
  4 context prompt/flush). Không fail mới.

## Review độc lập + rework (Approve with fixes, 2026-10-16)

- F1 ownership sweep: `habit_observations` lọc tường minh theo bot_id
  (chỉ owners chứa bot; loại None/team-only); co-owned entry đếm cho cả hai
  (ghi trong docstring/test: đồng sở hữu ≠ hai quan sát độc lập — nhưng quy
  tắc user đếm bot giữ habit nên vẫn cho qua, không dừng).
- F2 label sanitize: validate reject \n/\r/"<!--", collapse whitespace
  khi ghi (facade); extractor bỏ cụm field phụ theo cơ chế cũ; sweep/
  aggregate/parse bỏ qua label chứa marker.
- F3 memory_tasks vào BASELINE_BOT_TOOLS + MEMORY_READ_TOOLS (read=false
  chặn, hidden thắng); budget test 4400→4800, cli count không đổi (15).
- F4 lock: sweep bọc `facade.writer.lock_for(path)`; aggregate nhận thêm
  param `facade=None` tùy chọn (caller flush truyền vào) — không đổi chữ ký
  bắt buộc.
- F5 dead code: bỏ `own_norm` (dedupe), `SKIP_KINDS` (room_context).
- Kiểm chứng: focused habits/task-habits/runtime/context/dedupe/room_context
  xanh; full `uv run pytest -q` → **1500 passed, 0 failed, 0 errors**.
  Ròng +6 so baseline 1494 (test mới F1/F2/F3). Không fail mới.

## Group memory theo bot (done 2026-10-17)

- Nguyên tắc: bot nào tự trích phần của mình theo idle riêng; Thyca chỉ
  trích phòng của chính mình (participants == ["thyca"], gồm DM Thyca,
  legacy, group chỉ còn thyca) + tra trùng sau mỗi flush.
- Room sweep: phòng có bot con → skip (giữ `child_dm` cho DM, thêm
  `per_bot_jobs` cho group), vẫn compact; chỉ phòng thyca-only extract +
  dedupe như cũ. Participants từ item đã load, không quét thêm.
- `_flush_bot_memory`: sau apply có written → `dedupe.propose_duplicates`
  (reviewer llm_review_pair, best-effort); thứ tự extract → dedupe →
  habit sweep; watch global cuối sweep giữ nguyên.
- Tests cập nhật theo contract (group hỗn hợp skip, thyca-DM/group-cũ giữ).
- Kiểm chứng: focused sweep/flush/dedupe/habits/context xanh; full suite →
  **1506 passed, 0 failed, 0 errors**. Ròng +2. Không fail mới.

## DM-theo-bot + Thyca watch USER.md (done 2026-10-17)

- Quyết định: DM của bot nào thì job bot đó xử lý; Thyca không trích từ
  DM bot con; Thyca theo dõi thay đổi `bots/<id>/USER.md` để tổng hợp ≥2.
- Room sweep bỏ qua extract/dedupe cho child DM (`skipped: child_dm`, vẫn
  compact transcript); DM Thyca + group như cũ.
- `_flush_bot_memory` chỉ sweep local; watch mới
  `aggregate_changed_bot_habits` (chữ ký mtime_ns/size theo bot, lần đầu
  tính là đổi, chỉ cập nhật sau thành công, best-effort) gọi cuối
  `sweep_idle_sessions` → report key `habits_aggregated`.
- Kiểm chứng: focused sweep/flush/habits/chat_app xanh; full suite →
  **1504 passed, 0 failed, 0 errors**. Ròng +4. Không fail mới.

## Phase 1F kiểm chứng (2026-10-16)

- Focused (habits/context/jobs/prompt/cli/active/task-habits): 158 passed.
- Full `uv run pytest -q` → **1463 passed, 2 failed, 6 errors** (2m13s).
  Fail/error y hệt baseline (webui streaming cũ). Ròng +7 (6 habits +
  1 context). Seed user.md mới không phá test prompt/onboarding. Không fail mới.

## Phase 2A kiểm chứng (2026-10-16)

- Focused (room_context/context/rooms/bus_refs/jobs/tool_files/registry/
  task-habits): 118 passed.
- Full `uv run pytest -q` → **1473 passed, 2 failed, 6 errors** (2m13s).
  Fail/error y hệt baseline (webui streaming cũ). Ròng +10 (3 unit +
  5 context + 1 jobs + 1 files). Không fail mới, không sửa test FE cũ.

## Phase 2A rework kiểm chứng (2026-10-16)

- Focused (room_context/context/rooms): 57 passed.
- Full `uv run pytest -q` → **1475 passed, 2 failed, 6 errors** (2m13s).
  Fail/error y hệt baseline (webui streaming cũ). Ròng +2 (failed-turn +
  room-map; kỳ vọng lượt-2 sửa theo ngữ nghĩa mới). Không fail mới.

## Phase 2B kiểm chứng (2026-10-16)

- Full suite (trừ 2 file webui chậm): **1482 passed**, fail/error chỉ còn
  đúng baseline cũ (2 serve_chat createLiveStatus + 6 concurrent_streams
  errors, chạy riêng xác nhận). Ròng +6 so baseline 1476. Không fail mới.

## Fix lượt bot lỗi mất user row (done 2026-10-16, đóng open item 2A rework)

- Nguyên nhân: `_run_bot_turn` append user row qua manager riêng (early_room)
  trong khi `sessions` load từ đầu lượt; khi runtime raise, mark trên view
  stale rewrite file và xóa user row vừa append. Đường Thyca root an toàn
  (loop append user row trên cùng manager trước khi mark).
- Sửa: helper `_mark_room_error` reload room trên chính manager trước khi
  mark (best-effort, giữ nguyên thứ tự rows/error meta); chỉ đường bot dùng.
- Test đỏ→xanh: bot turn lỗi giữ user row + error code chat_unavailable,
  cursor dừng ở delta, lượt kế thấy lại tin đó.
- Full `uv run pytest -q` → **1476 passed, 2 failed, 6 errors** (2m14s).
  Fail/error y hệt baseline (webui streaming cũ). Ròng +1. Không fail mới.

## Phase 2A — ngữ cảnh phòng nhóm công khai (done 2026-10-16)

- Module thuần mới `thyca/bots/room_context.py`: `public_room_messages` +
  `room_delta` + fingerprint sha256(ts|role|speaker|content)[:16]. Điều kiện
  công khai cuối cùng: user content non-empty; assistant không tool_calls,
  content non-empty, bỏ lời chính recipient + row bus_event, kind
  message/result (message đòi parent_span_id None), card (render gọn từ
  meta.card, không JSON thô), llm (final Thyca root), hoặc không meta/kind
  (final cũ); bỏ role tool/system, reasoning (chỉ lấy content), mọi kind
  bus còn lại + naming/compaction.
- `_record_bus_events` thêm meta `bus_event: True` (giữ kind cũ).
  Cursor (count, fingerprint) lưu trong internal session qua synthetic row
  kind="room_context"; ChatApp tính delta TRƯỚC user row hiện tại (DM bỏ
  qua); runtime append synthetic trước compact/flush; deliver tính delta
  sau khi ghi bus rows (envelope hiện tại bị filter loại).
- `transcript_tail` bỏ kind room_context; PathGuard deny_read cả cây
  `sessions/` với bot con (Thyca/user giữ nguyên); rules.md +1 dòng.
- Kiểm chứng: xem dưới.

## Phase 2A rework — cursor ra khỏi prompt, qua tin hiện tại (done 2026-10-16)

- Nguồn sự thật cursor là room map (`room_sessions.json`, tương thích ngược
  string cũ): `runtime.room_cursor/set_room_cursor`; dict mới giữ session id
  + 2 key cursor (None/invalid thì không ghi, chưa có session thì không tạo).
  Map chưa có cursor nhưng internal đã có synthetic cũ → đọc meta làm seed.
- Runtime persist cursor delta sau append synthetic; SAU loop thành công mới
  set `room_cursor_after`. Lỗi/cancel không qua tin hiện tại.
- `_run_bot_turn` dựng user row một lần (dùng chung cho append + fingerprint),
  cursor-đích = (public-trước + 1 nếu tin hiện tại công khai, fp tin đó).
  Deliver (không có tin user) chỉ persist cursor delta.
- Tests: sửa kỳ vọng lượt-2 (không giao lại tin đã trả lời); lượt lỗi giữ
  nguyên ở mức delta (test ở tầng runtime); room map roundtrip + migration
  meta cũ; A2A cursor dừng ở delta.
- Open item ngoài phạm vi: `mark_turn_error` rewrite file từ view stale nên
  lượt lỗi qua `app.turn` bị mất user row vừa append (phát hiện khi viết test
  failed-turn; behaviour có sẵn từ trước 2A, không sửa ở lượt này).
- Kiểm chứng: xem dưới.

## Phase 2B backend — model session dm/group (done 2026-10-16)

- Wire `session_summary`/`session_detail` thêm `"kind"` (dm khi dm_bot set,
  còn lại group); giữ nguyên bots/dm cho FE hiện tại.
- `ChatApp.create(bots)`: None/rỗng → ["thyca"]; dedupe + validate ghost/
  paused như cũ; đúng 1 bot → `bot_dm` bot đó; ≥2 bot → group mới.
- `bot_dm`: chỉ nhận session đã có marker dm (bỏ adopt solo không-marker);
  find-or-create qua helper chung mới `SessionManager.ensure_dm` (không lock
  riêng; ChatApp giữ `_dm_lock` + discard_empty như hành vi cũ).
- `set_room_bots` giữ nguyên guards; group 1 bot còn lại vẫn là group.
- CLI default/--continue mở DM Thyca qua `ensure_dm`; --session load đúng id.
- Kiểm chứng: xem dưới.

## Phase 2B backend — model session dm/group (done 2026-10-16)

- Wire summary/detail thêm `"kind"` (dm khi dm_bot set, còn lại group).
- `create(bots)`: None/rỗng → ["thyca"]; dedupe + validate như cũ;
  1 bot → `bot_dm`; ≥2 → group mới (`_create_group_session`).
- `bot_dm`: chỉ nhận session đã marker dm (bỏ adopt solo); find-or-create
  qua helper chung `SessionManager.ensure_dm` (không lock riêng, ChatApp giữ
  `_dm_lock` + discard_empty như cũ).
- `set_room_bots` giữ nguyên guards; group 1 bot còn lại vẫn group.
- CLI default/--continue mở DM Thyca; --session load đúng id.
- Tests cập nhật theo contract: rooms (DM/group/kind), dm (không adopt),
  contract HTTP (group add/remove), fixes (group), chat_app (DM+group),
  serve_chat (DM singleton, group prune/retry-400), cli (DM/--continue/group),
  session (ensure_dm).
- Xoá dữ liệu 2026-10-16 (user đã cho phép, sau full suite không fail mới):
  kiểm tra không có serve/daemon (ps + port 8765 trống); phân loại bằng
  SessionStore metadata; GIỮ 6 file (5 DM: thyca/designer/thyca-coder/
  nhac-viec/be-bay + 1 group designer-thyca-thyca-coder); XOÁ 41 file
  (39 legacy/plain + 2 solo-thyca + 1 solo be-bay, toàn bộ dm=None và
  bots None/đơn). Sau xoá còn đúng 6 file, load OK toàn bộ. Không xoá gì
  khác (bots/**, memory, USER.md, cards giữ nguyên).
- Kiểm chứng: focused sessions/wire/rooms/dm/cli xanh; full suite (dưới).

## Phase 2B rework — prune + create validation (done 2026-10-16)

- Finding 1: `bot_dm` chỉ prune trên đường tạo mới (kiểm marker trước
  trong cùng `_dm_lock`); lookup DM có sẵn trả ngay, group trống của lượt
  khác không bị ăn. Concurrent-1-DM giữ nguyên.
- Finding 2: `create` chỉ thay None/list-rỗng bằng `["thyca"]`; string
  (và mọi kiểu khác) đi nguyên vào `_clean_participants` → ValueError cũ.
- Tests: lookup không prune group trống / lần đầu vẫn prune; create string
  → ValueError, [] và None → DM thyca.
- Full `uv run pytest -q` → **1485 passed, 2 failed, 6 errors**.
  Fail/error y hệt baseline (webui streaming cũ). Ròng +3. Không fail mới.

## UI avatar theo màn — Trace done (2026-10-17)

- Defs dùng chung: khối SVG từ index.html vào `bot-avatar.js` +
  `ensureBotAvatarDefs` (chèn 1 lần, guard fake-DOM); `botMark/Group` tự
  ensure; index.html xoá defs; `.bot-avatar-slot` dùng chung trong CSS.
- Trace: delegation node + turn heading (1 bot → mark, nhiều → stack,
  không bot → không avatar, state tĩnh idle); dashboard.html link CSS.
## UI Request theo bot + đếm request thật (done 2026-10-17)

Rework: backend bucket "unknown" được coi là chưa gắn bot trừ khi roster
thật sự có id đó (slot trống, tên "chưa gắn bot"); test dùng đúng payload
backend + ca roster-có-unknown. Full suite **1516 passed, 0 failed**.

- Đếm: `_aggregate_turn_meta` thêm requests nội bộ (assistant rows trừ
  context-stop) → `BotTurnResult.requests` → meta room row; `trace.py`
  `_row_requests` duy nhất cho turn total + breakdown (bus_event/bus
  kinds/card cũ/card = 0, meta.requests được tôn trọng, còn lại 1).
- UI: `request.js` khối Request theo bot từ `stats.by_bot` (avatar idle,
  tên roster, sort requests desc, share tổng, bar cùng thang, unknown
  slot trống, empty-note, roster patch 1 lần); CSS size 2.2rem.
- Kiểm chứng: request_chart + trace/runtime + markdown xanh; full suite
  **1515 passed, 0 failed, 0 errors** (ròng +4: request test, 2 runtime,
  1 trace-semantics; 2 context_recovery fail giữa chừng đã sửa bằng
  context-stop → 0 trong helper chung). Asset qua daemon (không restart):
  request.js + dashboard.html mới đã phục vụ.

- Xoá: heading + `#cost-sessions` (dashboard.html); renderSessions/
  sessionEntry/sessionsPager + state phân trang + export (cost-rows.js);
  `aggregateSessions` + `NO_SESSION_KEY` (cost-data.js; dedupeTurns cho
  overview/bot/model giữ nguyên); rule `#cost-sessions` (cost.css).
- Giữ: Tổng chi phí, biểu đồ ngày, theo mô hình, theo bot; Trace theo
  phiên, API traces, aggregate backend.
- Tests: 2 test session-aggregate → 1 test dedupe-qua-overview +
  assert tĩnh (HTML/JS không còn cost-sessions); pager/load assertions
  cập nhật theo code mới; markdown test chuyển journal-kit asserts sang
  cost-bots.js.
- Chi phí theo group là việc riêng chưa làm.
- Kiểm chứng: focused cost/markdown xanh; full suite **1511 passed,
  0 failed, 0 errors** (ròng 0: bỏ 2 test session-aggregate, thêm
  test dedupe-qua-overview + test static-pins). Asset qua daemon
  (không restart): cost-view.js và dashboard.html phục vụ đều 0 hit
  cost-sessions.

- `cost-bots.js`: slot `.bot-avatar-slot.cost-bot-avatar` trước tên
  (mark ngay khi dựng, `--team-tint` = tint meter); roster patch cả tên
  lẫn mark từ bot object thật; hàng chưa-gắn-bot giữ slot trống.
  CSS chỉ flex h3 + size 2.2rem. Không đổi gom/sắp xếp/meter/meta.
- Kiểm chứng: cost_journal + team_frontend + trace_journal + webui_format
  xanh; full suite **1511 passed, 0 failed, 0 errors**. Không fail mới.
## UI Token theo bot + fix lệch cost (done 2026-10-17)

- Cost: h3 của cost-bot-entry thành flex align-center (theo pattern
  Request head), giữ DOM.
- Usage: heading + `#usage-bots` sau Token theo mô hình; `aggregateBotTokens`
  từ payload.traces (ưu tiên breakdown, fallback bot_id, turns = số entry
  tham gia, sort token desc); hàng reuse request-model-* + avatar idle +
  meta calls/Đầu vào/Cache/Đầu ra; unknown merge + slot trống (roster thật
  có id đó thì là bot thật); roster patch 1 lần; empty-note khi không token.
- Kiểm chứng: dashboard/cost/request/markdown xanh; full suite **1519
  passed, 0 failed, 0 errors**. Không fail mới.
- Còn mở: team-cards + memories pending.

## UI Trace theo agent (done 2026-10-17)

- Backend: TurnSummary + `session_kind/session_dm/session_bots` từ Session
  vào to_payload; không đổi cách tính nào khác.
- FE: groupTraceTurns giữ kind/dm/bots; helpers thuần traceGroupBotIds
  (participants trước) / traceBotOptions (thyca trước) / filterTraceGroups;
  hàng `#trace-bot-filters` (class filter sẵn có, wrap được) với chips
  Tất cả + bot (avatar idle + tên roster + số phiên); list lọc giữ index
  tuyệt đối, pager đếm theo list lọc, đổi filter về page 1; sessionEntry
  avatar kênh (DM → seal chủ DM, group → stack participants/bots,
  aria-hidden); empty-note khi filter rỗng; deep-link/selectGroup giữ
  nguyên (filter mặc định Tất cả nên deep-link cũ không đổi).
- Kiểm chứng: trace/team_frontend xanh; full suite **1523 passed,
  0 failed, 0 errors**. Không fail mới.
- Còn mở: team-cards (memories pending done dưới đây).

## UI team-cards/exchange (done 2026-10-17) — danh sách màn avatar đã hết

- `team-cards.js`: summary mang group seal (botMarkGroup, profile từ
  message đầu mỗi bot); mỗi row thêm avatar slot trước tên + bọc who/body
  vào div content (giữ border tint + thứ tự); message không bot_id → slot
  trống; question/error card không thêm avatar (đã dưới header có avatar).
- CSS chỉ layout/size (slot 1.7rem, summary/row flex, content min-width).
- Kiểm chứng: team_frontend + format + markdown xanh; full suite **1525
  passed, 0 failed, 0 errors**. Không fail mới.
- Màn avatar đã xong: Trace, Cost, Request, Usage, Memories pending,
  team-cards (+ sidebar/mention/drawer/settings/transcript có từ trước).

## UI Nhật ký chờ duyệt (done 2026-10-17)

- `team-pending.js`: avatar slot + seal idle trong source card và filter
  chips (Tất cả bot không avatar); roster fetch 1 lần, patch tên+mark qua
  repaint (bỏ qua khi busy); item không botId giữ slot trống + text cũ.
  Export avatarSlotFor/candidateCard/filterChip cho test; bind() guard
  khi không có document.
- CSS chỉ layout (source inline-flex, size 1.9/1.4rem); memories.html link
  avatar CSS.
- Kiểm chứng: team_frontend + cost + webui_format xanh; full suite **1524
  passed, 0 failed, 0 errors**. Không fail mới.
- Còn mở: team-cards.

- Cụm serve_chat (2 assert): chuỗi khóa xuống dòng + arg thứ 3 → regex
  `createLiveStatus\(el\.messageList,\s*detail\.started_at` (ý nghĩa giữ
  nguyên: card mint từ started_at thật); bỏ 1 `import re` cục bộ thừa.
- Cụm concurrent_streams (6 errors): fake Element thêm stub `style`
  (nguyên nhân commit 578aadf thêm `header.style.setProperty` vào
  chatBrandHeader mà harness không có).
- Chỉ sửa test, không chạm code sản phẩm.
- Full `uv run pytest -q` → **1494 passed, 0 failed, 0 errors** (2m08s).

- Focused frontend (team_frontend 84 + webui_format): xanh.
- Full `uv run pytest -q` → **1486 passed, 2 failed, 6 errors** (2m11s).
  Fail/error y hệt baseline (webui streaming cũ). Ròng +1. Không fail mới.

## FE-SESSION-1 — frontend khớp model dm/group (done 2026-10-16)

- Xoá `createBotSession` + state newSession*; flap mỗi bot thành nút
  "Tin nhắn riêng" (giữ class/visual) mở DM qua `openBot`/resolveMain.
- `team-data.js`: `isSharedSession` (kind group thắng, thiếu kind fallback
  bots>1 — group 1 bot vẫn top-level), `isDmDetail` (+kind phải dm),
  `findMainSession` (marked hit đòi kind vắng-mặt/dm; fallback solo bỏ
  kind group), `isMainSessionDetail` (chấp nhận shape DM mới),
  `unifiedSessionsForBot` (loại DM theo marker + kind). Comment adopt
  legacy solo cập nhật theo backend mới.
- Tests: 4 flow flap viết lại theo DM (mở DM không POST, wrong-bot reject,
  lỗi hiển thị, stale qua đổi bot/click row) + kind unit (shared/dm/main/
  findMain) + nhãn/src assertions.
- Kiểm chứng: test_team_frontend 84 passed, webui_format xanh; full suite
  (dưới).

## FE-DASHBOARD-1 — redesign Dashboard gộp 1 màn (done 2026-10-17)

- Màu theo bot cho overview (2026-10-17): roster giữ tint/avatar/system, helper botTintStyle (--team-tint/--avatar-tint từ tintVarFor, không viết lại), gắn lên seal/sidebar/tr/seals; stack token 3 sắc độ color-mix của chính bot (In 80%+đen / Out thuần / Cache 35%+trong suốt), unknown fallback accent.
- Bảng agent → biểu đồ thanh ngang chọn chỉ số (2026-10-17): segmented Token/Cost/Request/Tool (mặc định Token), sort + badge theo chỉ số đang chọn, thanh màu bot (token stack 3 đoạn, còn lại đơn sắc), state module không refetch.
- Bảng agent rút gọn 5→3 cột (2026-10-17): Agent (hạng+avatar+tên+badge) | Mức sử dụng (tổng token+% + 1 thanh stack In/Ca/Out theo % tổng + meta request/tool) | Chi phí (% tổng); mobile Agent+Chi phí hàng đầu, Mức sử dụng full width.
- Bảng agent duy nhất ở Chung thay brow xếp hạng + bỏ panel so sánh ở bot view (2026-10-17): table Agent|Chi phí|Request|Gọi tool|Token, badge cao nhất + is-max 4 cột, token kèm split In/Ca/Out; mobile bảng thành hàng xếp chồng 2 cột metric.


- Đếm tool call thật thay ô Lượt ở summary (2026-10-17): runtime
  `_aggregate_turn_meta` đếm len(tool_calls) cùng tập rows, `BotTurnResult.
  tool_calls` → room_meta `tool_call_count`; trace `_row_tool_calls`
  (meta thắng body, bus/card/context-stop = 0) → TurnSummary/breakdown/
  totals/by_bot. Summary Chung + bot view: 6 ô Input/Cache/Output/
  Request/Gọi tool/Chi phí (hint TB/request); group view giữ Lượt.
  Giới hạn dữ liệu cũ: room row bot con trước thay đổi này không có
  tool_call_count nên phần nội bộ đếm 0 — không bịa số. Daemon đang chạy
  backend cũ: API chưa có tool_calls cho tới khi restart serve.


- Chuẩn hoá cỡ chữ overview theo thang Chat (2026-10-17): sidebar nav tên 0.94rem reading / meta 0.72rem; nội dung gốc reading 1rem, summary value 1.5rem; chỉ CSS overview.css, không chạm Trace/kit.


- Một màn duy nhất theo prototype `index-compact.html` (số prototype là
  MOCK, chỉ lấy DNA trình bày): sidebar là entity selector
  (Chung / theo bot / theo group bot), nội dung thay theo thực thể.
- Mới: `pages/dashboard/overview.js` (snapshot song song traces paged +
  stats + bots + sessions; aggregates thuần export để test:
  `aggregateOverview/ByBot/Groups/Models`, `timelineForBot/ForGroup`,
  `aggregateGroupBots`; 3 view Chung/bot/group; sidebar render vào
  `#overview-nav`; click trace row → deep link `?session=&turn=#trace` qua
  `location.assign`) + `pages/dashboard/overview.css` (scoped `#overview`,
  chỉ token Thyca).
- `dashboard.html`: 3 section usage/request/cost → 1 section `#overview`
  (trace-head + period + `#overview-content`); GIỮ section `#trace` chi
  tiết; bỏ 3 script entry cũ, thêm overview.js; giữ mọi CSS cũ cho Trace.
- `dashboard.js`: 2 trạng thái overview/trace; hash legacy về overview
  (strip hash + params); back Trace → overview.
- Model dữ liệu: DM tính vào bot sở hữu (không vào group); group gom theo
  đúng session group thật (sessionMap kind, fallback row session_kind);
  unknown bucket chỉ hiện ở Tổng quan; latency turn chỉ gán cho actor
  (không double-count); avatar = botMark/botMarkGroup idle, unknown = slot
  trống. Không đổi backend, không đổi Trace detail.
- Modules cũ (usage.js/request.js/cost*.js) giữ nguyên làm thư viện + test
  logic giữ nguyên; chỉ cập nhật test khóa markup dashboard.html/switcher.
- Kiểm chứng: aggregates unit mới, boot switcher 2-state, full suite xanh
  (dưới).

## Phase sau (mở — một số điểm đã chốt hướng, chưa triển khai)

- Bỏ luồng "+ Phiên mới" tạo phiên solo (`createBotSession` POST
  `{bots:[botId]}` + reject `detail.dm`) → mở DM qua `GET /api/bots/<id>/dm`.
- Tạo phiên chung chỉ với ≥2 bot; sidebar hiển thị theo `kind` (dm/group),
  gồm cả group legacy còn lại; cập nhật test FE liên quan
  (createBotSession/isDmDetail/findMainSession/verifySharedSession).
- Lưu ý: backend Phase 2B đã đổi contract POST 1-bot → DM nên FE hiện tại
  sẽ lệch cho tới khi task này làm.

## FE-DASHBOARD-1 — redesign Dashboard gộp 1 màn (done 2026-10-17)

- Màu theo bot cho overview (2026-10-17): roster giữ tint/avatar/system, helper botTintStyle (--team-tint/--avatar-tint từ tintVarFor, không viết lại), gắn lên seal/sidebar/tr/seals; stack token 3 sắc độ color-mix của chính bot (In 80%+đen / Out thuần / Cache 35%+trong suốt), unknown fallback accent.
- Bảng agent → biểu đồ thanh ngang chọn chỉ số (2026-10-17): segmented Token/Cost/Request/Tool (mặc định Token), sort + badge theo chỉ số đang chọn, thanh màu bot (token stack 3 đoạn, còn lại đơn sắc), state module không refetch.
- Bảng agent rút gọn 5→3 cột (2026-10-17): Agent (hạng+avatar+tên+badge) | Mức sử dụng (tổng token+% + 1 thanh stack In/Ca/Out theo % tổng + meta request/tool) | Chi phí (% tổng); mobile Agent+Chi phí hàng đầu, Mức sử dụng full width.
- Bảng agent duy nhất ở Chung thay brow xếp hạng + bỏ panel so sánh ở bot view (2026-10-17): table Agent|Chi phí|Request|Gọi tool|Token, badge cao nhất + is-max 4 cột, token kèm split In/Ca/Out; mobile bảng thành hàng xếp chồng 2 cột metric.


- Đếm tool call thật thay ô Lượt ở summary (2026-10-17): runtime
  `_aggregate_turn_meta` đếm len(tool_calls) cùng tập rows, `BotTurnResult.
  tool_calls` → room_meta `tool_call_count`; trace `_row_tool_calls`
  (meta thắng body, bus/card/context-stop = 0) → TurnSummary/breakdown/
  totals/by_bot. Summary Chung + bot view: 6 ô Input/Cache/Output/
  Request/Gọi tool/Chi phí (hint TB/request); group view giữ Lượt.
  Giới hạn dữ liệu cũ: room row bot con trước thay đổi này không có
  tool_call_count nên phần nội bộ đếm 0 — không bịa số. Daemon đang chạy
  backend cũ: API chưa có tool_calls cho tới khi restart serve.


- Chuẩn hoá cỡ chữ overview theo thang Chat (2026-10-17): sidebar nav tên 0.94rem reading / meta 0.72rem; nội dung gốc reading 1rem, summary value 1.5rem; chỉ CSS overview.css, không chạm Trace/kit.


- Một màn duy nhất theo prototype `index-compact.html` (số prototype là
  MOCK, chỉ lấy DNA trình bày): sidebar là entity selector
  (Chung / theo bot / theo group bot), nội dung thay theo thực thể.
- Mới: `pages/dashboard/overview.js` (snapshot song song traces paged +
  stats + bots + sessions; aggregates thuần export để test:
  `aggregateOverview/ByBot/Groups/Models`, `timelineForBot/ForGroup`,
  `aggregateGroupBots`; 3 view Chung/bot/group; sidebar render vào
  `#overview-nav`; click trace row → deep link `?session=&turn=#trace` qua
  `location.assign`) + `pages/dashboard/overview.css` (scoped `#overview`,
  chỉ token Thyca).
- `dashboard.html`: 3 section usage/request/cost → 1 section `#overview`
  (trace-head + period + `#overview-content`); GIỮ section `#trace` chi
  tiết; bỏ 3 script entry cũ, thêm overview.js; giữ mọi CSS cũ cho Trace.
- `dashboard.js`: 2 trạng thái overview/trace; hash legacy về overview
  (strip hash + params); back Trace → overview.
- Model dữ liệu: DM tính vào bot sở hữu (không vào group); group gom theo
  đúng session group thật (sessionMap kind, fallback row session_kind);
  unknown bucket chỉ hiện ở Tổng quan; latency turn chỉ gán cho actor
  (không double-count); avatar = botMark/botMarkGroup idle, unknown = slot
  trống. Không đổi backend, không đổi Trace detail.
- Modules cũ (usage.js/request.js/cost*.js) giữ nguyên làm thư viện + test
  logic giữ nguyên; chỉ cập nhật test khóa markup dashboard.html/switcher.
- Kiểm chứng: aggregates unit mới, boot switcher 2-state, full suite xanh
  (dưới).

## Phase sau (mở — một số điểm đã chốt hướng, chưa triển khai)

- Gỡ `related` + merge (quyết định đã chốt: bỏ); thay bằng tra trùng →
  đề xuất team. Chưa triển khai.
- Extractor gắn task/habit khi trích ký ức. Chưa triển khai.
- Habit sweep (quy tắc đã chốt, chưa triển khai): quan sát habit ghi vào
  daily; đủ 3 lần ở 3 NGÀY khác nhau trong phạm vi một bot → ghi vào
  `~/.thyca/bots/<id>/USER.md` riêng của bot đó; Thyca tổng hợp từ USER.md
  của từng bot, cùng thói quen xuất hiện ở ≥2 bot → `~/.thyca/USER.md`
  mục `## Habits`; user nói thẳng "tôi thường/luôn" thì 1 lần là đủ
  ở cấp bot đó.
- Active Memory bơm mục lục task open vào prompt. Chưa triển khai.
- Kênh user-facing chỉ còn 2 loại: `dm` (duy nhất theo bot) và `group`;
  session nội bộ bot×kênh chỉ là chi tiết kỹ thuật phía sau (quyết định
  đã chốt, chưa triển khai lại).
- Group context kiểu group chat của người (quyết định đã chốt, chưa triển
  khai): mọi thành viên thấy lịch sử công khai của phòng; bot mới vào đọc
  full lịch sử công khai với nhãn "lịch sử trước khi bạn tham gia" + ghi
  rõ người nói; cursor theo bot cho các lượt sau. Tool call/suy nghĩ/
  session nội bộ/DM/A2A riêng KHÔNG vào context chung. Bot được dùng thông
  tin DM để làm việc nhưng không tự tiết lộ nội dung DM vào group.
- Identity twin (quyết định đã chốt, chưa gia cố): chung SOUL không xưng
  tên, riêng identity từ TWIN.md — bot biết mình là twin thuộc hệ Thyca
  với vai trò riêng, không phải Thyca chính. Hướng gia cố: snapshot của
  bot con không chứa IDENTITY.md của Thyca ngay từ nguồn, thay vì nạp rồi
  thay thế ở runtime.
- Task mở/giao việc (quyết định đã chốt, không thuộc phase 1A): bất kỳ bot
  nào cũng mở được task (hệ thống sinh id), công bố id trong phòng; bot mở
  tạm điều phối và chỉ điều phối/user đóng task chung.

## Đối chiếu đóng backend đã chốt (2026-10-19)

- Hai mục "Phase sau" trong file này là bản cũ/trùng: gỡ related,
  extractor task/habit, habit sweep + aggregation, `<memory_tasks>` vào
  prompt, kênh dm/group, group context công khai và gia cố identity twin
  đã triển khai ở Phase 1B–1F, 1D, 2A, 2B và các mục done ngày 2026-10-17
  ở trên. Đọc các mục done làm căn cứ hiện hành.
- Bổ sung nốt quy tắc đã chốt vào tool description: chỉ bot điều phối/
  người mở task (hoặc user) mới đóng task (`memory_remember` +
  `memory_update`). Memory kind mở thêm `checkpoint` (bắt buộc gắn task)
  cho cơ chế bot chủ động checkpoint; extractor không tự sinh kind này.
- Kiểm chứng tại cây hiện tại: full `uv run pytest -q` xanh; port mặc
  định đã đổi sang 19502 (quyết định 2026-10-19) và daemon live trên
  cổng này có `tool_calls` ở cả list và stats.
- Còn mở duy nhất trong hướng này: công bố task id trong phòng + bot mở
  tạm điều phối qua sự kiện phòng (draft `team-context`, chưa kích hoạt).
  Plan giữ `in-progress`; commit chờ lệnh riêng.
