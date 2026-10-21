---
status: superseded
created: 2026-10-19
last_updated: 2026-10-20
---

# Bot Workspace + Channel Windows — historical implementation record

> **SUPERSEDED:** The migration/shadow design and the WS-7 “still waiting for
> promotion/parity” status below are historical, not current implementation
> guidance. The canonical-only hard cut in the next section is authoritative.
> Preserve the workstream evidence below as history; do not revive the old
> room-session migration, shadow, promotion/demotion, or rollback plan.

## Current canonical-only hard cut (verified 2026-10-20)

- Root and child turns share `BotRuntime`; the CLI is a thin `ChatApp.turn`
  adapter and has no separate turn executor. `thyca/app/run_turn.py`,
  `TurnScope`, and `TurnRequest` are removed.
- `WorkspaceStore` is authoritative for bot/window bindings and cursors;
  each bot's `WorkspaceTimeline` is the sole canonical execution store.
  There is no `room_sessions.json` migration path, shadow store, parity/cutover,
  promotion/demotion, or workspace rollback API.
- Public room history is a compact read model: the user's message and one
  aggregate assistant reply. Live thinking deltas remain available while a
  turn streams. Tool calls/results and persisted reasoning belong in Trace,
  read from original canonical `message` events by exact window and span;
  `view_reset` copies are excluded so details survive compaction without
  duplication.
- Manual compaction visits every room participant independently, only for
  an existing canonical context, and reports each result under `per_bot`.
- Effective model precedence is operation override > non-default bot-card
  model > configured global default. The context meter is an estimate; it
  cannot include unknown next-user text, mailbox input, or query-specific
  recall.
- The user-authorized data reset removed all `~/.thyca` data, including
  memories, config, keys, and skills. Do not access or recreate that data.
- Independent review of this final closeout remains outstanding; no
  real-provider quality verification is claimed.

## Historical design and implementation record (superseded)

The following sections preserve the 2026-10-19 approved design and evidence.
Their then-current/remaining-work statements are not current state.

Plan này đã được duyệt theo từng workstream (WS-0..WS-7), với mô hình:

> **Mỗi bot có một workspace (căn nhà) duy nhất. DM, group và các kênh ngoài
> là các cửa sổ giao tiếp mở vào workspace đó.**

Mục tiêu không phải gộp mọi transcript thành một prompt chung. Workspace là
nơi bot giữ identity, trạng thái công việc, task, checkpoint, memory refs và
quyết định đang dở. Mỗi cửa sổ có transcript công khai riêng, người tham gia
riêng, cursor riêng và mức visibility riêng. Khi bot đứng ở một cửa sổ, hệ
thống dựng **channel view** phù hợp từ workspace thay vì nạp toàn bộ nhà.

UI hiện tại được giữ làm WebUI adapter đầu tiên. API session hiện tại được
giữ tương thích ở các phase đầu; chỉ nối lại khi payload/status cần thêm
thông tin workspace/channel.

Ngoài phạm vi bản đầu:

- Không đổi kho memory Markdown/SQLite và không migrate ký ức cũ.
- Không viết lại LLM provider, tool gateway, MCP client hay thuật toán
  compact/shrink hiện tại.
- Không biến mọi kênh ngoài thành MCP server thật ngay. Kênh dùng contract
  kiểu MCP ở biên action/event; transport có thể native hoặc MCP.
- Không tự quyết các ngưỡng mới ngoài 80%/90%/95% đã chốt.

## Bằng chứng từ code hiện tại

- `ChatApp.turn()` (`thyca/app/chat_app.py:774`) là điểm vào phòng: resolve
  responder, claim theo room, rồi rẽ Thyca root (`run_turn.py`) hoặc bot con
  (`_run_bot_turn`). ChatApp đồng thời giữ room CRUD, DM/card posting, bus
  recording, memory sweep, compaction và trace detail — 1.378 dòng.
- `BotRuntime.internal_session()` (`thyca/bots/runtime.py:305`) tạo một
  internal session cho từng cặp bot × room qua `room_sessions.json`.
  `run_bot_turn_detailed()` tự refresh ActiveMemory, vá các section prompt,
  append synthetic `room_context`, dự đoán flush/compact, bind turn context
  và chạy AgentLoop — 886 dòng.
- Cửa sổ user-facing hiện là room session JSONL (`thyca/sessions/models.py`,
  `store.py`, `manager.py`, `wire.py`); wire chỉ biết `bots`, `dm`, `kind`.
- Group delta đã có `thyca/bots/room_context.py` với cursor theo bot × room.
- A2A đã có `Bus`/`Envelope.refs`/artifact store và task view thuần từ event
  log (`thyca/bots/bus.py`, `artifacts.py`, `tasks.py`), nhưng runtime task
  (span/delegate) và memory task (`task-<8hex>`) vẫn là hai hệ riêng.
- `message_user` đã có landing path an toàn vào DM riêng; `ask_user` đăng
  card ở phòng hiện tại. Chưa có pending record nối câu hỏi DM với task ở
  phòng gốc.
- Prompt được vá từ nhiều nơi bằng các field string trên `ActiveSnapshot`:
  today/today_elsewhere, team, runtime tasks, memory tasks, recalled,
  context_budget. `PromptManager` chỉ render; chính sách chọn dữ liệu đang
  nằm rải ở ChatApp, BotRuntime, run_turn và CLI.
- MCP hiện là client gọi tools (`thyca/tools/mcp.py`): spawn/list/call/sync.
  Chưa có channel subscription/notification inbound để coi MCP là kênh chat
  đầy đủ.
- Memory đã có owner theo bot, `chat` là room gốc, task/cont/kind/status và
  checkpoint kind mới. Đây là nền tốt cho workspace; chưa có nhãn
  sensitivity/shareability độc lập với owner.

## Quyết định đã có từ thảo luận

1. Một bot là một chủ thể thống nhất; session/cửa sổ không tạo ra “bộ não”
   tách rời.
2. Chung context ở tầng trạng thái làm việc, không chung raw transcript ở
   mọi prompt.
3. Kênh là adapter giao tiếp. Inbound là event đã chuẩn hoá; outbound/cross
   -channel là action có schema kiểu MCP tool. Trả lời thường ở kênh hiện
   tại vẫn là final response, không cần tool.
4. Memory cũ dùng nguyên. Bot được hệ thống nhắc kiểm tra memory/task đúng
   lúc; checkpoint dùng `kind="checkpoint"` đã triển khai.
5. Bot chủ động checkpoint ở ranh giới task; hệ thống đo context, nhắc ở
   80%, bắt buộc ở 90%, compact backstop ở 95%.
6. DM là cửa sổ riêng cố định của bot; group là cửa sổ chung. Nội dung riêng
   chỉ sang group dưới dạng kết luận đã được phép/redact.
7. Chốt ngày 2026-10-19: đích đến là **canonical timeline cho mỗi bot**;
   execution transcript theo cửa sổ chỉ là cầu migration. Mỗi bot chạy
   **một lượt tại một thời điểm** trên mọi cửa sổ. Phase đầu chỉ làm WebUI
   native; Discord inbound để phase sau.
8. Sau khi user trả lời ở DM, **bot tự quyết** quay lại group hay chỉ đánh
   dấu ready, nhưng quyết định đó bị chặn bởi share policy đã ghi trên
   pending: không consent thì không mang nội dung/kết luận riêng sang;
   mơ hồ thì ready im lặng hoặc hỏi tiếp ở DM.

## So sánh phương án lõi

### A — Workspace logic trên execution transcript theo cửa sổ (cầu migration)

