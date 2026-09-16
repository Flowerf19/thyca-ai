---
status: done
created: 2026-09-05
last_updated: 2026-09-05
---

# Staff: câu 8 ô, V7, event nghe khác nhau

## Summary

Khuông đang lặp `I–vi–IV–V` mỗi 4 ô; mọi pulse chỉ 1–3 nốt từ triad; `tool.*` = `skill.*`; `llm.retry` là rest. Piano playback đã có (C4–C6). Plan này đổi **hòa âm + catalog density + lớp `sound` cho playback** — không hash `name` thành pitch, không đụng duration/4/4/tick, không `switch(type)` trong `map.js`.

Nguồn: Open Music Theory phrase T–S–D–T (`harmonicSyntax1.html`); OMT 2e doo-wop `I–vi–IV–V` và biến `I–vi–ii–V`; 7th xuống 1 bậc, được bỏ 5th (Puget Sound 27.1). Không dùng jazz rootless (không có bass riêng).

Review 2026-09-05 (Muse 1.3 / GLM 5.3 Flash / GPT-5.6 Sol / Grok 4.6): không implement as-is. Bản này đã chốt 5 điểm họ chặn.

Success:

1. 8 ô activity: `I vi IV V | ii V7 I I` (`measureIndex % 8`). 32 `tool.finished` ok → đúng 8 harmony đó rồi lặp.
2. Staff `pitches` ≤ 3, luôn subset của chord ô (trừ `vii°` một phách). **V7 staff ≠ vii°.** Playback dùng `sound` (bass; **chỉ V7** thêm 7th).
3. `tool.started` (`cue`) ≠ `skill.started` (`outer`); `tool.finished` ok (`full`) ≠ skill ok (`outer`); `llm.retry` pulse `outer` (không rest); `llm.finished` `outer`, `tool_count === 0` → `full`. Thiếu `tool_count` coi như không `=== 0` (giữ `outer`).
4. `name` chỉ override **tool** `bash` → `full`. **Không** đẩy `read`/`write`/`memory_*` sang `outer` (trùng skill). Path/content không đọc.
5. Cadence terminal 1 ô riêng: completed = V7 half `G4 B4 F5` → I half; failed = V7 whole cùng voicing. Mapper không biết tên event.

## Tasks

### GOAL-001: Vòng 8 ô + chord ii / V7 trong mapper

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `HARMONY_ORDER = ["I","vi","IV","V","ii","V7","I","I"]`. `% 8`. | x | 2026-09-05 |
| TASK-002 | `VOICINGS` staff (≤3): I `C5 E5 G5`; vi `C5 E5 A5`; IV `C5 F5 A5`; V `B4 D5 G5`; ii `D5 F5 A5`; **V7 `G4 B4 F5`** (root+3rd+7th, bỏ 5th — **khác** vii° `B4 D5 F5`). Error-color `samePitches(..., vii)` vẫn nhận diện trên ô V7. | x | 2026-09-05 |
| TASK-003 | Terminal: V7 half `G4 B4 F5` → I half `C5 E5 G5`. Failed: V7 whole cùng voicing. File: `webui/js/staff/map.js`. | x | 2026-09-05 |

### GOAL-002: Catalog — event khác nhau, mapper vẫn mù type

YAML vẫn flat string. `familyFor` trả **bản sao đã resolve** `{slot, density, errorWhen?, kind?}` — **không mutate** object trong `FAMILIES`. `map.js` không đọc `name` / `tool_count`.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-004 | YAML: `llm.started` `cue` (khác `turn.accepted` `anchor`). `llm.retry` `slot: pulse` `density: outer`. `skill.started`/`skill.finished` `outer`; skill finished giữ `errorWhen: ok !== true`. `tool.started` `cue`; `tool.finished` `full` + `errorWhen`. | x | 2026-09-05 |
| TASK-005 | Whitelist `WHEN` **chỉ** `tool_count === 0` (không thêm `tool_count > 0`). YAML `llm.finished`: `density: outer` + `when: tool_count === 0` + `thenDensity: full`. Thiếu field → `=== 0` false → `outer`. Cặp `when`/`thenDensity` bắt buộc cùng có; `thenDensity` ∉ {anchor,cue,outer,full} hoặc unknown `when` → drop entry + warn như `errorWhen`. | x | 2026-09-05 |
| TASK-006 | `familyFor`: clone rồi apply `when`/`thenDensity`. Override `name` **chỉ** `tool.started`/`tool.finished` khi `name === "bash"` → density `full`. `read`/`write`/`memory_*` **không** override (giữ cue/full YAML, khác skill `outer`). Identifier lạ → YAML. `errorWhen` giữ trên bản sao, không ghi ngược catalog. | x | 2026-09-05 |

