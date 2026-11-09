---
status: draft
created: 2026-10-21
last_updated: 2026-10-21
---

# Memory curation bằng turn (bot tự dọn) + profile tổng hợp bằng Thyca turn

## Goal

Bỏ "mono" style (gọi LLM 1 lần, `tools=None`) cho 2 khâu:

1. **Per-bot memory curation**: khi bot sắp compact (memory đầy), bot chạy 1
   turn ngắn của CHÍNH NÓ — đọc transcript, tự quyết định gì đáng nhớ, dùng
   tool `memory_search`/`memory_remember`/`memory_update`/`memory_propose_team`
   để curate — thay cho `extract_from_transcript` mono hiện tại.
2. **Shared profile aggregation**: Thyca chạy turn định kỳ (scheduler cron)
   review profile facts từ các bot → ghi vào shared USER.md qua tool
   `profile_update` (Thyca-only) — thay cho rule-based `aggregate_team_habits`.

## Lịch sử quyết định

- 2026-10-21: đề xuất kind="fact" → BỎ (user: quay lại cơ chế habit, dạy LLM
  ở khâu tổng hợp).
- 2026-10-21: aggregation single-shot vs turn — user chốt hướng "làm theo
  cơ chế bình thường" (turn + tool), áp dụng luôn cho cả khâu extraction
  per-bot: "bot sau khi biết nó hết [việc] thì nên làm việc theo kiểu kia
  (turn), trực quan nhất".

## Hiện trạng đã xác minh (2026-10-21)

- `_flush_bot_memory` (chat_app.py:335) chạy trước compact: mono
  `extract_from_transcript` (`jobs.py:133`, `connect.chat(..., None)`) —
  cả bot mẹ lẫn bot con, không tool/A2A. JSON schema: task reuse, cont,
  kind, habit, team_proposal.
- `aggregate_team_habits` (habits.py:204) rule-based: normalize label, ≥2
  bots → append shared USER.md (append-only, không LLM).
- Bot card = TWIN.md (name, role, description, persona...); bot turn hiện
  chỉ nhận roster tên+role, không có tool đọc card đồng đội; **không có
  tool nào ghi USER.md** từ turn.
- Scheduler: card `schedule` (cron) → `scheduler.tick` fire bot qua
  `app.turn`/`scheduled_turn` — turn bình thường đầy đủ tool/A2A.

## Quyết định đã chốt

### 1. Per-bot curation bằng turn (thay mono extractor)

- Trigger giữ nguyên: trước compact thật sự (không phải mọi idle sweep —
  chốt chi tiết lúc implement: chỉ khi compact sắp diễn ra).
- Bot chạy **turn riêng trong DM của nó** (tránh SessionBusy với session
  đang compact): prompt hướng dẫn đọc transcript tail (inject sẵn), dùng
  tool: `memory_search` (chống trùng), `memory_remember` (ghi, kèm
  task/cont/kind/habit nếu có), `memory_propose_team` (fact đáng cả đội
  biết), `memory_update`/`memory_forget` (dọn).
- Giới hạn: turn tối đa N vòng tool (chốt lúc implement, đề xuất 8); fail/
  timeout → compact vẫn chạy (best-effort, transcript không mất).
- Cấu trúc metadata (task id reuse...) chuyển từ JSON schema sang prompt +
  params tool — cần test LLM thật.
- Gỡ dần `extract_from_transcript`/`apply_extracted` mono (giữ `transcript_tail`
  để inject).

### 2. Shared profile aggregation bằng Thyca turn

- **Tool mới `profile_update`** (Thyca-only, registry chặn bot con): nhận
  danh sách lines mong muốn cho auto-block `## Profile`; code lo atomic
  write + markers + **merge thay thế** (dòng auto cũ bị thay/xoá; manual
  user không đụng; "lời user hiện tại luôn thắng").
- **Tool mới `bot_card`** (đọc card đồng đội: name/role/description/persona)
  — để turn Thyca có ngữ cảnh đánh giá fact theo phạm vi bot. (Hoặc inject
  cards vào prompt turn — chốt lúc implement; tool tái dùng được sau này.)
- Thyca turn định kỳ (card Thyca `schedule` cron, fire thưa — đề xuất
  1 lần/ngày hoặc khi có observation mới): đọc `## Profile` blocks các bot
  (provenance bot kèm sẵn), dùng `memory_search`/`bot_card`/`bot_send` (A2A
  hỏi bot xác nhận fact) để review, quyết định universal vs scoped
  (qualifier trong label), rồi `profile_update` ghi kết quả.
- Ngưỡng (prompt cho turn): user nói trực tiếp / explicit → lên luôn;
  ≥2 bot/kênh → universal; 1 bot + xác minh tốt → lên; còn lại giữ bot.