Giữ internal session bot × window hiện tại làm execution log. Thêm
BotWorkspace ở trên để sở hữu window registry, cursor, work state, pending
private, budget và context builder. Bot vẫn có một nhà duy nhất về mặt trạng
thái; mỗi cửa sổ có sổ làm việc riêng và được nối bằng task/checkpoint.

- Ưu: tận dụng gần hết code đã verify; privacy/compaction theo cửa sổ đã
  đúng; migration thấp; UI nối nguyên API.
- Nhược: liên tục episodic xuyên kênh đi qua checkpoint/memory thay vì raw
  timeline chung; còn hai lớp session (room + internal).

### B — Một canonical timeline cho mỗi bot, prompt là projection đã lọc (đã chốt)

Thêm append-only workspace timeline chứa sự kiện từ mọi cửa sổ. Room JSONL
vẫn là transcript người dùng nhìn thấy. Mỗi lượt, projector chọn sự kiện
theo window/visibility/task để dựng model messages; tool/tool-result và
checkpoint cũng là event trong timeline.

- Ưu: gần nhất với “một căn nhà, một dòng thời gian”; cross-channel resume
  tự nhiên; một nguồn cho work state và audit nội bộ.
- Nhược: phải thay execution persistence của AgentLoop/Observe, compaction
  phải tóm theo visibility segment, migration/dedup transcript cũ khó, blast
  radius lớn nhất. Vì vậy B được triển khai bằng shadow-write + so sánh
  projector + cutover từng bot, không big-bang.

### C — Một raw session duy nhất cho mọi kênh

Không khuyến nghị. DM, group và tool traffic lẫn trong cùng transcript sẽ
làm yếu ranh giới riêng tư, lẫn chỉ thị giữa phòng, khó compact theo cửa sổ
và khó giữ public transcript sạch.

**Quyết định 2026-10-19:** B là kiến trúc đích. A chỉ là bước đệm để giữ
đường chạy hiện tại trong lúc dựng contract, shadow timeline và projector;
không coi A là trạng thái cuối. Không làm C.

## Kiến trúc đích

```text
WebUI ─┐
CLI ───┤  ChannelAdapter  ── ChannelEvent ──► WorkspaceTurnService
Discord┘  (native/MCP)                         │  queue theo bot
                                               │  context builder
                                               │  AgentLoop + tools
                                               ▼
                                      BotWorkspace (mỗi bot)
                                      ├─ identity/card
                                      ├─ windows + cursors
                                      ├─ work items / pending private
                                      ├─ checkpoints + memory refs
                                      └─ canonical timeline (shadow → cutover)
                                               │
ChannelAction ◄────────────────────────────────┘
  reply | post window | ask_private | card | status
```

### Contract kênh

Đặt type thuần ở `thyca/core/channel.py` (stdlib-only):

- `ChannelRef(channel_id, kind, external_ref)`
- `WindowRef(window_id, channel_id, kind=dm|group|system, participants,
  visibility)`
- `ActorRef(kind=user|bot|system, id)`
- `ChannelEvent(event_id, channel_id, window_id, actor, text, refs,
  visibility, work_ref, received_at, idempotency_key)`
- `ChannelAction(kind=reply|post|ask_private|ask_choice|status,
  target_window, text/card, work_ref, share_policy)`
- `ChannelCapabilities(can_dm, can_card, can_thread, can_edit, can_react,
  max_chars, supports_streaming)`

Identity người gửi và bot đích do runtime/adapter xác định, không lấy từ
tool arguments của model.

### Workspace

Đặt implementation dưới `thyca/bots/` để không phá layout backend:

- `workspace.py`: aggregate theo bot, turn queue một-lượt-mỗi-bot, window
  bindings/cursors, trạng thái budget/checkpoint.
- `workspace_store.py`: đọc/ghi atomic phần state mới trong
  `~/.thyca/bots/<bot_id>/workspace/`; tương thích đọc `room_sessions.json`
  cũ ở phase migration.
- `timeline.py`: append-only canonical event log + projection input;
  shadow-write trước, cutover sau khi parity đạt.
- `workspace_context.py`: dựng channel view + prompt sections từ identity,
  window transcript/delta, work index, memory catalog, checkpoint/recall
  được phép và context budget.
- `work_items.py`: projection hợp nhất runtime task, memory task và
  private follow-up. Canonical public id đã chốt: memory `task-<8 hex>`;
  runtime span chỉ là execution ref.
- `private_followups.py`: state machine `open → waiting_private →
  received → resumed|done|cancelled|expired`, tối đa một pending active
  cho mỗi bot+DM để user trả lời không bị đoán ngữ nghĩa.
- `channels.py`: `ChannelAdapter` protocol + registry. Adapter cụ thể của
  WebUI đặt ở `thyca/app/webui_channel.py`; MCP channel bridge để phase sau.

### WorkItem và private follow-up

WorkItem là cầu nối xuyên cửa sổ, không thay memory:

- `work_id`, `owner_bot`, `coordinator`, `origin_window`, `current_window`
- `memory_task_id` (nếu đã checkpoint), `runtime_span_key` (nếu có)
- `status`, `waiting_on`, `visibility`, `share_policy`
- `latest_checkpoint_entry`, `pending_private_id`, timestamps

Luồng hỏi riêng:

1. Ở group, bot tạo pending và báo công khai chỉ “đang chờ thông tin riêng”.
2. Adapter đăng câu hỏi vào DM cố định của chính bot, gắn pending id ở meta.
3. User trả lời DM; runtime resolve deterministic theo pending active.
4. Raw answer ở DM/private memory. Bot tạo bản shareable đã redact nếu
   policy cho phép.
5. WorkItem chuyển `ready`; bot tự quyết quay lại group bằng bản shareable
   hay chỉ để ready cho lượt group sau, trong giới hạn share policy đã ghi
   trên pending. Không có consent chia sẻ thì bot không được mang kết luận
   riêng sang; mơ hồ thì hỏi tiếp ở DM.
6. Cancel ở bất kỳ cửa sổ nào đóng pending; trả lời muộn thành tin DM
   thường, không tự nối lại task đã đóng.

### Context builder

Prompt mỗi lượt gồm, theo thứ tự ổn định cho cache:

1. Identity/role/user/bot_user như hiện tại.
2. Channel brief: kênh, cửa sổ, participants, visibility, capability.
3. Window delta/recent public history của đúng cửa sổ.
4. Work index: task đang mở, task của cửa sổ này trước, pending private ở
   dạng trạng thái không nội dung.
5. Memory: same-window today trước; cross-window chỉ index/reference và
   checkpoint được phép. Private DM hit không xuất hiện ở group, kể cả
   title, trừ khi được liên kết bằng WorkItem shareable.
6. Recalled sau compact, lọc cùng visibility như trên.
7. Rules và `<context_budget>` ở cuối như hiện tại.

Bot vẫn tự gọi `memory_tasks/search/get` để kéo chi tiết. Hệ thống tự bơm
mục lục và checkpoint liên quan để bot không phụ thuộc vào việc “nhớ ra”.

## Lộ trình triển khai

### WS-0 — Chốt quyết định + characterization

- Đã chốt ngày 2026-10-19: canonical timeline là đích đến; một lượt active
  cho mỗi bot; WebUI trước; bot tự quyết resume trong share policy.
- Chốt nốt canonical WorkItem ID và thời điểm đưa Thyca root vào cùng
  workspace executor (cuối plan).
- Viết characterization tests cho các flow phải giữ nguyên: Thyca DM,
  child DM, group mention, A2A deliver, scheduler DM, retry/cancel/follow,
  compact manual/auto, trace by bot.
- Ghi baseline command/results trước refactor.

Gate: không bắt đầu WS-2 khi canonical WorkItem ID chưa chốt.

