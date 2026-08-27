---
status: in-progress
created: 2026-08-26
last_updated: 2026-08-27
---

# Trace theo model · lượt · token (cache/in/out) · chi phí — bề mặt Sổ tay

## Summary

Chat hiện có operational trace sống (staff C major + `TurnEvent` NDJSON) nhưng **không giữ lịch sử** và **không giữ token/chi phí**. `ChatReply.usage` lấy từ provider nhưng bị bỏ ở `Observe` — session JSONL chỉ lưu `content`/`tool_calls`, không có `meta.usage`. Màn Trace (`webui/js/data.js:130`, `webui/css`) vẫn là mock hai trang tĩnh theo `thyca-web-notebook-redesign.md`.

Mục tiêu: biến **Trace** thành LangSmith-thu-nhỏ đúng chất Thyca — giấy ấm / terracotta / notebook, không dashboard tối: **chia theo model, đếm lượt request, bóc token `in` / `cached` / `out`, tính chi phí**. Sống nhờ JSONL hiện có (markdown là nguồn sự thật cho memory, JSONL là nguồn sự thật cho hội thoại) — không thêm vector DB, không WebAudio, không thay `ThreadingHTTPServer`.

Success:

1. Mỗi LLM call (kể cả `session.naming`) lưu **normalized usage** + **model** + **latency_ms** vào `Message.meta` và do đó nằm trong JSONL; session cũ không có usage hiển thị `—`, không crash.
2. `GET /api/traces/stats` (và/hoặc `GET /api/sessions` mở rộng) trả aggregate **by model** và **total** mà WebUI dùng để render header Trace: `requests` · `prompt` (trong đó `cached`) · `completion` · `total` · `cost_usd` + latency trung vị. Unknown model → `cost_usd: null`.
3. Trace list lọc được theo **model**, **status** (ok/failed/loop_limit), khoảng ngày; detail một lượt hiện waterfall `assemble → think → act → observe` với latency bar, token từng round và staff khuông nhạc tĩnh (cùng grammar C major `I–vi–IV–V` của `thyca-operational-music-trace.md`, render bằng Bravura như `staff-map.js`/`staff-draw.js`).
4. Không lộ prompt/secret/tool args: API chỉ trả summary, `Message.content` vốn đã trong `get_payload`, cost là số đã sanitize. `python -m http.server --directory webui` vẫn chạy mock không API.
5. Mock tĩnh vẫn đẹp ở 320/375/414/768px, không horizontal overflow; `uv run pytest -q` không thêm failure ngoài baseline `test_debug_prints_prompt_flags`.

Tham chiếu LangSmith: cost tracking tự động từ token×price, phân tách `cache_read`/`text`/`image`, filter bar, project stats/dashboard, và run detail với inputs/outputs/timing/metadata (`docs.langchain.com/langsmith/cost-tracking`, `/log-llm-trace`, `/filter-traces-in-application`). Thyca mượn: **filter by model/attributes**, **aggregated metrics by model**, **run detail với timing + token breakdown** — nhưng áp lên **một bếp ấm duy nhất** (loop nhỏ, một `~/.thyca`, một user local).

Tham chiếu khuông nhạc: `thyca-operational-music-trace.md` đã chốt grammar (C major, 4/4, 16 ticks/measure, trailing-rest, cadence `V half → I half` cho completed và `V whole` cho failed, Bravura glyphs, 16-measure window). Trace lịch sử tái dùng **cùng mapper/renderer** — score được dựng lại từ `messages` (không persist `TurnEvent`), nên staff của lượt cũ vẫn cùng tiếng với staff sống.

## Quyết định đã chốt trong plan này

### 1. Trace gì

**Đơn vị:** một **turn** = một lần `POST .../turn` (hoặc `.../turn/stream`) — từ `turn.accepted` đến `turn.completed/failed`. Trong JSONL, turn được suy ra bằng cặp `user` → các `assistant`+`tool` tiếp theo cho tới `assistant` text cuối. `turn_index` là thứ tự turn trong session.

**Span trong một turn:**