- Không đổi kind nội bộ `habit` (đổi kind = rewrite heading trong .md);
  đổi tên hiển thị `## Habits` → `## Profile` (parse cả marker cũ).

## Ngoài phạm vi (v1)

- Không đổi candidate queue / team memory (vẫn qua duyệt).
- Không migration dữ liệu.
- Không đổi UI.
- Bot đọc card đồng đội trong turn thường (ngoài aggregation) — tool
  `bot_card` tạo sẵn, mở cho bot con hay không chốt sau.

## Kiểm chứng

- Unit: `profile_update` (markers, lock, replace, manual không đụng);
  `bot_card` (registry, quyền); turn prompt parse kết quả; fallback
  compact vẫn chạy khi turn fail.
- Integration: scheduler fire turn curation; turn dùng tool ghi memory;
  Thyca turn aggregation cập nhật shared USER.md; chạy lại idempotent.
- E2E thật: chạy 1 vòng trên dữ liệu hiện tại; dọn 8 dòng habit trùng.

## Các bước (dependency order)

1. Tool `profile_update` + `bot_card` (registry, Thyca-only).
2. Turn curation per-bot: prompt + giới hạn vòng + trigger trước compact +
   fallback; wire thay `_flush_bot_memory` mono.
3. Thyca scheduled turn aggregation: prompt (ngưỡng/scope/card context) +
   wire scheduler.
4. Đổi tên hiển thị ## Profile (parse cả marker cũ) + dọn block trùng 1 lần.
5. Tests full + E2E.

## Trạng thái thi công

- [x] 2026-10-21 (v2 — agent chủ động, thay cho turn-curation riêng):
  - Tool **`compact`** (`thyca/tools/session_tools.py`) — agent gọi để compact
    session hiện tại (force, summarizer + tail); turn-scoped qua
    `BotTurnContext.compact` (runtime bind callback `_force_compact`);
    `BASELINE_BOT_TOOLS` có `compact` (card cũ tự được cấp); đăng ký trong
    `toolchain.build_tool_registry`.
  - **Flush skip khi agent đã checkpoint**: runtime `_segment_checkpointed`
    (kind='checkpoint' + task trong segment) → bỏ mono extractor; không có
    → mono giữ làm lưới an toàn. Áp dụng cho cả bot mẹ lẫn bot con (bỏ
    special-case `bot_id == "thyca"` của v1).
  - Guidance `<context_budget>` nhắc agent: ghi checkpoint xong → gọi compact.
  - Test: test_session_tools.py (4), test_bots_runtime (baseline),
    test_bots_context (checkpoint-skip flush), budget bumps (5400/8500).
- [x] 2026-10-21 (v4 — E2E-2 thực tế + fix phát hiện):
  - **E2E-2 PASS trên dữ liệu thật + LLM thật**: `aggregate_team_profiles()`
    → turn Thyca gộp ~50 dòng biến thể còn **5 dòng** trong `## Profile`
    block; legacy `## Habits` auto block bị strip; manual sections nguyên
    vẹn.
  - Bug phát hiện + đã fix: (a) prompt aggregation 24KB vượt `TEXT_MAX` →
    thêm `SYSTEM_TEXT_MAX=24000` cho system turns (turn_options +
    workspace_turns, có test); (b) card context lặp theo từng dòng → in
    1 lần/bot (prompt 24KB → 3.7KB); (c) `_replace_block` mất newline sau
    marker (fix + test); (d) strip legacy giữ heading khi section chỉ có
    comment auto (fix + test).
  - **Phát hiện vận hành (chưa fix)**: sweep idle chạy mono extraction
    tuần tự gọi LLM thật (read timeout 300s + retry) — scheduler thread
    kẹt hàng chục phút sau mỗi sweep; aggregation phải chờ sau nó; daemon
    còn kẹt không SIGTERM thoát được khi scheduler wedged (phải kill -9).
    Đây là vấn đề của mono path — sẽ tự hết khi bot con chuyển sang
    TURN-CURATE; ghi nhận trong câu hỏi mở.
- [ ] **Test end-to-end** — plan dưới đây; E2E-2 đã chạy thực tế (mục
  trên), E2E-1 (compact tool do agent gọi) và E2E-3 (regression) chưa chạy
  đủ trên dữ liệu thật.

## Plan test end-to-end (dữ liệu thật + LLM thật)

Môi trường: daemon `thyca --serve --daemon` port 19502 (code mới), provider
cấu hình sẵn, dữ liệu `~/.thyca` thật. Mỗi bước ghi lại kết quả vào
`serve.log` + kiểm tra file.