### WS-1 — Contract + workspace registry ở chế độ shadow

- Thêm `core/channel.py`, `bots/workspace.py`, `bots/workspace_store.py`.
- Map room session hiện tại thành `WindowRef(channel_id="webui")`; đọc
  `room_sessions.json` cũ làm nguồn fallback, ghi format mới song song.
- Chưa đổi đường chạy turn; chỉ ghi/đọc registry và so sánh khi test.

Gate: restart vẫn resolve đúng DM/group/internal session như trước.

### WS-2 — WebUI channel adapter + turn service

- Tách phần điều phối khỏi `ChatApp`: tạo `WorkspaceTurnService` nhận
  `ChannelEvent`, claim theo window + queue theo bot, gọi Thyca/bot
  executor hiện tại, trả `ChannelAction`/detail tương thích UI.
- `ChatApp` còn là facade API; `serve/sessions_api.py` không đổi payload.
- Hàng đợi workspace bảo đảm một bot chỉ có một lượt active trên mọi cửa
  sổ; claim theo window vẫn giữ để UI/stream biết phòng đang bận.
- CLI đi qua cùng event contract ở mức adapter mỏng, chưa ép dùng service
  nếu làm tăng blast radius.

Đã triển khai (2026-10-19, cùng branch, chưa commit):

- `thyca/app/webui_channel.py`: `WebUIChannelAdapter` — `window_for` suy
  WindowRef từ `dm_bot`/`bots` thật của session (dm=private, group=public,
  channel `webui`); `event_for` mint `ChannelEvent` với actor
  `ActorRef("user","user")`, `received_at` UTC, event_id/idempotency uuid;
  capabilities `can_dm/can_card/supports_streaming`.
- `thyca/bots/workspace.py`: `WorkspaceRegistry.lease(bot_id)` async CM —
  asyncio.Lock mỗi bot (một lượt/bot trên mọi cửa sổ). KHÔNG re-entrant:
  cùng owner acquire lại cùng bot raise `WorkspaceTurnConflict`; reuse
  hợp lệ chỉ qua `holds()` + `workspace_claimed`. Wait-for graph là
  multigraph có đếm `owner -> {target: n}`; DFS qua `lease.owner` hiện
  tại của từng target; cancel khi chờ chỉ gỡ edge của request đó.
- `ChatApp.turn` validate text bằng `_clean_turn_text` trước khi
  normalize event (giữ thứ tự validation cũ: `InvalidTurnText` trước
  `SessionNotFound`, không ghi/claim khi text hỏng). Service bind
  WindowRef thật của session vào workspace của responder trước khi
  executor chạy; `_deliver_envelope_claimed` bind room WindowRef cho
  recipient trước khi chạy turn của nó.
- `thyca/app/workspace_turns.py`: `WorkspaceTurnService.run(event, ...)`
  giữ nguyên orchestration cũ của `ChatApp.turn` (validate, cfg sync,
  responder, window claim/job trước khi chờ lease, sink, submit,
  terminal/release); `ChatApp.turn` là facade normalize event WebUI và
  delegate, signature không đổi.
- `thyca/bots/runtime.py`: `run_bot_turn_detailed` là wrapper lease;
  `_run_bot_turn_detailed` là executor cũ; kwarg nội bộ
  `workspace_claimed` được validate bằng `holds()` của registry.
- `ChatApp._run_bot_turn` và `_deliver_envelope` chạy trong lease đã giữ
  (delivery bọc lease recipient, conflict → `delivery failed: ...`,
  envelope còn nguyên trong mailbox).
- Lease owner là contextvar token (không phải Task): tool handler chạy
  trong child task của turn (`asyncio.gather`/`create_task`) kế thừa
  cùng owner nên wait-for graph thấy đủ lease đang giữ.
- Fix sau review WS-1: FileLock bọc trọn read-modify-write của
  `bind_window`/`sync_legacy`; `bind_window` seed internal session/cursor
  từ legacy khi cửa sổ chưa import; backup legacy `O_EXCL` 0600, lỗi
  raise thay vì nuốt; `WindowRef` cấm `participants` kiểu str và chốt
  visibility theo kind (dm=private, group=public, system=system —
  `visibility=None` suy ra, lệch thì raise).

Gate (tạm thời — chỉ narrow suites đã chạy, CHƯA phải full pass):
2026-10-19 `uv run pytest -q tests/test_bot_workspace.py
tests/test_channel_webui.py tests/test_chat_app.py tests/test_serve_chat.py
tests/test_bots_runtime.py tests/test_bots_context.py
tests/test_bots_bus_refs.py tests/test_turn_stream.py
tests/test_bots_dm.py` → **188 passed** (`/tmp/thyca-ws2.log`).
Sau review-fix: narrow lại **81 passed** (`/tmp/thyca-ws2-review.log`,
suites: test_bot_workspace, test_channel_webui, test_chat_app,
test_bots_bus_refs, test_turn_stream). Stream/cancel/retry không đổi
thứ tự event theo các suite này; claim "toàn bộ serve/chat/bots xanh"
chỉ đạt sau broad pass cuối (chưa chạy). `git diff --check` sạch.

Thay đổi kỳ vọng đã chốt: `test_two_sessions_run_turns_in_parallel` —
song song chỉ còn giữa các bot khác nhau (một bot một lượt trên mọi cửa
sổ), nên turn thứ hai giờ @mention bot khác; ý nghĩa test (session claim
độc lập, transcript không lẫn) giữ nguyên.

### WS-3 — Context builder và unified work index

- Gom logic vá `ActiveSnapshot` từ ChatApp/BotRuntime/run_turn/CLI vào
  `workspace_context.py`; đầu ra tạm thời vẫn là `ActiveSnapshot`.
- Hợp nhất hiển thị runtime task + memory task bằng `work_items.py`, giữ
  runtime span làm execution ref; canonical id là memory `task-<8 hex>`
  (đã chốt).
- Áp visibility filter cho today/elsewhere, post-compact recall và
  checkpoint index theo window hiện tại.
- Dời việc tính `context_budget` từ AgentLoop sang context builder;
  AgentLoop vẫn giữ shrink/backstop theo assembled payload.

Đã triển khai (WS-3):

- `thyca/memory/visibility.py`: `MemoryView` (immutable window + bot +
  thyca_dir) qua ContextVar; `bound_memory_view`/`current_memory_view`/
  `bind_memory_view`. Group view: origin khác phải resolve thành group
  session thật và participants hiện tại ⊆ origin participants; mọi DM
  origin + origin không resolve được đều deny; chủ team KHÔNG vượt qua
  DM boundary. Private view (DM/native/system): DM origin chỉ đọc khi là
  của bot đó, group origin đọc khi bot tham gia, origin legacy/unresolved
  giữ owner-scoped cũ. Không view → hành vi cũ y nguyên.
- `active.py`: `split_today_by_session` thêm `origin_ok`/`include_prefix`;
  `_snapshot` lấy predicate từ view hiện tại; free-text prefix không inject
  trong group view.
- `facade.py`: `tasks()` lọc member theo origin chat TRƯỚC khi aggregate/
  chọn title/latest (row `chat` từ `task_session_rows` SELECT mới
  `MAX(chat_session) AS chat`; today chunks dùng `chunk.chat_session`);
  `search`/`recent` lọc hit theo origin (`chat_session` hoặc parse heading
  `thyca {...}` comment) trước khi trả về; `get()` deny trước
  usage.record_gets/TTL/return cho cả chunk+session selector, kể cả
  fallback unindexed (parse heading).