| Span | Nguồn | Đo gì |
|------|-------|-------|
| `turn` | `AgentLoop.run` + `ChatApp._name_if_needed` | `started_at`, `ended_at`, `latency_ms`, `model` (lấy từ `Config.provider.model` tại thời điểm đó), `status` (`completed`/`failed`/`loop_limit`), `total_tokens`, `cost_usd` |
| `llm` (mỗi round) | `Think.think` | `round`, `model`, `latency_ms`, `prompt_tokens`, `cached_tokens`, `completion_tokens`, `total_tokens`, `reasoning_tokens?`, `tool_count`, `finish_reason` |
| `tool` | `Act._one` | `call_id` (public), `name` (public allowlist, như `events.py`), `latency_ms`, `ok` |
| `naming` | `ChatApp._name_if_needed` | `latency_ms`, `updated` — được tính là một `llm` call tách biệt với `model` giống turn, nhưng `kind: "naming"` để filter |
| `compact` | `Observe.compact` | `compacted: bool` |

Không stream `chain-of-thought`, không persist `arguments`/`tool result` ngoài content vốn đã trong JSONL, không log `apiKey`, path hay stack.

**Bóc token theo provider:**

- OpenAI (`/chat/completions`): `usage.prompt_tokens`, `usage.completion_tokens`, `usage.prompt_tokens_details.cached_tokens`, `usage.completion_tokens_details.reasoning_tokens`, `usage.total_tokens`
- Anthropic: `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens` → `cached_tokens`, `usage.cache_creation_input_tokens` gộp vào `prompt_tokens` (ghi chú riêng)
- Google: `usageMetadata.promptTokenCount`, `candidatesTokenCount`, `cachedContentTokenCount`
- Stub/unknown: `null` — UI hiện `—`

Chuẩn hóa ở `thyca/llm/*_chat.py` → `ChatReply.usage` luôn là `dict` với keys `prompt_tokens`, `completion_tokens`, `cached_tokens`, `total_tokens` (và optional `reasoning_tokens`). Các connect chưa implement trả `usage=None`.

### 2. Lưu ở đâu

**Không thêm sqlite/DB mới trong v1.** Dùng **session JSONL** làm source of truth, mở rộng `Message.meta`:

```json
{
  "role": "assistant",
  "content": "...",
  "tool_calls": [...],
  "ts": "2026-08-26T09:12:03Z",
  "meta": {
    "model": "gpt-4o-mini",
    "kind": "llm",
    "round": 2,
    "latency_ms": 1842,
    "usage": {
      "prompt_tokens": 812,
      "cached_tokens": 240,
      "completion_tokens": 31,
      "total_tokens": 843,
      "reasoning_tokens": 0
    },
    "cost_usd": 0.00042,
    "finish_reason": "tool_calls"
  }
}
```

- `tool` message giữ `meta: { "is_error": true }` như hiện tại; thêm optional `latency_ms` và `round` nếu cần waterfall.
- `cost_usd` tính tại thời điểm persist bằng `thyca/llm/pricing.py` (bảng tĩnh), làm tròn 6 chữ số. Thiếu giá → `null`.
- Tương thích ngược: `Message.from_dict` bỏ qua `meta` thiếu field; aggregate bỏ qua `null`.

**Bảng giá** (`thyca/config.py` + `thyca/llm/pricing.py`): `Config.pricing: dict[model_id, {input, cache, output}]` lưu USD/1M tokens — `cache` là giá cho phần prompt được cache hit (OpenAI `prompt_tokens_details.cached_tokens`, Anthropic `cache_read_input_tokens`, Google `cachedContentTokenCount`), không có cache output. Seed từ public pricing snapshot 2026-08 (ví dụ `gpt-4o-mini: input=0.15, cache=0.075, output=0.60`). `pricing.py` resolve theo thứ tự: `Config.pricing[model]` → builtin `DEFAULT_PRICES[model]` → `None` (unknown → `cost_usd=null`). `cost_for(model, usage, pricing_cfg)` tính `((prompt - cached)*input + cached*cache + completion*output) / 1e6`, làm tròn 6 chữ số. Giá `cache` mặc định ~0.25–0.5× `input`; nếu provider không phân biệt thì `cache = input`. Config thiếu `pricing` vẫn load (migrate rỗng), chỉnh giá bằng sửa `~/.thyca/config.json` không cần code review; API `/api/traces/stats` và persist đều dùng chung resolver nên không lệch.