~~TASK-006 cũ: `read`/`write`/`memory_*` → `outer` — gạch; trùng skill, 4 reviewer chặn.~~

### GOAL-003: Playback `sound` + bass sample

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-007 | Event activity + terminal thêm optional `sound: string[]`. Staff vẽ `pitches`. `play.js` `playScore` dùng `event.sound \|\| event.pitches`. Không `sound` trên rest. | x | 2026-09-05 |
| TASK-008 | `sound` = bass + staff pitches. **Chỉ harmony `V7` thêm F5** nếu chưa có — **không** thêm F vào ô `V`. Bass: I `C4` (sample sẵn); vi `A3`; IV `F3`; V và V7 `G3`; ii `D3`. vii° không bass, `sound` bỏ hoặc = `pitches`. Terminal: V7 `G3`+`G4 B4 F5`; I `C4`+`C5 E5 G5`. | x | 2026-09-05 |
| TASK-009 | Copy `A3.mp3 F3.mp3 G3.mp3 D3.mp3` từ fuhton/piano-mp3 (xác nhận file tồn tại trước copy). `FREQ` + `loadPiano` thêm 4 nốt + Hz. README piano: phạm vi D3–C6. | x | 2026-09-05 |

### GOAL-004: Test + changelog

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-010 | `tests/test_staff_map.py`: `test_harmony_cycle` 32 pulse → 8 harmony; **`test_window_keeps_terminal_*` ô 15 = `I` (`15 % 8`), không còn `V`**. `CHORDS` ii + V7=`G4 B4 F5`; test `V7 full` rồi `tool.finished ok:false` vẫn ra vii° (không nuốt). `sound` bass; vii° không bass. | x | 2026-09-05 |
| TASK-011 | `tests/test_staff_play.py`: có `sound` thì phát `sound`, không `pitches`. | x | 2026-09-05 |
| TASK-012 | CHANGELOG 0.7.6 (unreleased): một bullet. Không bump version. | x | 2026-09-05 |
| TASK-013 | `tests/test_staff_catalog.py`: `llm.started`=`cue`; `llm.retry` pulse `outer` (không rest); skill ≠ tool; `familyFor(llm.finished, tool_count:0)`=`full`, thiếu/`1`=`outer`; `tool.started name=read` giữ `cue`, `bash`=`full`; skill `name=bash` **không** override; hai lần `familyFor` bash rồi read không nhiễm catalogEntries; unknown `when` drop. | x | 2026-09-05 |
| TASK-014 | Downstream so exact: `tests/test_trace_score.py` terminal (+ `sound` nếu object so sánh pitches); `tests/test_turn_stream.py` nếu còn đợi `D5` trên cadence. Chạy `uv run pytest tests/test_staff_map.py tests/test_staff_catalog.py tests/test_staff_play.py tests/test_staff_draw.py tests/test_trace_score.py tests/test_turn_stream.py -q` rồi suite rộng hơn nếu xanh. | x | 2026-09-05 |

## Test Plan

- Lệnh TASK-014. Invariants: 16 tick/ô, không overlap, unknown type = silence, không hai vii° liên tiếp **kể cả trong ô V7**.
- `map.js` không chứa `"tool.started"` / `"llm.finished"`.
- Tay: `read` (cue) ≠ skill (outer); `bash` started = full; `bash` fail = vii° khác V7 `G4 B4 F5`; lượt không tool — `llm.finished` full; chạm khuông nghe bass, ô V không nghe 7th.

## Assumptions

1. Một event = một quarter; trailing rest và cửa sổ 16 ô không đổi.
2. Không hash path/token. `name` whitelist chỉ `bash` → `full` trên tool. Lạ / `tool` fallback → density YAML.
3. Khuông ≤3 nốt; tai nghe `sound` 3–5. Draw.js không đọc `sound`.
4. Pack fuhton có A3/F3/G3/D3 (TASK-009 kiểm tra trước copy). I bass cố ý `C4`, không C3.
5. `staff-event-catalog.md` (done) giữ tách catalog; vòng 4 ô và tool=skill bị plan này thay.
6. Không đổi `events.py` / NDJSON.
7. Retrogression V→ii (bar 4→5) chấp nhận — motif 8 ô, không đổi thành `I ii V7 I` trong v1 này.
