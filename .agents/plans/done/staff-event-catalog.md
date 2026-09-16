---
status: done
created: 2026-08-28
last_updated: 2026-08-28
---

# Staff music: tách event catalog khỏi score normalizer

## Summary

`webui/js/staff-map.js` hiện gộp 3 việc trong `scoreFromEvents`: biết loại
event (`ACTIVITY_TYPES` + switch trong `activityFor`), chọn sonority
(pitch theo hợp âm ô), và xếp ô nhịp (normalizer: beat slot, đóng ô,
trailing rest, cadence, cửa sổ 16 ô). Mỗi trace mới phải sửa switch —
sai một case là lệch 16 tick hoặc đúp phách.

Refactor này tách **catalog** (event type → role nhạc: slot + density +
errorWhen) thành module riêng; `scoreFromEvents` còn lại **normalizer thuần**
chỉ đọc family, không có `switch(type)`. Event mới sau này = đăng ký 1 dòng
catalog + 1 dòng status + allowlist `TurnEvent` — không sửa thuật toán nhịp,
không sửa renderer, không sửa HTTP.

Bối cảnh đã landed (plan trước, cùng ngày): `skill.started`/`skill.finished`
backend (`events.py` allowlist, `Act._one` emit qua `skill_event.py`,
`chat_app.py` truyền `skills_root`) + mapper/status + tests. Refactor này
**không đổi behavior** — output của `scoreFromEvents` phải byte-equal với
trước refactor cho mọi input.

## Scope & non-goals

Trong scope (chỉ frontend + tests):
- Module mới `webui/js/staff-catalog.js`: bảng `FAMILIES` + hàm
  `familyFor(event) -> role | null`.
- `staff-map.js`: xóa `ACTIVITY_TYPES` + `activityFor` switch, gọi
  `familyFor`; giữ nguyên toàn bộ normalizer logic.
- `turn-status.js`: tách câu hiển thị ra map riêng cùng file (độc lập nhạc,
  không đụng catalog).

Ngoài scope (không đụng):
- Backend `events.py` / `act.py` / `skill_event.py` — đã ổn ở tầng đúng.
- `staff-draw.js` / `staff.js` — renderer vẽ model, không biết event.
- `trace-score.js` — sinh event giả từ JSONL, giao thức với mapper không đổi.
- WebAudio, dựng lại khuông từ history, token stream — đã chốt bỏ.
- Không đổi wire format NDJSON, không đổi model shape (measures/events/rests).

## Role grammar (chốt, không mở rộng trong plan này)

| role | nghĩa | chiếm |
|------|-------|-------|
| `pulse` | hoạt động, 1 quarter | density: `anchor` [low] / `cue` [high] / `outer` [low,high] / `full` [triad] |
| `rest` | nốt lặng quarter (naming.started) | 1 beat, `previousActivityPitches` reset null |
| `terminal` | đóng ô + ô cadence riêng, dừng | kind: `completed` (V→I, final barline) / `failed` (V whole) |
| (không đăng ký) | unknown / silence | 0 tick, no-op |

Luật error: `errorWhen(event)` true → vii° một beat, **không lặp liên tiếp**
(nốt trước đã vii° → hạ thành full triad của ô). Sonority luôn lấy từ hợp âm
ô hiện tại (`VOICINGS[harmony]`), không từ tên event.

## Catalog đăng ký (trạng thái hiện tại, di chuyển nguyên vẹn)

```js
const CATALOG_YAML = `
# slot: pulse | rest | terminal — pulse cần density; terminal cần kind
- type: turn.accepted
  slot: pulse
  density: anchor
- type: llm.started
  slot: pulse
  density: anchor
- type: llm.finished
  slot: pulse
  density: outer
- type: tool.started
  slot: pulse
  density: cue
- type: tool.finished
  slot: pulse
  density: full
  errorWhen: ok !== true
- type: skill.started
  slot: pulse
  density: cue
- type: skill.finished
  slot: pulse
  density: full
  errorWhen: ok !== true
- type: session.naming.started
  slot: rest
- type: session.naming.finished
  slot: pulse
  density: anchor
- type: turn.completed
  slot: terminal
  kind: completed
- type: turn.failed
  slot: terminal
  kind: failed
`;
```

Thêm trace mới = thêm 4 dòng YAML, không đụng code mapper. `errorWhen`
chỉ chấp nhận expression trong whitelist (hiện: `ok !== true`) — để tránh
biến YAML string thành eval.

## Tasks