**Latency** đo bằng `time.perf_counter()` trong `Think.think` (và `Act._one`, `ChatApp._name_if_needed`); không dùng wall-clock để tránh lệch khi đổi timezone.

### 3. API

Giữ nguyên `/api/sessions`, `/api/sessions/{id}`, `/api/sessions/{id}/turn`, `/api/sessions/{id}/turn/stream`.

Thêm:

```
GET /api/traces/stats?from=2026-08-01&to=2026-08-26&model=gpt-4o-mini
→ {
    "totals": {"requests": 41, "prompt_tokens": 12840, "cached_tokens": 3120,
               "completion_tokens": 4021, "total_tokens": 16861, "cost_usd": 0.0182,
               "latency_ms_p50": 1320, "latency_ms_p90": 2840},
    "by_model": [
      {"model":"gpt-4o-mini","requests":30,"prompt_tokens":...,"cached_tokens":...,
       "completion_tokens":...,"total_tokens":...,"cost_usd":0.012,"latency_ms_p50":1200},
      {"model":"openai/gpt-4o","requests":11, ...}
    ],
    "by_day": [{"day":"2026-08-26","requests":5,"cost_usd":0.0031}, ...],
    "models": ["gpt-4o-mini","openai/gpt-4o"]
  }
```

```
GET /api/traces?model=gpt-4o-mini&status=completed&limit=50&offset=0
→ {"traces":[
     {"session_id":"...","turn_index":3,"title":"Linux là target","started_at":"...","ended_at":"...",
      "model":"gpt-4o-mini","status":"completed","rounds":2,"requests":2,
      "prompt_tokens":812,"cached_tokens":240,"completion_tokens":31,"total_tokens":843,
      "cost_usd":0.00042,"latency_ms":2100},
   ], "total": 41}
```

Cách tính: server scan `SessionStore.list_paths()` → `scan()` từng file, nhóm messages thành turn, cộng dồn `meta.usage` + `meta.cost_usd`. Không đọc `~/.thyca/memory/*`. Với hot file đang mở chưa index, không ảnh hưởng.

Tối ưu: scan lazy, sort theo `started_at` desc, bỏ qua `SessionCorrupt` như `list_sessions`. Cache in-memory keyed `mtime_ns` của mỗi file để không re-parse khi không đổi; cap scan 200 files gần nhất (đủ cho local single-user).

Fallback: khi không có `ChatApp` (static `http.server`) → 404 `{error:"trace unavailable"}` thay vì crash.

### 4. UI Trace — đúng质 Thyca, mượn LangSmith

**Thiết kế tổng:** giữ `webui/index.html` shell warm sidebar + paper texture + header terracotta. Trace không phải dashboard tối; nó là **sổ nghe** — LangSmith cung cấp thông tin, Thyca cung cấp giọng giấy.

**Bố cục Trace (đổi từ mock `music-page`):**