- `thyca/bots/workspace_context.py`: `WorkspaceContextBuilder.build` gom
  memory.read gate, bot_user, layered soul + identity, skills/team, tasks,
  memory_tasks, recalled và `channel_brief` mới; `with_context_budget`
  (staticmethod) chứa 2-pass math dời nguyên từ AgentLoop (80/90/95
  thresholds + `format_context_budget` giữ nguyên). `PromptManager` emit
  `<channel>` sau `bot_user`, trước `today` — text do lead viết, không
  thêm policy prose. `ActiveSnapshot.channel_brief` thêm cuối dataclass.
- `thyca/bots/work_items.py`: `WorkItem` frozen dataclass +
  `project_work_items` (merge chỉ khi OpenTask.task là `task-<8hex>` rõ
  ràng; runtime-only giữ `work_id=None` + runtime ref, KHÔNG promote) +
  `prioritize_window` (current window trước, stable).
- Bind view: `WorkspaceTurnService._leased` bind MemoryView sau khi
  acquire lease (fresh, không stale); `ChatApp._deliver_envelope` bind
  view recipient (room WindowRef, fallback private); public
  `runtime.run_bot_turn_detailed` tự derive WindowRef từ session store
  (synthetic room → window=None private view) khi chưa bound; `run_turn`
  và CLI bind private native view (`window=None`, bot `thyca`) nếu chưa
  bound. `BotTurnContext.window` thêm cuối.
- `AgentLoop._with_context_budget` delegate về builder — timing
  post-compact, shrink/backstop giữ nguyên trong loop.

Gate: WS-3 narrow pass: `uv run pytest -q tests/test_workspace_context.py
tests/test_workspace_e2e.py tests/test_bots_context.py
tests/test_bots_context_fixes.py tests/test_memory_active.py
tests/test_memory_task_habits.py tests/test_context_budget.py
tests/test_agent_loop.py tests/test_cli.py tests/test_chat_meter.py
tests/test_memory_tools.py tests/test_memory_fallback_safety.py` →
**199 passed** (`/tmp/thyca-ws3.log`); `git diff --check` sạch. WS-2 broad
đã xanh trước đó: 283 passed (`/tmp/thyca-ws2-broad.log`).

Review-fix batch (provisional — chờ lead review):

- C) `Hit.chat_session` thêm cuối từ cột `chunks.chat_session` có sẵn
  (`SELECT c.*`/`SELECT *`), additive-only. `lexical_search` lọc candidates
  theo view SAU dedup, TRƯỚC `[:limit]` và usage recording; `recent` lấy
  pool `CANDIDATE_CAP` rồi filter rồi clamp — không cap mới, không đụng
  ranking. `tasks()` lọc theo `chat` (MAX(chat_session)). `get()` resolve
  provenance từ `chat_session` indexed hoặc `parse_heading` của heading
  đầu tiên thật — KHÔNG regex cả body; selector `canonical#` bypass
  daily-origin check; deny trước usage/TTL.
- D) Service `_leased` reload WindowRef SAU khi acquire lease (fresh);
  `runtime.run_bot_turn_detailed` luôn normalize view đúng target
  (reuse chỉ khi `bot_id`+`window_id` khớp, kể cả claimed path);
  `_window_for_room` + `MemoryView._origin_info` chỉ nuốt
  `SessionNotFound`/missing — corrupt/permission raise; `run_turn`/
  CLI dùng `bound_memory_view` contextmanager (reset cả exception).
- A) `WorkspaceContextBuilder.build` nhận replace thật: soul/identity/
  skills/team + memory.read=false suppress toàn bộ memory section
  (today/elsewhere/bot_user/memory_tasks/recalled) — comment cũ MOVE
  nguyên vào builder. Runtime dùng một builder call trước flush compact
  prediction (channel brief được tính vào prediction). run_turn/CLI/
  `_overhead_tokens` (meter) cùng gọi builder với typed inputs; meter
  bound view cục bộ, không global leak.
- B) `OpenTask.memory_task_id` (cuối, optional) là link tường minh;
  `project_work_items` đọc catalog schema thật (`task_id`, `title`,
  `latest_session_id`, `status`), merge chỉ khi link khớp `task-<8hex>`;
  title giống task-id KHÔNG merge; runtime-only giữ `work_id=None`.
  Builder nhận `runtime_tasks`/`memory_catalog`, project + prioritize
  current window, render `<open_tasks>`/`<memory_tasks>` từ projection —
  một nguồn, giữ provenance (`runtime_task`/`memory_row`). Hook mới
  `runtime.memory_task_catalog` trả rows thật từ `facade.tasks()`.
- `BotTurnContext.window` populate từ view bound (impl dùng `_window`
  của builder call).
- E) Tests thật: checkpoint `kind="checkpoint"` + task canonical; recall
  armed `_reinject_pending` + spy callback chứng minh callback chạy trong
  view; candidate-limit (private outrank public, limit=1 vẫn trả public);
  canonical `canonical#soul` get OK trong group; `get` deny session id
  private; meter: filtered hot có channel, thiếu PRIVATE, khác
  unfiltered peek.

Verify: `uv run pytest -q tests/test_workspace_context.py
tests/test_workspace_e2e.py tests/test_context_budget.py
tests/test_bots_context.py tests/test_cli.py tests/test_chat_meter.py
tests/test_memory_tools.py tests/test_memory_fallback_safety.py
tests/test_memory_task_habits.py` → **123 passed**
(`/tmp/thyca-ws3-review.log`); adjacent `test_chat_app.py
test_channel_webui.py test_bots_runtime.py test_bot_workspace.py
test_serve_chat.py` → **147 passed**; `git diff --check` sạch.

Doc cũ cần sửa khi vào phase sau: `team-context` mô tả budget per-session
độc lập — target nay là projection theo workspace; `memory-task-habits`
nói "private title index ở chỗ khác ok" — không còn đúng với group window.

### WS-4 — Channel actions + private follow-up (PROVISIONAL — awaiting review)

Đã nối store, inbound DM, channel actions và consent native. Các gate
focused/regression dưới đây đã qua; chưa có full-suite hoặc independent
review, chưa được coi là ready to merge.

- `thyca/bots/private_followups.py`: `PrivateFollowupStore` + `Pending` —
  atomic+lock 0600; open→waiting_private→received→resumed|done|cancelled|
  expired; terminal immutable; idempotency trên create/receive/decide/
  approve với payload-equality check; validation toàn bộ field types/
  enums/id; tối đa 1 active; raw answer không lưu (chỉ ref).
- `thyca/bots/channels.py`: ChannelAdapter protocol + ChannelRegistry +
  ChannelActionService (validate kind/caps/length).
- `thyca/bots/channel_tools.py`: `ask_private` + `private_followup` với
  schema/mô tả lead-authored; dm_resolve dep giải DM thật (không phải
  current window); origin phải group + sender là participant; ownership
  check qua task_owners registry + legacy earliest-entry adoption
  (singleton only, global owner lookup trước); dm_post ok-check trước
  mark_waiting; group chỉ nhận `Đang chờ thông tin riêng.`;
  `memory_remember(new_task)` wrapped để record owner từ ctx.sender_id.
- `workspace_store.py`: `record_task_owner`/`task_owner`/
  `task_owner_fallback` — earliest Markdown heading `meta.task` match,
  `meta.bots` singleton concrete only; global cross-bot registry scan.
- `workspace_turns.py`: `_consume_dm_pending` — DM window + bot lease +
  waiting_private → received, event_id idempotency, trước executor.
- `builder` + `ActiveSnapshot.private_followup` + PromptManager section:
  DM `pending_private={id} work_id={wid} state={st} origin_window={ow}`;
  group `work_id={wid} state=waiting_private`; probe wired runtime+meter.
