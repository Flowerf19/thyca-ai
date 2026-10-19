---
status: draft
created: 2026-10-19
last_updated: 2026-10-19
---

# Bot Workspace + Channel Windows — kiến trúc mới

## Trạng thái và phạm vi

Plan này là **draft để chốt quyết định**, chưa phải lệnh triển khai. Nó thay
cách nhìn hiện tại “mỗi lượt chạy trong một internal session của bot theo
phòng” bằng mô hình:

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
  private follow-up. Canonical public id là điểm cần chốt ở WS-0.
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

Gate: toàn bộ serve/chat/bots tests hiện tại xanh, stream/cancel/retry
không đổi thứ tự event.

### WS-3 — Context builder và unified work index

- Gom logic vá `ActiveSnapshot` từ ChatApp/BotRuntime/run_turn/CLI vào
  `workspace_context.py`; đầu ra tạm thời vẫn là `ActiveSnapshot`.
- Hợp nhất hiển thị runtime task + memory task bằng `work_items.py`, giữ
  hai ID nguồn làm refs đến khi WS-0 chốt canonical ID.
- Áp visibility filter cho today/elsewhere, post-compact recall và
  checkpoint index theo window hiện tại.
- Dời việc tính `context_budget` từ AgentLoop sang context builder;
  AgentLoop vẫn giữ shrink/backstop theo assembled payload.

Gate: prompt snapshot tests chứng minh DM không lọt vào group, group delta
không lọt DM, budget vẫn tính đủ hot + tools + pending.

### WS-4 — Channel actions + private follow-up

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

- Scheduler phát `system` event vào workspace với target policy thay vì
  tự tìm solo room.
- AgentStatus snapshot theo bot + window + work item; UI dùng lại layout,
  chỉ nối thêm trạng thái chờ riêng và channel/window metadata.
- Trace room meta cộng `channel`, `window_id`, `work_id` nếu có; không
  đổi field cũ (`session_id`, `bot_id`, `kind`, `dm`).

Gate: Dashboard/Trace hiện tại không regress; phiên nhóm test trên Chrome
thấy bot resume đúng task.

### WS-6 — Canonical timeline shadow + cutover (đích đến đã chốt)

- Shadow-write workspace timeline từ các turn mới; projector dựng prompt
  từ timeline và so token/nội dung với đường cũ.
- Bootstrap bằng checkpoint + refs tới room/internal session cũ; không
  import mù toàn bộ internal transcript vì có room-context/mailbox trùng.
- Cutover theo từng bot, rollback về execution transcript cũ khi projector
  lệch. Chỉ sau parity mới ngừng ghi internal session mới.

Gate riêng: không bắt đầu nếu WS-1..5 chưa ổn định.

### WS-7 — Kênh ngoài kiểu MCP + dọn legacy

- Thiết kế MCP channel bridge riêng: subscription/notification inbound,
  idempotency, reconnect và mapping identity. Không nhét vào
  `thyca/tools/mcp.py` hiện chỉ là tool client.
- Xoá đường cũ theo danh sách dưới khi không còn caller/test legacy cần nó.

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

## Quyết định còn mở trước WS-2

1. **Canonical WorkItem ID:** dùng memory `task-...` khi việc xuyên lượt,
   runtime span chỉ là execution ref (đề xuất), hay mint `work-...` mới?
2. **Thyca root:** đưa vào workspace executor như các bot ngay phase đầu,
   hay giữ `run_turn.py` làm adapter tương thích đến phase cleanup?

Khi hai điểm còn mở được chốt và user ra lệnh triển khai, đổi status plan
sang `in-progress` và làm theo WS-1 trở đi. Các phase sau trong
`team-context.md`, `compaction-hybrid.md` và `memory-task-habits.md` phải
được đối chiếu lại để tránh hai nguồn quyết định mâu thuẫn.