- **Header Trace** (`book-reading` variant): kicker `Trace · AgentLoop · {model}`, tiêu đề `Lượt · chi phí · token`, note `model · cache · in/out là derived, markdown/memory không tính phí`.
- **Stats strip** (4 ô như `stat-row` trong Memories, nhưng cho trace): `Yêu cầu` · `Token vào` (trong đó `cache`) · `Token ra` · `Chi phí` + `p50/p90 latency`. Mỗi số có `progress-label`. Dùng `var(--color-trace)`/`--color-trace-soft` đã có trong `tokens.css`.
- **Filter bar** (ngay dưới stats): pills `Model: all | gpt-4o-mini | gpt-4o` + `Status: all/ok/failed` + `Ngày: 7d/30d/all` + search `title/session_id`. Không thêm dependency; filter chạy trên `/api/traces` query, client chỉ render.
- **Sidebar Trace** (list): `page-card` tái dùng, mỗi card hiện `tag = model` (màu `trace`), `title` (fallback `Phiên trống`), `date` (started_at), và một dòng nhỏ `prompt+completion · $cost · latency`. Card đang chọn `is-active`.
- **Detail** (paper): 
  - Dòng kicker `ses_xxx · turn #n · {model} · {status}`
  - **Staff tĩnh**: cùng `staff-map.js`/`staff-draw.js` + Bravura; score được dựng **từ messages** qua helper `traceScoreFromMessages(messages, turnRange)` (logic giống `scoreFromEvents` nhưng ánh xạ `assistant.meta.round` → `llm.finished` event, `tool` messages → `tool.finished`). In-flight không có — đây là lịch sử.
  - **Timeline waterfall**: `ol.phase-list` mở rộng; mỗi `li` là một span (`assemble`, `think#n`, `act` với các `tool` con, `observe`, `naming`). Thanh latency là `div.track-rule > span` width tỉ lệ `latency_ms / turn.latency_ms`. Hiển thị `latency_ms` và token của round đó. Tool song song hiện cùng hàng, giữ declaration order nhưng ghi `latency_ms` riêng.
  - **Token breakdown** cho turn: `prompt 812 (cached 240) → completion 31 → total 843 · $0.00042`. Cached hiện như badge `cache 29%`.
  - **Raw** collapsible `<details>`: summary `Xem JSON`, nội dung là `messages` của turn (đã escape), không thêm fetch.

**Tận dụng khuông nhạc:**
- Live (Chat) giữ staff động theo `TurnEvent` NDJSON như hiện tại.
- History (Trace) dùng cùng grammar và renderer nhưng **đọc lại** từ JSONL — cùng tiếng, hai thời điểm. List item có thể có mini-staff 1-system nhỏ (optional v1, nếu không kịp thì chỉ detail có staff).

**Empty/error:**
- Chưa có trace → `Chưa có lượt nào. Gửi một câu trong Chat.`
- Fetch lỗi → giữ mock `data.js:trace` như `hydrateChat`/`hydrateMemories` (không throw).
- Unknown cost → `—`

**Responsive:** giữ breakpoint 320/375/414/768 của `chrome.css`; filter bar wrap, stats strip chuyển 2×2 dưới 600px; staff `widthPx` lấy từ `getBoundingClientRect` như `staff.js:paint`.

---

### GOAL-001: Chuẩn hóa usage và bảng giá

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `thyca/llm/llm_base.py`: định nghĩa `Usage = TypedDict(prompt_tokens, completion_tokens, cached_tokens, total_tokens, reasoning_tokens?)` và helper `normalize_usage(raw: dict, provider: str) -> dict \| None`. Thêm `ChatReply.model: str \| None` và `ChatReply.usage: dict \| None` đã normalize. | x | 2026-08-27 |
| TASK-002 | `thyca/llm/openai_chat.py`: `_parse_reply` bóc `prompt_tokens_details.cached_tokens` và `completion_tokens_details.reasoning_tokens`; map qua `normalize_usage("openai", raw)`. Giữ `_cap`/`_redact` không đổi. | x | 2026-08-27 |
| TASK-003 | `thyca/llm/google_chat.py` / `anthropic_chat.py`: khi implement lần đầu, dùng cùng `normalize_usage` (Google: `cachedContentTokenCount`, Anthropic: `cache_read_input_tokens`). Trước khi có key thật, giữ `NotImplementedError` nhưng thêm normalize stub để test không phụ thuộc network. | | |
| TASK-004 | `thyca/config.py`: thêm `PricingCfg(input, cache, output)` và `Config.pricing: dict[str, PricingCfg]` (validate `>=0`, finite, key `model` non-empty). `to_dict`/`_parse_dict` hỗ trợ `pricing` optional; chấp nhận alias `cached_input` khi đọc để backward-compat nhưng ghi ra `cache`. Seed mặc định `DEFAULT_PRICING` cho known models nếu config không có. `thyca/llm/pricing.py`: `DEFAULT_PRICES`, `cost_for(model, usage, pricing_cfg) -> float \| None` resolve `pricing_cfg.get(model) ?? DEFAULT_PRICES[model]`, `resolve_model(raw)`. Unknown → `None`. | x | 2026-08-27 |