- `sessions_api.py`: `POST /api/sessions/<dm>/private-followup` —
  pending+revision+DM validation, two-stage approve (record auth → post
  stored `proposed_redacted` vào origin → finalize resumed), revalidate
  participant+unpaused, stale revision denied, keep_private clears
  proposal+approval, cancel any nonterminal.
- `chat_app.py`: `post_bot_room_message`, `post_bot_dm_card`,
  `dm_post` `meta` kwarg, AdminDeps `dm_resolve`/`dm_card_post`/`room_post`.
- UI: `questionCard` carry `private_followup={pending_id,revision}` →
  choiceChips `onChoice(label,metadata)` → sendChoiceText →
  POST consent endpoint + reload; normal cards giữ nguyên label-only.
- `tasks.py`: `task_open` carry `memory_task_id` explicit (no title infer).

Gate: `uv run pytest -q tests/test_private_followups.py
tests/test_workspace_e2e.py tests/test_workspace_context.py
tests/test_bots_dm.py tests/test_bots_admin.py tests/test_ask_remember.py
tests/test_bots_status.py tests/test_team_frontend.py
tests/test_serve_chat.py tests/test_chat_app.py tests/test_bots_tasks.py
tests/test_bots_bus_refs.py tests/test_chat_meter.py
tests/test_bots_context.py tests/test_memory_tools.py
tests/test_memory_fallback_safety.py tests/test_memory_active.py
tests/test_bots_runtime.py tests/test_webui_live_recovery.py` →
**366 passed** (`/tmp/thyca-ws4-complete.log`); `git diff --check` sạch;
`node --check` các file JS sạch. Full-suite chưa chạy.

WS-4 completion pass (lead store fixes landed; integration + tests
implemented, 377 passed → `/tmp/thyca-ws4-final.log`):

- `thyca/bots/channels.py`: `ChannelAdapter.land(action,*,ctx) -> dict`,
  `ChannelActionService.dispatch(action,*,channel_id,ctx)` — trusted
  ctx, validates sender/caps/target; real `WebUIOutboundAdapter` in
  `app/webui_channel.py` holds the low-level callbacks (dm_post/
  dm_card_post/room_post/card_post/session_for); `_WebUIAdapter` stub
  removed. `_deps.channel_actions` wired after construction;
  `_outbound` helper routes `message_user` (post→own DM via dm_resolve)
  and `ask_private`/`room_post` calls through dispatch.
- `thyca/app/private_followups.py`: `PrivateFollowupService.decide` —
  sync facade over `app._submit`; `_decide` loads DM → bot async lease
  → per-bot `private_delivery.lock` → re-reads pending under lock →
  typed errors (400/404/409/503). `approve`: records revision, revalidates
  origin participant + unpaused card, posts stored `proposed_redacted`
  once with `pending_id+proposal_revision+kind="private_resume"+work_id`
  marker, then `finalize_resumed` — resume only after the row lands.
  Route is a thin parser over the facade; `traceback` kept for 503s.
- `post_bot_room_message(...,meta=)`: marker scan of persisted room
  under the delivery lock — dedupes crash-between-post/finalize retries.
  `post_bot_dm_message` dedupes `kind="private_question"` on
  `pfq_marker`. `post_bot_dm_card` for the consent preview.
- Inbound `_consume_dm_pending` moved post-append (user row persisted
  first, `answer_ref` = real row index, retry-safe via `event_id`
  idempotency, never on `retry=True`); `turn_event.py` ContextVar carries
  the trusted ChannelEvent for user-row metadata + pending consume;
  `BotTurnContext.pending_private_id` populated from the store at ctx
  creation.
- `work_items.overlay_private_followups`: DM maps ready/received;
  group sees only `work_id=waiting_private`. `app.cancel` closes the
  own-room pending only.
- Frontend: `option.id` → `metadata.choice` (allowlist only, no label
  mapping), busy guard, `el.status` failure line, `loadSession` on
  success; ordinary cards unchanged.
- `tasks.derive_open_tasks` carries `memory_task_id` from task_open args;
  `memory_remember` wrapper records owner on `new_task` success.
- WS-3 safety: `Bus.drain_matching` snapshot + restore on predicate
  crash (no envelope loss); `PathGuard._group_sensitive` denies raw
  memory/session/workspace/private paths + USER.md/sqlite in group
  view only; `strip_auto_habits` projects AUTO blocks out of
  `hot.user`/`hot.bot_user` and canonical `memory_get` for group
  (exact markers, no keyword scan).
- Tests: `tests/test_private_followup_integration.py` (10 tests: ready
  zero-conclusion, stale/wrong-DM/404/400 errors, terminal-late-DM,
  retry no-reconsume, membership-change reject, cancel-own-pending,
  ask-from-DM denied, drain crash restore, group path denies,
  habits strip); updated `test_private_followups` for exact-replay
  + finalize-marker + invalid-input coverage.

Kết quả sau các lượt review và sửa integration:

- Consent chạy trên app loop dưới bot lease và delivery FileLock.
  Membership được kiểm tra trước authorization. Mọi lựa chọn kiểm tra
  revision; lỗi public dùng thông báo cố định. Test crash sau khi đăng
  nhưng trước finalize chứng minh restart/retry không đăng trùng.
- Production `message_user`, `ask_user`, `ask_private` dùng channel
  dispatch. Callback fallback giữ tương thích caller không có registry.
- Root và child đều có test scripted `ToolCall` qua gateway: mở memory
  task → group hỏi riêng → DM nhận trước executor → proposal → user
  duyệt → group chỉ nhận text đã duyệt.
- `app.cancel` có test DM idle, active turn và origin group; group có
  nhiều pending không rõ task trả conflict, không tự chọn.
- Fake DOM click chạy module JS thật, kiểm tra option ID, revision,
  endpoint, busy guard, lỗi HTTP và reload. Chưa có Chrome product-flow;
  fake provider không chứng minh model thật hiểu consent.
- `task_open(memory_task_id=...)` nhận link explicit qua schema/gateway;
  không suy từ title. DM creation giữ origin/pending windows khỏi
  cleanup blank session.

Gate mới nhất:

- Required focused command (4 files) → **64 passed**,
  `/tmp/thyca-ws4-required-focused.log`.
- Required regression command (11 files) → **219 passed**,
  `/tmp/thyca-ws4-required-regression.log`.
- `uv run pytest -q tests/test_private_followup_integration.py
  tests/test_private_followup_ui.py` → **32 passed**,
  `/tmp/thyca-ws4-required-proof.log`.
- `git diff --check` sạch. WS-5 đã triển khai xong; WS-6 xong gồm
  cutover canonical + review độc lập (chi tiết mục WS-6); WS-7 chưa.

- Chuẩn hoá `message_user`/`ask_user` qua ChannelActionService, giữ tên
  tool làm compatibility wrapper.
- Thêm `ask_private` trong `bots/channel_tools.py` (tách khỏi
  `admin_tools.py`), identity/target lấy từ turn context.
- Thêm pending store/state machine, DM answer resolution, share policy,
  status `waiting_private` cho UI qua payload cộng thêm.
- Chỉ coordinator/owner của WorkItem được mở pending; bot khác chỉ thấy
  trạng thái chờ.

Gate: E2E group → DM → group không lộ raw answer; duplicate/cancel/late
answer không nối sai task.

### WS-5 — Scheduler, status, trace và UI nối lại

Đã triển khai và qua focused gate; full-suite và independent review vẫn
chưa chạy.

- `WorkspaceTurnService.run_system` tạo actor `system/scheduler`, gửi vào
  DM cố định và bỏ qua nếu bot đang giữ lease ở cửa sổ khác. Scheduler
  không được tiêu thụ pending như một câu trả lời DM của user.