### E2E-1: compact tool + flush skip (agent chủ động)

1. Vào phòng 1 bot (webui 19502), nói chuyện đủ dài hoặc tạm hạ
   contextTokens để sớm chạm `state=prepare/mandatory`.
2. Quan sát prompt (trace/webui thinking): `<context_budget>` có guidance
   "After recording, call compact".
3. Kỳ vọng agent tự: `memory_remember(kind='checkpoint')` → `compact`.
   Nếu LLM không tự làm trong 2 thử, ghi nhận là prompt chưa đủ mạnh (fix
   guidance, không phải bug luồng).
4. Verify:
   - Session có `[compaction: ` marker; transcript giữ tail.
   - Memory entry checkpoint tồn tại (nhật ký page).
   - Mono extractor KHÔNG chạy trong turn đó (log không có traceback/không
     có extractor prompt — kiểm tra qua trace: không có requests lạ).
   - Turn tiếp theo hoạt động bình thường.
5. Ngược lại (không checkpoint): để 1 phiên chạm compact mà agent không
   ghi gì → mono flush vẫn chạy (lưới an toàn) — xác nhận bằng trace/log.

### E2E-2: profile aggregation (Thyca turn → USER.md)

1. Kích hoạt: chờ scheduler tick (1 phút) — signatures mới sau restart →
   aggregation tự chạy; hoặc chạy tay:
   `.venv/bin/python -c "from thyca.app.chat_app import ChatApp; ...; app.aggregate_team_profiles()"`
2. Verify:
   - `~/.thyca/USER.md` có section `## Profile` + markers mới, lines đã
     dedupe (8 dòng "Discord trả lời ngắn gọn..." gom còn vài dòng, giữ
     qualifier Discord nếu chỉ scoped).
   - Legacy `## Habits` block bị strip (chỉ khi mọi line đã migrate).
   - Manual content (`## Name and forms of address`...) không đụng.
3. Chạy lại lần 2 → kết quả `ran: False` (skip 24h).
4. Idempotent: prompt không đổi → turn không gọi `profile_update`.

### E2E-3: regression

- `/api/memory/stats` (global + bot scope), `/api/memory/candidates`,
  diary page load < 1s; chat turn bình thường.
- `python -m pytest tests/ -q` (trừ webui node tests đã biết lỗi).

### Tiêu chí pass

- E2E-1: compact do agent gọi thành công ≥1 lần; flush skip đúng; lưới an
  toàn hoạt động khi agent không ghi.
- E2E-2: USER.md có Profile block hợp lý, legacy stripped, manual nguyên
  vẹn, lần chạy 2 skip.
- E2E-3: toàn bộ 200 + suite pass.

### Rủi ro chưa verify được trong unit test

- LLM có tự gọi `compact` đúng thời điểm không (phụ thuộc guidance) —
  E2E-1 bước 3.
- Chất lượng dedupe/scope của turn aggregation (prompt tiếng Việt) —
  E2E-2 bước 2, chỉnh prompt nếu kết quả kém.

## Contract `profile_update` (đã implement — v3)

- **Làm gì**: tool Thyca-only (`thyca/tools/profile_tools.py`), 1 lần gọi =
  1 lần cập nhật atomic auto-block `## Profile` trong shared USER.md. Input:
  **full set** lines mong muốn (turn quyết định cả danh sách cuối). Code lo:
  writer lock, markers (parse cả marker cũ), diff → thêm/thay/xoá dòng auto,
  manual user không bao giờ đụng, atomic write. Trả về `{ok, added, removed}`.
- **Khi nào chạy**: tool không tự chạy — chỉ được gọi BỞI turn aggregation
  (`ChatApp.aggregate_team_profiles`, chạy trong `sweep_idle_sessions` —
  scheduler tick). Turn tự kiểm tra: skip nếu signatures bot không đổi
  và < 24h từ lần chạy trước; prompt có quy tắc "không đổi thì không gọi
  tool". Sau turn: `maybe_strip_legacy_block` (strip legacy ## Habits chỉ
  khi mọi line đã migrate). A2A `bot_send` dùng để xác minh chéo khi cần;
  không cần tool `bot_card` (card static → backend inject vào prompt).

## Câu hỏi mở

- Turn curation chạy khi nào chính xác: mọi compact hay chỉ compact do
  context meter? (chi phí)
- Cap vòng turn curation (đề xuất 8) và tần suất aggregation (đề xuất 1/ngày).
- `bot_card` mở cho bot con luôn hay Thyca-only (aggregation dùng)?
- Metadata task/habit sau khi bỏ JSON schema: chất lượng có đủ không
  (test LLM thật).