### GOAL-002: Đo latency và persist vào session JSONL

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | `thyca/agent/think.py`: `Think.think(stage)` đo `perf_counter` quanh `self._llm.chat(...)`, lưu `stage._last_llm_latency_ms` và `stage._last_llm_model` (lấy từ `Connect` hoặc `ChatReply.model` nếu có). Không đổi signature public ngoài việc thêm field tạm trên `Stage`. | x | 2026-08-27 |
| TASK-006 | `thyca/agent/stage.py`: thêm optional `meta_llm: list[dict]` hoặc dùng `Stage.results` mở rộng để mang `latency_ms`/`usage` qua `Observe`. Giữ `Stage` dataclass non-frozen, không I/O. | x | 2026-08-27 |
| TASK-007 | `thyca/agent/observe.py`: `assistant()` và `observe()` nhận `latency_ms`/`usage`/`cost_usd` từ `Stage`, ghi vào `Message.meta` của `assistant` (`kind: "llm" | "naming"`, `round`, `model`, `latency_ms`, `usage`, `cost_usd`, `finish_reason`). Tool messages thêm `meta.latency_ms`/`meta.round`. Giữ `order_results` theo `call_id` như hiện tại. | x | 2026-08-27 |
| TASK-008 | `thyca/agent/loop.py`: trong `for round` loop, sau `think.think` tính `cost_for(model, usage, self._pricing)` (pricing lấy từ `Config.pricing` truyền vào `AgentLoop`/`ChatApp`) và chuẩn bị meta cho `Observe`; `Act.act` đo từng tool `latency_ms` (trong `Act._one` tương tự) và trả về `ToolResult` kèm latency qua `Stage`. `loop_limit` cũng ghi meta với `status: loop_limit`. | x | 2026-08-27 |
| TASK-009 | `thyca/chat_app.py`: `_run_turn` truyền `model` từ `Config.provider.model` và `pricing` từ `Config.pricing` xuống `Stage`/`AgentLoop`; `_name_if_needed` đo latency riêng, lưu `kind: "naming"` với model/usage/cost (dùng cùng `cost_for`) nếu LLM title thành công. Không lưu `TurnEvent` riêng — JSONL là source. | | |

### GOAL-003: HTTP aggregates cho Trace (không phá endpoint cũ)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-010 | `thyca/serve.py`: thêm `GET /api/traces/stats` và `GET /api/traces` (filter `model`, `status`, `from`/`to` `YYYY-MM-DD`, `q`, `limit`/`offset`). Dùng helper `_trace_from_messages(messages)` để nhóm thành turns và cộng `meta.usage`/`cost_usd`/`latency_ms`. Sort desc theo `started_at`. Unknown cost giữ `null`. Lỗi scan một file → bỏ qua file đó, không 503 toàn bộ. | x | 2026-08-27 |
| TASK-011 | `thyca/serve.py`: thêm `GET /api/traces/{session_id}/{turn_index}` detail: trả `{session_id, turn_index, title, started_at, ended_at, model, status, latency_ms, usage, cost_usd, messages: [...]}` với messages là canonical `to_canonical_dict()` của turn đó (đã có trong JSONL). Traversal/id sai → 404. | x | 2026-08-27 |
| TASK-012 | Cache scan: `dict[path -> (mtime_ns, parsed)]` trong `Handler` hoặc module-level với lock; invalidate khi `mtime_ns` đổi. Cap 200 files gần nhất; `by_day` aggregate từ `by_model` turns. Không thêm sqlite. | | |

### GOAL-004: WebUI Trace — LangSmith thông tin, Thyca giọng giấy