- Session wire và trace cộng `channel`, `window_id`, `work_id`; field
  cũ và positional constructor của `TurnSummary` được giữ tương thích.
  Root/child assistant metadata đi qua Stage/Observe; work ref chỉ lấy
  từ event tin cậy khớp cửa sổ thực thi.
- Public status khôi phục từ persisted pending sau restart. Trạng thái
  riêng chỉ hiện thông báo chờ chung, không có question/answer/proposal.
  Thứ tự ưu tiên thinking/running/error/offline và layout được giữ.
- Gate sau review: 11 suite liên quan → **211 passed**,
  `/tmp/thyca-ws5-reviewed.log`. Bổ sung kiểm tra root/child qua
  `test_workspace_metadata.py`, `test_agent_stage.py`, `test_agent_loop.py`
  → **42 passed**, `/tmp/thyca-ws5-final.log`.
- Chrome đã chạy group → DM → click duyệt → group với provider scripted
  trên server fixture riêng, không gọi provider thật. Group chỉ nhận
  bản đã duyệt; chuỗi riêng tư không có trong ba group provider payloads.
  Các request luồng consent đều 200, console không có warning/error.
  Server user `19502` không restart hay sửa dữ liệu trong lượt này.
  Đây là kiểm tra runtime/UI, không chứng minh chất lượng quyết định của
  model thật.

- Scheduler phát `system` event vào workspace với target policy thay vì
  tự tìm solo room.
- AgentStatus snapshot theo bot + window + work item; UI dùng lại layout,
  chỉ nối thêm trạng thái chờ riêng và channel/window metadata.
- Trace room meta cộng `channel`, `window_id`, `work_id` nếu có; không
  đổi field cũ (`session_id`, `bot_id`, `kind`, `dm`).

Gate: Dashboard/Trace hiện tại không regress; phiên nhóm test trên Chrome
thấy bot resume đúng task.

### Historical WS-6 — canonical timeline shadow/cutover proposal (SUPERSEDED)

Đã triển khai đầy đủ gồm cutover canonical per window, qua review độc
lập (`code-reviewer` → các mục Important đã sửa và có test). Chi tiết:

- `thyca/bots/timeline.py`: `WorkspaceTimeline` — journal append-only
  tại `bots/<id>/workspace/timeline.jsonl` (FileLock reentrant,
  0600/0700, strict schema `v=1`, seq liên tục, event_id duy nhất;
  validate trên MỌI lần đọc qua `_iter_events`; file lỗi →
  `TimelineError`, không tự vá). Event kinds: `bootstrap`, `message`,
  `view_reset`, `parity`. `os.write` loop + fsync; `_record` re-scan
  khi file lớn hơn snapshot để tránh seq conflict giữa hai instance.
- `bootstrap` chỉ ghi refs (`legacy_session_id`, `legacy_count`,
  `legacy_digest`, `compaction_ref` chỉ tới hàng `[compaction:`,
  `checkpoint_ref` tới kết quả `memory_remember(kind='checkpoint')`
  thành công gồm `session_id`+`task_id`+`origin_window`) — không import
  raw transcript cũ; `legacy_session_id` dùng đúng internal session id,
  không nhầm room/window id.
- `project(window, legacy_store, session_id)` dựng view theo cửa sổ từ
  basis gần nhất (view_reset rồi đến bootstrap, resolve bootstrap qua
  digest prefix); DM/group không trộn.
- `ShadowSessionStore` bọc internal SessionStore của bot con khi có
  window tin cậy: dual-write legacy→journal nguyên tử dưới journal
  lock; passthrough session lạ; meta-ops (`append_meta`,
  `append_title_if_missing`, `append_naming_attempted`, `append_bots`,
  `append_dm`) journal `view_reset` mang meta hiệu lực đọc ngược từ đĩa
  (khớp preserve-on-None của base rewrite); `rewrite` khớp signature
  base (positional meta được).
- `reconcile` giữ journal lock trọn một pass, từ chối trên window
  canonical, tail-append khi legacy là prefix của projection, rebase
  khi `TimelineReferenceError` hoặc prefix diverge; lỗi corrupt/
  permission propagate. Root `thyca` không shadow ở phase này; room
  synthetic A2A không có `WindowRef` tin cậy → không tạo artifact.
- `parity` ghi marker {equal, legacy_tokens, projected_tokens,
  session_id, head_seq} sau mỗi turn shadowed — không chứa nội dung;
  propagate trên turn sạch, không bao giờ che lỗi turn đang bay
  (flag `turn_failed` cục bộ, không dùng `sys.exc_info()`).
- `thyca/bots/timeline_parity.py`: `check_projection` — parity đầy đủ
  qua đường assemble thật (hot snapshot, tools, wire payload, metadata)
  cho audit; không dùng làm gate cutover (by design).
- Cutover per (bot, window): `cutover.json` atomic + `mode()`,
  `promote()` (giữ journal lock suốt parity→flag để đóng TOCTOU với
  dual-write pair; gate = parity live bắt buộc `equal`), `demote()`
  (bắt buộc canonical — từ chối trên shadow vì projection là subset
  sẽ truncate tail chưa journal; materialize projection về legacy
  trước rồi mới xoá flag). Mọi entry-point từ chối
  `CanonicalSessionStore` làm `legacy_store`.
- `CanonicalSessionStore`: canonical window đọc từ projection, ghi chỉ
  vào timeline (`message`/`view_reset`), file legacy đóng băng byte-
  perfect; `create`/`latest`/`list_paths` theo contract canonical;
  session lạ passthrough.
- Runtime `_run_bot_turn_detailed`: child bot + window tin cậy →
  `WorkspaceTimeline` + `CanonicalSessionStore` (canonical) hoặc
  `ShadowSessionStore` + reconcile + parity finally (shadow);
  `mark_turn_error` trên internal session để lỗi turn được journal
  (`view_reset`); `SYSTEM_BOT_ID` thay literal.
- Gate: timeline/parity/cutover/E2E → **49 passed**; regression WS liên
  quan → **338 passed**; full suite → **1736 passed**;
  `git diff --check` sạch. Chưa promote bot thật nào trong dữ liệu live;
  operator path đã có qua Thyca-only `bot_workspace_promote`/
  `bot_workspace_demote` và lease-gated runtime methods (review fix
  2026-10-19; full suite sau fix: 1741 passed).

Residual đã biết (accepted, ghi lại để phase sau xử lý):
- `demote` trên window canonical mà bootstrap basis file đã bị xoá →
  `TimelineReferenceError` khi project → cần đường recovery ngoài API
  (không có caller thật nên chưa vá).
- Crash giữa legacy write và journal append trong shadow mode vẫn nhờ
  `reconcile` vá ở turn sau; canonical mode không reconcile (thiết kế).
- Reader lock-free có thể thấy torn tail giữa chừng append → fail loud,
  đúng contract.
- `check_projection` chưa có production caller (audit recipe, cố ý).

### Historical WS-7 — legacy cleanup proposal (SUPERSEDED; not current work)

The remaining-work status and migration tasks recorded below were superseded
by the canonical-only hard cut above.

- Theo phạm vi user đã chốt, bridge Discord/MCP inbound chưa thuộc đợt
  triển khai này. Contract kênh giữ khả năng nối bridge sau; không thêm
  subscription/reconnect transport hay sửa `thyca/tools/mcp.py`.
- Trạng thái hiện tại: KHÔNG xoá được đường cũ nào — mọi window vẫn ở
  shadow mode theo mặc định dù operator promote/demote đã có production
  caller, `room_sessions.json` vẫn là nguồn registry hiện hành và
  internal JSONL là đường ghi chính của shadow dual-write. Các mục dưới
  giữ nguyên là điều kiện xoá, chưa một mục đủ điều kiện.