### GOAL-001: Catalog module + mapper refactor (behavior-preserving)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Tạo `webui/js/staff-catalog.js`: **catalog khai báo bằng YAML string trong module** (biến `const CATALOG_YAML = \`...\``) + subset parser thuần (flat list of flat maps, string values, không dependency ngoài). Parser trả entries `{type, slot, density?, kind?, errorWhen?}`. Export `FAMILIES` (Map type→entry) + `familyFor(event) -> entry \| null` (object có `type: string` mới tra; ngược lại `null`). `errorWhen` từ string expression `"ok !== true"` — parser chỉ chấp nhận whitelist expression mẫu `ok !== true` (compile thành predicate đóng gói), giá trị lạ → bỏ entry + console.warn | x | 2026-08-28 |
| TASK-002 | `staff-map.js`: import `familyFor`, xóa `ACTIVITY_TYPES` + `switch` trong `activityFor`. Vòng lặp: `family = familyFor(raw)` → `null` skip; `slot === "terminal"` break; `slot === "rest"` → quarter rest; `pulse` → pitches theo density từ hợp âm ô, `entry.errorWhen?.(raw)` → luật vii°. Không còn `switch(type)` hay tên event nào còn lại trong file | x | 2026-08-28 |
| TASK-003 | Chạy lại toàn bộ test mapper/draw/stream hiện có (test_staff_map, test_staff_draw, test_turn_stream, test_trace_score) — phải pass **không sửa assertion nào**. Đây là acceptance của behavior-preserving | x | 2026-08-28 |
| TASK-004 | Parse-guard test trong test catalog: YAML lỗi cấu trúc (thiếu `type`, `slot` lạ, trùng `type`) → entry bị bỏ + warn, mapper vẫn chạy (không throw lúc import); catalog rỗng → mọi event no-op, score 1 ô whole rest | x | 2026-08-28 |

### GOAL-002: Tests cho kiến trúc mới

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-101 | Test mới cho catalog (node eval, style test_staff_map): đăng ký đúng — mỗi family trả role/density khớp bảng; `familyFor` với object lạ / thiếu type / null → `null` | x | 2026-08-28 |
| TASK-102 | Property-style invariant test: sinh chuỗi event hỗn hợp từ catalog (pulse/rest/terminal xen kẽ, error lặp, >16 ô) → mọi ô 16 tick kín, không overlap, không xuyên beat 3, cadence đúng kind | x | 2026-08-28 |
| TASK-103 | Test "unknown không tốn phách" nâng cấp: chuỗi chỉ khác nhau bởi event không đăng ký → score byte-equal (đã có 1 test tương tự; chuyển sang dùng catalog mới và giữ assertion) | x | 2026-08-28 |
| TASK-104 | Test chống suy biến catalog: không family nào được đăng ký slot `terminal` ngoài 2 type transport (assert trên `FAMILIES`); mọi `pulse` đều có `density` hợp lệ trong {anchor, cue, outer, full} | x | 2026-08-28 |

### GOAL-003: Docs

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-201 | `webui/js/staff-catalog.js` + `staff-map.js` header comment: ghi quy ước thêm trace mới — sửa catalog YAML + status + allowlist `TurnEvent` + test không đúp với `tool.*`; unknown mặc định no-op, KHÔNG fallback "event lạ = nốt" | x | 2026-08-28 |
| TASK-202 | `.agents/plans/services/agent-loop.md` mục Events: 1 câu trỏ sang `staff-catalog.js` là bảng đăng ký nhạc phía UI (catalog YAML, sửa không cần đụng mapper) | x | 2026-08-28 |

## Luật bắt buộc khi thêm family sau này (khóa vào header comment)

1. Một hành động vận hành = tối đa một pulse (không `tool.*` + `skill.*` cùng lúc — phân loại ở `Act`, không ở mapper).
2. Không slot theo thời gian — latency 2ms hay 20s vẫn 1 quarter.
3. Không hash name/path/token/bytes thành pitch.
4. Duration chỉ 4/8/16 — không dotted, beam, tie.
5. Terminal giả bị cấm — chỉ transport completed/failed.
6. Catalog không được cần field nhạy (path, content) trên wire.
7. Event tần suất cao (token delta, log dòng) → silence (không đăng ký).

## Test Plan

- `uv run pytest tests/test_staff_map.py tests/test_staff_draw.py tests/test_turn_stream.py tests/test_trace_score.py -q`
- Full suite `uv run pytest -q` (393 hiện tại) phải pass.
- Acceptance refactor: TASK-003 pass mà **không thay đổi assertion cũ**.

## Assumptions

- Node-based test style hiện có (subprocess `node --input-type=module`) giữ nguyên.
- Catalog là ES module thuần (browser + node import được), không phụ thuộc DOM.
- `previousActivityPitches` (luật vii° không lặp) thuộc normalizer — giữ nguyên vị trí, không đưa vào catalog.
- YAML subset cố ý nhỏ: flat list + flat string maps. Parser ~30 dòng, không thêm dependency vendor. Nếu sau này cần cấu trúc phức tạp hơn (nested, anchors) → cân nhắc vendor js-yaml lúc đó, không phải bây giờ.