**Visual superseded 2026-08-27.** Fetch + `traceScoreFromMessages` (TASK-013/014) giữ. Layout admin dump (`.trace-card` / select / table trong `page-body`, mock header/sidebar) không đạt TASK-015. Bề mặt sổ nghe chuyển sang `.agents/plans/thyca-trace-notebook.md` — không làm tiếp TASK-015/016/017 như đã ship.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-013 | `webui/js/trace.js` (mới): `fetchTracesStats`, `fetchTraces`, `fetchTraceDetail`. Fallback khi `!response.ok` → giữ `modes.trace.pages` từ `data.js` như `chat.js:hydrateChat`/`render.js:hydrateMemories`. Không throw ra ngoài. | x | 2026-08-27 |
| TASK-014 | `webui/js/trace-score.js` (mới): `traceScoreFromMessages(messages, turnRange)` — dựng array `TurnEvent`-like từ `assistant.meta.kind/round` và `tool` messages, rồi gọi `scoreFromEvents` hiện có. Giữ invariant 16 ticks/measure, Bravura, cadence `V→I` như `staff-map.js`. Test thuần JS, không DOM. | x | 2026-08-27 |
| TASK-015 | `webui/js/render.js` / `webui/js/data.js`: đổi Trace từ mock `music-page` sang layout mới — header stats strip (4 ô), filter bar (pills), sidebar list `page-card` có `trace` tone, detail có staff + timeline waterfall + token breakdown. Giữ `clearStaffs`/`syncStaffs` lifecycle như Chat (`staff.js` đã có `unmountStaff`). | | |
| TASK-016 | `webui/css/workspace.css` + `webui/css/chrome.css`: thêm `.trace-stats`, `.trace-filter`, `.trace-timeline`, `.trace-token-badge` bằng tokens hiện có (`--color-trace`, `--color-paper-2`, `--font-ui`). Không thêm font hay framework. Responsive wrap như `chrome.css` breakpoint hiện tại. | | |
| TASK-017 | `webui/js/app.js`: wiring `renderMode("trace")` gọi `hydrateTrace()`, filter pills trigger `fetchTraces` với query. Khi `state.chatLive` false, Trace vẫn render mock. Không đụng `chat.js` NDJSON streaming. | | |

### GOAL-005: Verification và tài liệu contract

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-018 | `tests/test_llm_openai_chat.py`: thêm case bóc `cached_tokens` và `reasoning_tokens` từ fixture OpenAI `usage` có `prompt_tokens_details`/`completion_tokens_details`; unknown shape → `usage=None` không raise. | x | 2026-08-27 |
| TASK-019 | `tests/test_llm_pricing.py` (mới): `cost_for` cho known model khớp bảng, unknown → `None`, cached cheaper, total = input+output-cached? (assert theo bảng). | x | 2026-08-27 |
| TASK-020 | `tests/test_agent_loop.py` / `test_agent_observe.py`: stub `FakeLLM` trả `ChatReply(usage={...}, model="gpt-4o-mini")`, assert `SessionManager.current.messages[-1].meta.usage` và `meta.cost_usd` được persist; tool `latency_ms` có mặt. | x | 2026-08-27 |
| TASK-021 | `tests/test_serve_trace.py` (mới): tạo 2 sessions với turns có meta khác model/usage, gọi `GET /api/traces/stats` → `by_model` đúng tổng, filter `?model=gpt-4o-mini` chỉ trả turns đó; corrupt file bị skip; `GET /api/traces/{id}/{idx}` 404/200. | x | 2026-08-27 |
| TASK-022 | Frontend Node test (mở rộng `tests/test_chat_ui.py` hoặc file mới `tests/test_trace_score.py`): `traceScoreFromMessages` với 2 LLM rounds + 2 tools → score có 2 measures + terminal `V half → I half`, harmony `I→vi`, mỗi measure đủ 16 ticks, `vii°` chỉ khi `is_error`. | x | 2026-08-27 |
| TASK-023 | Cập nhật `.agents/plans/services/agent-loop.md` (events → trace) và `webui-chat-backend.md` assumption superseded; chuyển plan này sang `done` khi `uv run pytest -q` pass và manual check 320/375/414/768px không overflow. | | |

## Test Plan

Chạy theo thứ tự:

```bash
uv run pytest tests/test_llm_openai_chat.py tests/test_llm_pricing.py -q
uv run pytest tests/test_agent_loop.py tests/test_agent_observe.py -q
uv run pytest tests/test_serve_trace.py -q
uv run pytest -q
```

Acceptance:

1. `FakeLLM` trả `usage: {prompt_tokens:100,cached_tokens:20,completion_tokens:10}` với `Config.pricing["gpt-4o-mini"]={input:0.15,cache:0.075,output:0.6}` → `Message.meta.usage` đúng và `cost_usd` = `((100-20)*0.15 + 20*0.075 + 10*0.6)/1e6` (theo `pricing_cfg` + cache tách riêng).
2. Unknown model `foo/bar` → `cost_usd is None`, UI hiện `—` (không `0.00$` giả).
3. Session cũ không có `meta.usage` → `GET /api/traces/stats` vẫn 200, turn đó `total_tokens: null`, không làm lệch tổng của model khác.
4. Hai provider khác model trong cùng session list → `by_model` có 2 entries, `totals.requests` = sum.
5. Filter `GET /api/traces?model=gpt-4o-mini&status=completed` chỉ trả turns khớp; filter `from=2026-08-26&to=2026-08-26` theo `started_at` UTC.
6. Corrupt JSONL một file → `GET /api/traces/stats` bỏ qua file đó, vẫn trả 200 với các file còn lại.
7. Detail `GET /api/traces/{id}/{turn_index}` trả đúng slice messages của turn, đủ `meta.latency_ms` để timeline vẽ.
8. `traceScoreFromMessages` với turn 2 rounds + naming → score có 3 activity measures + terminal completed `V half→I half` đủ 4/4, mỗi measure 16 ticks, harmony theo `measureIndex % 4`.
9. WebUI static mock (`python -m http.server --directory webui`) không crash khi `/api/traces/*` 404; Trace vẫn hiện `data.js` mock và không overflow ở 320px.
10. `uv run pytest -q` không tạo thêm failure ngoài baseline `test_debug_prints_prompt_flags`.

Manual browser:

- Gửi 2 lượt với model khác nhau → Trace header cập nhật `by_model` ngay, filter theo model đổi list.
- Turn có cached_tokens cao → badge `cache 60%` hiện, cost rẻ hơn turn không cache cùng prompt.
- Turn failed → card có viền/kicker lỗi, staff kết bằng `V whole` single barline, không `I`.
- Reload Trace → không mất staff/history (đọc từ JSONL, không từ NDJSON live).

## Assumptions

1. Một `ChatApp` serialize turn bằng `_turn_lock` như hiện tại; stats scan chịu được second reader trong lúc append (đọc `SessionStore.scan` có thể 503 transient, bỏ qua file).
2. Không thêm sqlite/DB trong slice này; nếu scan 1k files chậm, tối ưu sau bằng sqlite riêng, không đổi API shape.
3. Pricing sống trong `~/.thyca/config.json` (`pricing: {model: {input, cached_input, output}}` USD/1M) + builtin `DEFAULT_PRICES`; không fetch live. Sửa giá bằng sửa config (hoặc PR cập nhật default), không cần deploy code. Historical `cost_usd` đã persist trong `Message.meta` không tính lại khi giá đổi.
4. Token `in` = `prompt_tokens`, `cached` là subset của `in`, `out` = `completion_tokens`, `total = prompt + completion`. `reasoning_tokens` hiển thị kèm `out` nếu có.
5. Latency đo bằng `perf_counter` tại backend; frontend không tự đo. Clock lệch không ảnh hưởng sort (dùng `ts` ISO của message đầu turn làm `started_at`).
6. Không tính phí memory/tool/MCP; chỉ LLM. Tool latency vẫn trace nhưng không cost.
7. Không sửa `ToolRegistry` global; telemetry chỉ ở `Act._one`/`Think.think`/`ChatApp._name_if_needed`.
8. Code/identifier English; UI tiếng Việt như hiện tại. Token/cost hiển thị với locale `vi-VN` (dấu phẩy).
9. Không persist `TurnEvent` NDJSON; history trace dựng lại từ JSONL messages + meta — cùng grammar với `thyca-operational-music-trace.md` nhưng không replay event stream.
10. Không đổi `SessionStore` rewrite/compaction contract; `meta` mới chỉ append, không chỉnh rewrite ngoài `Message.meta`.