- Còn cần trước khi đóng WS-7: promote có kiểm soát từng bot/window thật
  và theo dõi parity; demote-recovery khi bootstrap basis bị xoá; quyết
  định có đưa metadata vào parity gate hay giữ
  `check_projection.metadata_equal` audit-only.

## File phải đại tu

| File | Mức | Việc chính |
|---|---|---|
| `thyca/app/chat_app.py` | Đại tu/tách | Tách room lifecycle, turn routing, DM/card landing, bus recording, sweep/flush, overhead sang service/adapter; cuối cùng còn facade tương thích. |
| `thyca/bots/runtime.py` | Đại tu | Internal session + cursor chuyển vào workspace; prompt vá rời sang context builder; runtime thành executor của workspace. |
| `thyca/app/run_turn.py` | Đại tu, có thể xoá cuối | `TurnScope` string fields và đường Thyca riêng được thay bằng turn service/context typed. Chỉ xoá khi Thyca chạy cùng executor mà không đổi quyền root. |
| `thyca/bots/admin_tools.py` | Đại tu/tách | CRUD bot giữ lại; send/fetch/task_open/message_user/ask_user chuyển sang channel/work tools. |
| `thyca/bots/tasks.py` | Đại tu | Projection runtime task hợp nhất với memory task + private pending; có thể thành importer legacy. |
| `thyca/bots/status.py` | Đại tu | Trạng thái theo bot + window + work item; resolve waiting theo pending, không chỉ theo room. |
| `thyca/memory/active.py` + `thyca/llm/prompt_manager.py` | Đại tu | Từ snapshot string rời rạc sang nguồn dữ liệu cho context builder; lọc today/elsewhere theo visibility. Renderer giữ. |
| `thyca/agent/loop.py`, `assemble.py`, `context_budget.py` | Đại tu vừa | Loop nhận prompt/context đã dựng; budget chuyển ra builder. Nếu sang timeline, execution journal thay SessionManager trực tiếp. |
| `thyca/bots/room_context.py` | Đại tu | Thành window-delta source của projector; bỏ việc append synthetic row vào internal transcript sau cutover. |
| `thyca/bots/bus.py`, `artifacts.py` | Sửa vừa | Envelope thêm channel/window/work refs; giữ hop/artifact authorization. |
| `thyca/bots/context.py` | Đại tu | `BotTurnContext` thành typed actor/window/channel/work context; bỏ phụ thuộc `scope: Any`. |
| `thyca/sessions/models.py`, `store.py`, `manager.py`, `wire.py` | Sửa vừa | Window metadata cộng thêm (`channel`, external ref/visibility) và migration meta; JSONL cũ đọc được. |
| `thyca/serve/routes.py`, `sessions_api.py`, `bridge.py`, `turn_state.py` | Sửa vừa | WebUI adapter dùng service mới nhưng giữ route/payload/stream hiện tại. |
| `thyca/serve/trace.py`, `trace_api.py` | Sửa vừa | Gắn channel/window/work vào trace mà không phá filter cũ. |
| `thyca/bots/scheduler.py` | Sửa vừa | Phát system event theo target policy thay vì tự tìm solo room rồi gọi `app.turn`. |
| `thyca/app/toolchain.py`, `thyca/app/cli.py` | Sửa vừa | Wiring workspace/context builder; CLI là một channel adapter mỏng. |
| `thyca/memory/facade.py`, `jobs.py`, `tools/memory_tools.py` | Sửa vừa | Search/recall theo visibility + origin window; extractor gắn origin; checkpoint giữ nguyên contract. |
| `thyca/webui/pages/chat/team-data.js`, `team-sidebar.js`, `app.js`, `composer.js`, `turn-follow.js`, `chat-status.js` | Nối lại | Đọc thêm channel/window/waiting state nếu backend cộng field; không redesign UI. |

## File thêm mới

| File | Trách nhiệm |
|---|---|
| `thyca/core/channel.py` | Type thuần cho channel/window/event/action/capability. |
| `thyca/bots/workspace.py` | Aggregate + per-bot turn queue + window binding. |
| `thyca/bots/workspace_store.py` | Đọc/ghi atomic registry/cursors; migration từ `room_sessions.json`. |
| `thyca/bots/workspace_context.py` | Dựng channel view, memory/work index và context budget. |
| `thyca/bots/work_items.py` | Projection/state hợp nhất runtime task + memory task. |
| `thyca/bots/private_followups.py` | Pending DM state machine + share policy. |
| `thyca/bots/channels.py` | Adapter protocol/registry và action execution policy. |
| `thyca/bots/channel_tools.py` | `ask_private` và wrapper tương thích cho message/ask/send. |
| `thyca/bots/timeline.py` | Canonical append-only workspace timeline, projection input và checkpoint ranges. |
| `thyca/app/webui_channel.py` | Adapter giữa room session/API hiện tại và channel contract. |
| `tests/test_bot_workspace.py` | Registry, queue, migration, restart. |
| `tests/test_workspace_context.py` | Visibility, cross-window recall, budget. |
| `tests/test_private_followups.py` | Pending lifecycle, consent/redaction, late/cancel. |
| `tests/test_channel_webui.py` | Contract WebUI adapter và payload tương thích. |
| `tests/test_workspace_e2e.py` | DM checkpoint → group hỏi; group → DM riêng → resume. |

`thyca/bots/timeline.py` nằm trong phạm vi đích đến. Kênh MCP inbound là
file/phase riêng sau contract này.

## File/đường cũ xoá sau cutover

Không xoá ở phase đầu. Chỉ xoá khi caller đã chuyển và parity test xanh:

- Logic `_room_map_path/_read_room_map/_write_room_map/_internal_id_of`
  trong `runtime.py` sau khi WorkspaceStore đọc được format cũ.
- Synthetic persisted `room_context` row trong internal transcript khi
  projector cung cấp delta dạng ephemeral; dữ liệu cũ vẫn phải đọc/lọc được.
- `ChatApp.post_bot_dm_message/post_bot_card` sau khi ChannelActionService
  là đường duy nhất; tool name public giữ wrapper đến hết migration.
- `TurnScope.hot_tasks/hot_memory_tasks/hot_bot_user` và các `replace(hot,…)`
  rải rác sau khi context builder là nguồn duy nhất.
- `thyca/app/run_turn.py` chỉ khi Thyca root chạy qua cùng workspace executor
  mà vẫn giữ quyền/tool snapshot hiện tại.
- `derive_open_tasks` dạng độc lập nếu WorkItem projection đã bao phủ và có
  importer cho transcript cũ.
- `thyca/agent/context_budget.py` có thể gộp vào workspace context sau khi
  AgentLoop không còn tự tính signal; không xoá nếu vẫn là pure helper dùng
  chung.
- Không xoá `thyca/bots/bus.py`, `artifacts.py`, `room_context.py`,
  `sessions/*` hay memory store: chúng là nền được tận dụng.

## Migration và rollback

- Room session ID hiện tại trở thành `window_id`; channel mặc định `webui`.
- DM marker/bots meta cũ giữ nguyên; field channel chỉ cộng thêm.
- `room_sessions.json` được đọc tương thích cả value string cũ và dict hiện
  tại. Migration ghi format mới bằng atomic write, giữ backup trong thư mục
  bot trước cutover.
- Internal session cũ không bị xoá. Nếu rollback trước WS-6, đường cũ vẫn
  đọc được vì room/internal JSONL không đổi format.
- Memory Markdown + SQLite index không migrate. Legacy entry không có
  visibility mới được suy từ owner + `chat` origin; không suy diễn là
  shareable ở group.
- Mỗi phase có feature gate ở wiring layer để quay lại đường ChatApp cũ
  mà không phải revert dữ liệu. Không dùng hai đường ghi room transcript
  cùng lúc sau cutover.

## Kiểm chứng bắt buộc

Unit:

- Channel contract validation, idempotency key, capability.
- Workspace registry migration từ cả hai format `room_sessions.json`.
- Context projection: DM không lọt title/content vào group; group public
  history không lọt nguyên văn vào DM nếu không liên quan; other-bot
  private memory không xuất hiện.
- Pending private: resolve đúng một task, duplicate, cancel, late answer,
  restart, hết hạn nếu WS-0 chọn expiry.
- WorkItem: runtime span + memory task link, latest status, coordinator.

Integration:

- DM checkpoint → group hỏi lại (case đã test live ngày 2026-10-19).
- Group cần thông tin riêng → DM trả lời → resume bằng bản redact.
- Hai cửa sổ của cùng bot gửi gần đồng thời: không split-brain, không mất
  cursor; bot khác vẫn chạy song song.
- Compact ở một cửa sổ không xoá work state của cửa sổ khác.
- Scheduler/system event vào đúng target window.
- A2A delegate: refs/artifact authorization và hop limit giữ nguyên.

Commands theo phase:

```bash
uv run pytest -q tests/test_bot_workspace.py tests/test_workspace_context.py \
  tests/test_private_followups.py tests/test_channel_webui.py
uv run pytest -q tests/test_bots_context.py tests/test_bots_runtime.py \
  tests/test_bots_tasks.py tests/test_bots_bus_refs.py tests/test_bots_dm.py \
  tests/test_chat_app.py tests/test_serve_chat.py tests/test_agent_loop.py \
  tests/test_context_budget.py tests/test_memory_task_habits.py
uv run pytest -q
git diff --check
```

Product-flow trên Chrome DevTools ở port mặc định hiện hành: tạo group,
hỏi xuyên DM/group, kiểm tra network/console và screenshot. Test bằng fake
LLM không được coi là bằng chứng model thật hiểu private handoff.

## Quyết định đã chốt ngày 2026-10-19

1. **Execution store:** canonical timeline là đích đến ngay từ đầu của
   kiến trúc mới; execution transcript theo cửa sổ chỉ là cầu migration.
2. **Concurrency:** một bot chỉ chạy một lượt tại một thời điểm trên mọi
   cửa sổ; bot khác vẫn chạy song song.
3. **Phạm vi kênh phase đầu:** chỉ WebUI native. Discord inbound qua MCP
   bridge là phase riêng sau khi contract ổn định.
4. **Resume sau DM:** bot tự quyết quay lại group hay chỉ đánh dấu ready,
   trong giới hạn share policy của pending. Không có consent chia sẻ thì
   bot không được mang nội dung/kết luận riêng sang; mơ hồ thì hỏi tiếp
   ở DM.

## Quyết định đã chốt trước WS-2 (triển khai 2026-10-19)

1. **Canonical WorkItem ID:** memory `task-<8 hex>` là canonical id cho
   việc xuyên lượt; runtime span chỉ là execution ref. Không mint
   `work-...` mới.
2. **Thyca root:** `run_turn.py` giữ nguyên làm adapter tương thích cho
   đến khi permission/tool parity với workspace executor được chứng minh.

## Tiến độ triển khai

### WS-0 — done 2026-10-19 (branch `feat/bot-workspace-channels`)

- Baseline command (trước khi đổi code):
  `uv run pytest -q tests/test_bots_context.py tests/test_bots_runtime.py
  tests/test_bots_tasks.py tests/test_bots_bus_refs.py tests/test_bots_dm.py
  tests/test_chat_app.py tests/test_serve_chat.py tests/test_agent_loop.py
  tests/test_context_budget.py tests/test_memory_task_habits.py
  tests/test_bots_status.py tests/test_turn_stream.py
  tests/test_compaction_llm.py tests/test_trace.py`
  → **251 passed**. Raw log: `/tmp/thyca-ws-baseline.log`.
- Characterization mới `tests/test_channel_webui.py` (4 tests, xanh trước
  khi đổi execution code):
  - `test_root_dm_keeps_identity_and_full_tool_scope` — DM Thyca, reply
    không gán bot_id con, tool snapshot còn `bot_create`.
  - `test_child_dm_reply_recorded_with_bot_id_and_internal` — reply con
    ghi vào DM kèm `bot_id`, internal session bot×window khớp room map,
    child snapshot không có `bot_create`.
  - `test_group_mention_runs_child_not_root` — `@coder` trong group
    `[thyca, coder]` chọn con, không chạy root.
  - `test_retry_keeps_single_user_row_and_new_reply` — retry giữ đúng 1
    user row và reply mới.
- Flow còn lại đã có suite: A2A (`tests/test_bots_bus_refs.py`,
  `tests/test_bots_context.py`), scheduler DM
  (`test_scheduled_result_lands_in_dm_not_in_room` trong
  `tests/test_bots_dm.py`), cancel/follow (`tests/test_chat_app.py`,
  `tests/test_turn_stream.py`), compact manual/auto
  (`tests/test_compaction_llm.py`, `tests/test_bots_context.py`), trace
  by bot (`tests/test_trace.py`, `test_trace_groups_by_bot` trong
  `tests/test_bots_rooms.py`).

### WS-1 — done 2026-10-19 (cùng branch, chưa commit)

- `thyca/core/channel.py`: `ChannelRef`, `WindowRef`, `ActorRef`,
  `ChannelCapabilities`, `ChannelEvent`, `ChannelAction` — frozen
  dataclass stdlib, validate kind/visibility/ts (`parse_ts` của
  `core.protocol`) và `work_ref` `task-<8 hex>`; `to_dict/from_dict`
  chỉ trên `WindowRef` (persistence).
- `thyca/bots/workspace_store.py`: `WorkspaceStore` + `WindowBinding`;
  registry mới ở `bots/<bot>/workspace/registry.json`, đọc fallback cả
  hai shape `room_sessions.json` (string + dict), cursor/internal ref giữ
  nguyên, không tạo internal session khi đọc, legacy window chưa resolve
  metadata (`window=None`, không đoán dm/group), write atomic qua
  `atomic_write_text` + FileLock, backup `room_sessions.json.bak` một
  lần không ghi đè, unknown metadata được giữ, registry mới hỏng →
  `WorkspaceStoreError` (không reset), bot_id qua `_NAME_RE`/`NAME_MAX`.
- `thyca/bots/workspace.py`: `BotWorkspace` + `WorkspaceRegistry`
  (singleton per instance, keyed by bot). Turn queue để WS-2.
- `thyca/bots/runtime.py`: constructor giữ `WorkspaceRegistry`
  (`runtime.workspaces`); `_write_room_map` mirror nguyên mapping vào
  shadow qua `sync_legacy` — không đoán `WindowRef`, không đổi routing.
- Verify (tại thời điểm WS-1, trước các thay đổi WS-2): `uv run pytest -q
  tests/test_bots_context.py tests/test_bots_runtime.py
  tests/test_bots_dm.py tests/test_bots_rooms.py
  tests/test_bot_workspace.py tests/test_channel_webui.py` →
  **75 passed**; `git diff --check` sạch.
- Chưa làm (đúng scope): turn queue, window metadata thật từ adapter,
  context/turn routing, full-suite run (giữ cho gate cuối).

Các phase sau trong `team-context.md`, `compaction-hybrid.md` và
`memory-task-habits.md` phải được đối chiếu lại để tránh hai nguồn quyết
định mâu thuẫn.
