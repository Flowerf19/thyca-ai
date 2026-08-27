---
status: done
created: 2026-08-26
last_updated: 2026-08-26
---

# Khuông nhạc theo operational events thật

## Summary

Thay cơ chế timer + câu thơ giả bằng một **musical operational trace**: khuông chỉ đổi khi Thyca thực sự bắt đầu/kết thúc một bước vận hành (`LLM`, tool, đặt tên phiên, hoàn tất hoặc lỗi). Không stream chain-of-thought, prompt, arguments, tool output hay nội dung riêng của model.

WebUI dùng một endpoint NDJSON streaming mới; endpoint JSON `/turn` hiện tại được giữ để không phá compatibility. Trace chỉ sống trong lượt đang chạy và trên reply vừa sinh; không ghi vào session JSONL, không hiện lại trong history, không phát audio.

Success criteria:

1. Không còn nốt/câu trạng thái được sinh theo đồng hồ.
2. Tool chạy song song phải hiện `started` và `finished` theo thứ tự thực tế; không giả completion order.
3. Lượt nhanh vẫn có event mở đầu thật ở beat 1 và cadence thật; không bỏ event đầu hoặc tự thêm câu chưa từng xảy ra.
4. Lượt dài luôn hiện event mới nhất; renderer không âm thầm đứng ở event 22.
5. Telemetry hỏng hoặc client ngắt kết nối không được làm hỏng/pause AgentLoop.
6. Không lộ chain-of-thought, tool arguments, tool result, prompt, exception stack hoặc secret.
7. Mọi ô nhịp đã chốt đủ đúng 4 phách; chỉ ô đang chạy được phép dùng rest tạm để chờ event tiếp theo.
8. Bản nhạc dùng một grammar C major xác định, tiết tấu dễ đọc, cadence hợp lệ và glyph ký âm chuẩn; không biến loại event thành hợp âm/nghịch âm tùy tiện.

## Những chỗ đang sai

| ID | Vị trí hiện tại | Sai ở đâu | Hậu quả cần test hồi quy |
|----|-----------------|-----------|--------------------------|
| DEFECT-001 | `webui/js/chat.js:39-57,277-291`; `webui/js/staff-map.js:48-135` | `setInterval(3000)` tự chọn câu thơ rồi biến câu thành nốt. Dữ liệu không đến từ `AgentLoop`, LLM hoặc tool. | UI trình bày thời gian chờ như tiến trình thật; thêm luật frontend không thể sửa bản chất. |
| DEFECT-002 | `webui/js/chat.js:52-55,83-94` | Câu đầu được hiện nhưng không tạo event; lúc settle lại tự thêm `Sắp xong rồi…` dù câu đó chưa hề hiện. | Lượt <3 giây có khuông trống khi chờ rồi xuất hiện cadence giả trên reply. |
| DEFECT-003 | `webui/js/staff-draw.js:19-27,48-76` | Chỉ đặt tối đa 64 phách rồi `break`; phase cuối tạo nhiều nốt trắng 4 phách. | Chuỗi thật hiện tại dừng ở 22 SVG event từ khoảng 75 giây, dù status vẫn đổi. |
| DEFECT-004 | `tests/test_staff_map.py`; `tests/test_staff_draw.py` | Test mapper/SVG rời rạc, dùng 40 nốt đen nhân tạo; không test submit → progress → settle/error. | Suite vẫn xanh khi khuông đóng băng, event đầu/cuối lệch hoặc timer nói dối. |
| DEFECT-005 | `webui/js/staff.js:17-20,49-61`; `webui/js/chat.js:88` | Xóa/replace host nhưng không `ResizeObserver.unobserve`. | Phiên dài giữ detached host trong observer. |
| DEFECT-006 | `webui/js/staff-map.js:18-46,60-63` | Có `Am`, `G`, `poetry`, `memories`, nhưng composer chỉ gửi trong Chat. | Dead contract và test bảo vệ behavior không có caller. |
| DEFECT-007 | `thyca/chat_app.py:133-135,142-152` | Sau khi AgentLoop đã có reply, lượt đầu còn chờ thêm một LLM call đặt title nhưng frontend không biết. | Nếu chỉ instrument AgentLoop, trace lại đứng im trước khi HTTP trả về. |
| DEFECT-008 | `thyca/serve.py:_chat_turn`; `webui/js/chat.js:103-112` | Một POST JSON chặn tới cuối lượt; không có kênh để frontend nhận event thật. | Frontend buộc phải đoán bằng timer. |
| DEFECT-009 | `webui/js/staff-draw.js:19-45,83-111,153-159` | Khuông không có chỉ số nhịp; luôn vẽ sẵn 8 ô; dấu lặng là path tự chế, không phải glyph ký âm chuẩn. | Người đọc không biết meter, ô trống không mang nghĩa nhịp và rest có hình sai quy ước. |
| DEFECT-010 | `webui/js/staff-map.js:79-107`; `webui/js/staff-draw.js:48-76` | Loại/duration/pitch đến từ regex trên câu status. Một status có thể tự tạo whole-note triad; hòa âm đổi theo index event thay vì ranh giới ô nhịp. | Harmonic rhythm và phrase length phụ thuộc latency/từ ngẫu nhiên, không tạo thành bản nhạc nhất quán. |

## Các quyết định đã chốt

### 1. Ý nghĩa và phạm vi

- Nốt nhạc là **operational trace**, không phải chain-of-thought và không phải phân tích nội dung câu trả lời.
- Chỉ Chat có trace. Giọng duy nhất là **Đô trưởng (C major)**.
- Không WebAudio, MIDI, playback, composer, lưu trace hoặc dựng lại trace từ session history.
- Trace của reply mới nhất tồn tại tới khi bắt đầu lượt tiếp theo, đổi trang/mode hoặc reload. History không có khuông.
- Static mock không API chỉ hiện khuông trống trung tính trong khoảng chờ mock; không tự sinh nốt.

### 2. Quy chuẩn âm nhạc bắt buộc

#### 2.1 Meter, pulse và ô nhịp

- Giọng **C major**, khóa Sol, chỉ số nhịp **4/4**. Không dấu hóa đầu khuông.
- Đơn vị nội bộ là `tick`; `1 quarter = 4 ticks`, `1 measure = 16 ticks`. Chọn 16 ticks để biểu diễn quarter, half và whole bằng số nguyên, đồng thời chừa đường mở rộng sau này.
- Không dùng anacrusis/pickup. `turn.accepted` bắt đầu đúng beat 1 của ô đầu. Lý do: trace có độ dài không biết trước; pickup chuẩn sẽ buộc ô cuối bù phần thiếu, làm cadence động phức tạp và dễ sai meter.
- Mỗi ô đã đóng phải có tổng duration đúng 16 ticks. Ô đang chạy cũng được render đủ 16 ticks bằng **placeholder rests**; các rest này chỉ là layout dẫn nhịp, không phải operational event và được tính lại khi event mới đến.
- Beat 1 và 3 là strong beats. Event thật không bị đẩy sang beat giả bằng timer, nhưng normalizer phải làm ranh giới beat nhìn rõ: không duration nào băng qua beat 3; không dùng syncopation trong v1.
- Chỉ dùng `quarter`, `half`, `whole` và rest tương ứng. Không dotted value, eighth/sixteenth, beam, tuplet, tie hoặc grace note.
- Trailing-rest fill cố định: 0 event = whole rest; 1 event = quarter rest ở beat 2 + half rest ở beats 3–4; 2 events = half rest ở beats 3–4; 3 events = quarter rest ở beat 4. Internal silence do `session.naming.started` giữ quarter rest tại đúng beat. Cách này không che midpoint beat 3.
- Mỗi operational event ban đầu chiếm **1 quarter**. Rest chỉ dùng để hoàn chỉnh ô nhịp/cadence, không dùng để giả có hoạt động.
- Nếu event đến khi ô hiện tại còn chỗ, đặt vào beat kế tiếp. Nếu ô đã đủ 4 beat, mở ô mới. Không kéo dài nốt theo wall-clock latency.

#### 2.2 Harmonic grammar

- Harmonic rhythm: **một harmony cho mỗi ô**, không đổi chord giữa ô chỉ vì có thêm tool event.
- Các ô hoạt động đi theo vòng xác định `I → vi → IV → V`, lặp lại theo `measureIndex % 4`:

| Chord | Activity voicing cố định (low–middle–high) |
|-------|--------------------------------------------|
| I | C5–E5–G5 |
| vi | C5–E5–A5 |
| IV | C5–F5–A5 |
| V | B4–D5–G5 |
| vii° (màu lỗi cục bộ) | B4–D5–F5 |

- Các activity voicing cố ý dùng inversion để voice leading êm: I→vi giữ C/E, vi→IV giữ C/A, IV→V đi tối đa một minor third, V→I đi step/common tone. Không dùng root-position triad nối song song cho cả vòng.
- Event trong một ô chỉ được lấy nốt từ chord của ô đó. Ngoại lệ duy nhất là `tool.finished(ok=false)` dùng `vii°` đúng một beat để tạo tension; event sau quay về grammar của ô, không cho hai `vii°` liên tiếp.
- Không có melody giả độc lập. Đây là texture một bè/homophonic cue: note đơn, dyad hoặc triad đều là vertical sonority của harmony hiện hành. Không gán voice identity xuyên qua note đơn/dyad, nên không tuyên bố/test SATB counterpoint cho texture này.
- Với mỗi activity voicing `[low, middle, high]`, mức dày tăng theo significance nhưng không đổi duration:

| Event family | Sonority, đều là quarter |
|--------------|--------------------------|
| `turn.accepted`, `llm.started` | `[low]` anchor |
| `llm.finished` | `[low, high]` outer dyad |
| `tool.started` | `[high]` cue |
| `tool.finished(ok=true)` | `[low, middle, high]` full triad |
| `tool.finished(ok=false)` | full `vii°` |
| `session.naming.started` | quarter rest |
| `session.naming.finished` | `[low]` anchor của harmony hiện tại |

- Vòng I–vi–IV–V là design motif, không tuyên bố là phân tích chức năng của agent event. Không hash tool name, text, token count hay latency thành pitch.

#### 2.3 Cadence và terminal measure

- `turn.completed` không chỉ append hai event vào ô bất kỳ. Normalizer đóng ô hoạt động hiện tại bằng rest tới barline, rồi tạo **một terminal measure riêng đủ 4/4**:

```text
beats 1–2: V triad (G4–B4–D5), half
beats 3–4: I triad (C5–E5–G5), half
final double barline
```

- Đây là authentic `V–I`; dominant phải có B (leading tone), không dùng open fifth G–D. Hai half-note sonorities đặt đúng hai nửa mạnh của ô 4/4 nên midpoint beat 3 vẫn rõ.
- `turn.failed`: đóng ô hiện tại bằng rest tới barline, rồi tạo terminal measure:

```text
beat 1–4: V triad (G4–B4–D5), whole
single barline; không I, không final double barline
```

- Kết trên V là half-cadence/open ending, phù hợp trạng thái chưa giải quyết. Không dùng `vii°` whole ở cuối.
- Không tự thêm terminal event nếu transport chưa gửi `turn.completed`/`turn.failed`.

#### 2.4 Engraving

- Hiện khóa Sol ở đầu mỗi system. `4/4` chỉ hiện ở đầu score/system đầu (và khi meter đổi; v1 không đổi meter), không lặp trên system thứ hai. Mỗi system tối đa 8 measures.
- Chỉ vẽ barline tại ranh giới measure. Không vẽ trước hàng loạt 8 ô rỗng: chiều dài score theo số measure hiện có; ô đang chạy có rests lấp phần chưa dùng.
- Notehead/rest/clef/time signature phải dùng glyph **SMuFL Bravura** self-hosted (WOFF2) hoặc SVG path được sinh từ đúng glyph Bravura và giữ license/attribution. Glyph names/codepoints bắt buộc: `gClef U+E050`, `timeSig4 U+E084` (vẽ hai glyph 4 xếp dọc), `noteheadBlack U+E0A4`, `noteheadHalf U+E0A3`, `noteheadWhole U+E0A2`, `restQuarter U+E4E5`, `restHalf U+E4E4`, `restWhole U+E4E3`. Không tiếp tục ellipse/path rest thủ công. Staff lines/barlines/stems/ledger lines vẫn là SVG primitives theo SMuFL engraving defaults.
- Bravura là reference SMuFL font và có SIL Open Font License; nếu vendoring font thì include OFL file trong repo/package.
- Stem convention cho single voice: với note đơn, dưới dòng giữa stem up, từ dòng giữa trở lên stem down. Với chord, notehead xa dòng giữa nhất quyết định hướng; nếu hai phía cách đều thì stem down. Tất cả note trong chord dùng một stem, không mỗi head một stem; stem up ở bên phải notehead, stem down ở bên trái.
- Quarter có filled head + stem; half có open head + stem; whole có open head, không stem.
- Quarter/half/whole rests phải là glyph đúng duration và đặt đúng staff position. Gộp phần im lặng liên tiếp thành rest lớn nhất **mà vẫn giữ rõ beat 1/3**; không che beat 3 bằng một rest băng qua giữa ô.
- Chord seconds (nếu grammar tương lai thêm) phải offset notehead; grammar hiện tại tránh seconds để renderer v1 không cần xử lý collision đó.
- Ledger line chỉ xuất hiện khi pitch ngoài staff; activity voicing hiện tại giữ trong vùng B4–A5 nên không cần ledger line. Terminal V/I có G4/C5–G5, vẫn nằm trong treble staff. Không đưa bass C4/A3/F3 vào treble staff v1.
- Final double barline chỉ cho completed terminal measure; failed/in-flight dùng single barline.
- SVG có `aria-hidden=true`; status text là semantic progress, score không bị screen reader đọc từng glyph.

#### 2.5 Normalized score model

`staff-map.js` không trả danh sách nốt phẳng. Nó trả pure score model đã normalize:

```js
{
  key: "C",
  meter: { beats: 4, beatType: 4, ticksPerQuarter: 4 },
  measures: [
    { harmony: "I", terminal: null, events: [{ offset: 0, duration: 4, pitches: ["C5"] }], rests: [...] },
    { harmony: null, terminal: "completed", events: [...], rests: [], finalBarline: true }
  ]
}
```

Invariants:

- `offset` và `duration` là integer ticks; `0 <= offset < 16`.
- Event/rest trong cùng measure không overlap; union phủ đúng `[0,16)` cho measure đã render.
- Event không vượt measure; tổng duration mỗi measure là 16 ticks.
- Chord activity measure do `measureIndex`, không do event index sau rolling window.
- Renderer chỉ layout score model; không tự quyết harmony/duration hoặc sửa meter.

### 3. Event contract nội bộ

Tạo `thyca/agent/events.py` với:

- `TurnEvent`: frozen dataclass, có `type` và chỉ các field hữu hạn bên dưới.
- `EventSink = Callable[[TurnEvent], None]`.
- `emit_event(sink, event)`: no-op nếu sink là `None`; bắt exception của sink để telemetry không làm hỏng lượt.

Event hợp lệ:

```json
{"type":"turn.accepted"}
{"type":"llm.started","round":1}
{"type":"llm.finished","round":1,"tool_count":2}
{"type":"tool.started","round":1,"call_id":"call-1","name":"bash"}
{"type":"tool.finished","round":1,"call_id":"call-1","name":"bash","ok":true}
{"type":"session.naming.started"}
{"type":"session.naming.finished","updated":true}
```

Quy tắc:

- `turn.accepted` phát sau khi session đã load và user message đã append thành công.
- `llm.started(round)` đồng thời là dấu mốc bắt đầu loop iteration; không tạo thêm `round.started` dư thừa.
- `llm.finished` phát ngay sau `Think.think`, trước khi chạy tool hoặc persist assistant cuối. `tool_count` là số call mà loop sắp giao cho `Act`, bị giới hạn bởi response validation hiện có; không tin/emit một count tùy ý từ provider.
- `tool.started` phát trong `Act._one`, ngay trước parse-error/dispatch. Parse error và unknown tool vẫn có cặp start/finish với `ok=false`.
- `tool.finished` phát khi từng call thực sự kết thúc; tool song song giữ completion order thật. `Observe` vẫn persist theo declaration order như hiện tại.
- Event đặt tên chỉ phát nếu session chưa có title. `updated=false` gồm LLM title lỗi hoặc title bị sanitize thành `None`; đây không làm turn thất bại.
- Không field nào chứa prompt, message content, reasoning, arguments, result, exception text hoặc path. `tool.name` chỉ được emit sau khi kiểm tra là tên registered tool/identifier hợp lệ; nếu không hợp lệ dùng chuỗi public `tool` thay vì chuyển raw provider text ra UI.
- Không timestamp/sequence ở v1: thứ tự dòng NDJSON là thứ tự chuẩn; không có reconnect/replay.
- `turn.completed`/`turn.failed` là transport terminal do `ChatApp`/HTTP bridge tạo, không phải `AgentLoop` event. `AgentLoop` kết thúc bằng return/exception; điều này giữ đúng ownership cho naming và serialization.

Thứ tự chuẩn cho lượt có tool:

```text
turn.accepted
llm.started(round=1)
llm.finished(round=1, tool_count=N)
tool.started... (declaration/scheduling order)
tool.finished... (actual completion order)
llm.started(round=2)
...
[session.naming.started → session.naming.finished]
turn.completed | turn.failed
```

### 4. HTTP streaming contract

Giữ nguyên `POST /api/sessions/{id}/turn` trả JSON. Thêm:

```text
POST /api/sessions/{id}/turn/stream
Content-Type request: application/json
Body: {"text":"..."}
Success stream: application/x-ndjson; charset=utf-8
```

Mỗi dòng là một JSON object kết thúc bằng `\n`; server flush sau từng dòng.

Các event trung gian dùng schema nội bộ ở trên. Terminal line là một trong hai dạng:

```json
{"type":"turn.completed","detail":{"id":"...","title":"...","model":"...","messages":[],"ask_remember":false,"reply":"..."}}
{"type":"turn.failed","code":"llm_error","message":"provider error đã sanitize"}
```

Error behavior đã chốt:

- JSON/body/text/session lỗi **trước** `turn.accepted`: giữ HTTP `400/404/503` + JSON error như endpoint cũ; chưa mở NDJSON stream.
- Lỗi **sau** `turn.accepted`: HTTP đã là `200`; gửi đúng một terminal `turn.failed` rồi đóng stream.
- Mapping public error phải dùng chung helper với endpoint `/turn`. `LLMError` giữ chuỗi đã redacted/capped theo contract provider hiện tại; `ValueError`/session errors/unexpected dùng message hằng (`invalid text`, `session not found`, `session unreadable`, `session unavailable`, `chat unavailable`). Không gửi `str(ConfigError)` hoặc raw exception qua terminal nếu nó có thể chứa config path/value; chỉ dùng public code/message đã allowlist.
- Endpoint stream bridge một blocking `ChatApp.turn(..., event_sink=...)` sang request bằng `queue.Queue` + một worker thread. Handler đợi item đầu: pre-accept exception thì trả HTTP error bình thường; `turn.accepted` thì mở NDJSON và drain queue.
- Queue không được block AgentLoop. Khi socket `BrokenPipeError`/`ConnectionResetError`, đánh dấu disconnected để sink bỏ event mới; turn vẫn chạy và persist như endpoint hiện tại. Không thêm cancellation trong scope này.
- Dùng HTTP/1.0 connection-close streaming hiện có của `BaseHTTPRequestHandler`; không đổi toàn server sang HTTP/1.1 và không tự viết chunked framing.
- Header stream: `Cache-Control: no-store, no-transform`, `X-Content-Type-Options: nosniff`; không `Content-Length`.

### 5. Event → status và score

Không còn pool thơ/random/classify text. Status dùng `textContent`:

| Event | Status |
|-------|--------|
| Chưa có event server | `Đang chờ Thyca…` |
| `turn.accepted` | `Đã nhận lượt…` |
| `llm.started` | `Đang xử lý vòng {round}…` |
| `llm.finished`, `tool_count > 0` | `Đã chọn {tool_count} công cụ…` |
| `llm.finished`, `tool_count = 0` | `Đang hoàn tất câu trả lời…` |
| `tool.started` | `Đang dùng {name}…` |
| `tool.finished`, ok | `{name} đã xong…` |
| `tool.finished`, lỗi | `{name} gặp lỗi, đang xử lý tiếp…` |
| `session.naming.started` | `Đang đặt tên phiên…` |
| `session.naming.finished` | `Đang hoàn tất…` |
| `turn.completed` | `Đã xong.` |
| `turn.failed` | `Lượt đã dừng.` |

Mapper chỉ dùng event type/round/ok, không dùng status text. Mỗi non-terminal event thêm đúng một quarter sonority theo bảng §2.2 vào beat kế tiếp của activity measure hiện tại; harmony do measure quyết định. Terminal event không đi qua mapping quarter thông thường mà tạo terminal measure đúng §2.3.

UI completion/failure:

- `turn.completed`: append cadence, replace status bằng assistant mới, gắn khuông vào reply vừa sinh; lượt sau xóa khuông cũ. Cadence chỉ hiện sau naming (nếu có), vì terminal do transport phát sau khi `ChatApp.turn` trả detail.
- `turn.failed`: append dominant mở, giữ block status inline với class lỗi tới lượt sau/navigation; composer hint hiện public error. Không dựng assistant giả. Nếu lỗi xảy ra trong naming thì main assistant đã persist nhưng stream vẫn terminal failed theo contract atomic hiện tại; UI không `applyDetail` khi không có completed detail.
- Bắt đầu lượt mới phải xóa failed status cũ và unmount staff cũ trước khi append user mới.
- `prefers-reduced-motion`: cập nhật text/SVG ngay, không animation; event vẫn không bị bỏ.

### 6. Capacity và resize

- Giữ tối đa 2 systems × 8 measures = 16 measures. Budget là **measure**, không phải event/phách rời.
- Window chạy trên normalized measures: terminal measure luôn được giữ; sau đó giữ tối đa 15 activity measures mới nhất. Không cắt giữa measure và không tái đánh harmony từ 0 sau khi cắt — mỗi measure giữ `harmony` đã gán khi được tạo.
- Nếu đã cắt đầu score, cửa sổ được xem là score excerpt mới: system đầu bắt đầu ở barline hợp lệ và hiện khóa Sol + `4/4`; system thứ hai chỉ lặp khóa Sol. Không tạo pickup ảo.
- Một system nếu retained measures `<=8`, hai systems nếu `9..16`. Không thêm system thứ ba hoặc dấu `…` trong v1.
- Score in-flight luôn có một current measure hoàn chỉnh bằng generated rests. Khi terminal đến, normalizer materialize phần padding của current measure thành rests cố định (nếu current measure đã đầy thì giữ nguyên), rồi append terminal measure; không thay/xóa operational events đã render.
- `clearStaffs`/unmount phải gọi `ResizeObserver.unobserve(host)` và xóa host khỏi `watched` trước khi remove. Trước `liveList.replaceWith(fresh)`, unmount status host cũ.

## Tasks

### GOAL-001: Event port trong AgentLoop

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Thêm `thyca/agent/events.py` với `TurnEvent`, `EventSink`, validation field theo contract, allowlist/sanitize `tool.name` và `emit_event` fail-open. Không thêm dependency. | x | 2026-08-26 |
| TASK-002 | `AgentLoop.run(..., event_sink=None)`: phát `turn.accepted`, `llm.started`, `llm.finished` đúng ownership point; CLI/caller cũ không truyền sink vẫn giữ behavior byte-for-byte. | x | 2026-08-26 |
| TASK-003 | `Act.act(stage, event_sink=None)`/`_one`: phát cặp tool start/finish quanh từng call, kể cả parse error/dispatcher exception; `asyncio.gather` và persist order hiện tại không đổi. | x | 2026-08-26 |
| TASK-004 | `ChatApp.turn(..., event_sink=None)` truyền sink xuống loop; `_name_if_needed` phát cặp naming event và trả `updated: bool`; event sink không được lưu trên singleton `ChatApp`/`Act`. | x | 2026-08-26 |

### GOAL-002: NDJSON transport không phá endpoint cũ

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Thêm `_TURN_STREAM_RE` và handler `POST .../turn/stream` trong `thyca/serve.py`; `/turn` JSON cũ giữ nguyên contract/status. | x | 2026-08-26 |
| TASK-006 | Viết bridge queue + worker theo quyết định HTTP: đợi first item trước headers, flush mỗi NDJSON line, terminal exactly-once, disconnect làm sink no-op nhưng không cancel turn; worker luôn enqueue completion sentinel trong `finally` để handler không treo nếu serialization/cleanup lỗi. | x | 2026-08-26 |
| TASK-007 | Tách helper map exception → public `(status, code, message)` để `/turn` và `/turn/stream` không drift và không lộ stack/path/secret. | x | 2026-08-26 |

### GOAL-003: WebUI tiêu thụ event thật

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-008 | `webui/js/chat.js`: đổi `sendChatTurn` sang fetch `/turn/stream`, parse UTF-8 NDJSON qua `ReadableStream` kể cả JSON line bị chia giữa nhiều chunk; non-2xx parse JSON như cũ; chỉ `turn.completed` mới `applyDetail`. | x | 2026-08-26 |
| TASK-009 | Xóa `statusTimer`, `createThinkCycle`, `THINK_PHASES`, `THINK_BREATH`, `CLOSE_LINE`, `classifyThink`, `keyForMode`; begin chỉ vẽ waiting + khuông trống, event server mới cập nhật status/nốt. | x | 2026-08-26 |
| TASK-010 | Chuyển `staff-map.js` thành pure event → normalized score model theo §2: C major, 4/4, integer ticks, measure harmony I–vi–IV–V, generated rests và terminal measures. Unknown event no-op; không hash text/token/tool name. | x | 2026-08-26 |
| TASK-011 | Hoàn thiện completion/failure lifecycle: completed tạo terminal `V half → I half` trên beats 1/3 + final double barline; failed tạo V-whole + single barline; đổi mode khi stream chạy không đụng DOM mode khác nhưng vẫn cập nhật dữ liệu Chat khi terminal về. | x | 2026-08-26 |
| TASK-012 | `staff-draw.js`: render normalized measures, rolling theo barline 16-measure, clef mỗi system nhưng 4/4 chỉ ở đầu score/excerpt, barline/final barline đúng; renderer không tự sửa rhythm/harmony. | x | 2026-08-26 |
| TASK-013 | Thay glyph tự vẽ bằng Bravura/SMuFL đúng names/codepoints §2.4 cho clef, time signature, notehead và rest; self-host WOFF2 hoặc SVG paths đúng glyph, kèm OFL/attribution; update package inclusion nếu thêm asset; staff/stem/bar primitives theo engraving defaults. | x | 2026-08-26 |
| TASK-014 | `staff.js`: thêm unmount/unobserve thật; cleanup khi clear, settle, failure replacement, navigation; không giữ detached host. | x | 2026-08-26 |
| TASK-015 | `app.js`/`render.js`: bỏ import/call timer lifecycle; static mock giữ waiting trung tính rồi gỡ, không sinh fake event/nốt. | x | 2026-08-26 |

### GOAL-004: Verification và tài liệu contract

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-016 | Mở rộng `tests/test_agent_loop.py` và `tests/test_agent_act.py`: text-only order; multi-round; parallel tools completion order; parse error; sink ném exception không làm turn fail. | x | 2026-08-26 |
| TASK-017 | Mở rộng `tests/test_serve_chat.py`: endpoint JSON cũ không đổi; first NDJSON line đến trước khi Slow LLM được release; tool events; completed detail; pre-accept 400/404; post-accept provider error thành terminal failed; content type/header. | x | 2026-08-26 |
| TASK-018 | Thay test timer/text trong `tests/test_staff_map.py` bằng grammar tests: integer ticks, mỗi measure phủ đúng 16 ticks không overlap, harmony cố định, event pitches thuộc chord, unknown no-op, no consecutive vii°, completed/failed terminal đúng. | x | 2026-08-26 |
| TASK-019 | Mở rộng `tests/test_staff_draw.py`: >16 measures vẫn giữ terminal và barline boundary; clef mỗi system nhưng 4/4 chỉ ở đầu score/excerpt; glyph SMuFL đúng duration; note/chord stem direction và stem-side; one/two systems; unobserve detached host. | x | 2026-08-26 |
| TASK-020 | Thêm Node test cho NDJSON parser và browser lifecycle: arbitrary chunk boundaries, fast completion, tool event, failure, mode switch, reduced motion. Không thêm jsdom/dependency; mở rộng DOM/fetch shim hiện có. | x | 2026-08-26 |
| TASK-021 | Sau khi code pass, cập nhật contract trong `.agents/plans/services/agent-loop.md`, ghi rõ assumptions “No stream” ở `webui-chat-backend.md`/`webui-chat-status.md` đã được plan này supersede; chuyển plan này sang `done`. | x | 2026-08-26 |

## Test Plan

Chạy theo thứ tự:

```bash
uv run pytest tests/test_agent_loop.py tests/test_agent_act.py -q
uv run pytest tests/test_serve_chat.py -q
uv run pytest tests/test_staff_map.py tests/test_staff_draw.py -q
uv run pytest -q
```

Acceptance assertions bắt buộc:

1. Lượt text-only có event order chính xác và không có tool event.
2. Hai tool song song emit finish theo actual completion; session vẫn persist declaration order.
3. Slow LLM: client đọc được `turn.accepted` và `llm.started` trước khi reply được release.
4. LLM lỗi sau accepted: stream kết thúc bằng đúng một `turn.failed`; user message vẫn được persist như behavior hiện tại.
5. Lượt nhanh: `turn.accepted` nằm ở beat 1 ô đầu (không pickup); cadence chỉ xuất hiện khi có terminal completed.
6. Mọi measure render đủ 16 ticks, không overlap/overflow; chỉ số nhịp 4/4 và beat 1/3 đọc được bằng rest grouping.
7. Completed terminal measure đúng `V half + I half` tại beats 1/3, đủ 4/4 và final double barline; failed đúng `V whole`, không I và single barline.
8. 100+ musical events: score chỉ giữ tối đa 16 measures, bắt đầu ở barline và vẫn chứa terminal measure/event mới nhất.
9. Mọi non-error sonority là subset của chord activity measure; `vii°` chỉ do failed tool, không lặp liên tiếp; cùng input luôn ra cùng score/SVG.
10. Mỗi system có clef; 4/4 chỉ ở đầu score/excerpt; note/rest dùng đúng Bravura/SMuFL glyph; note/chord dùng stem direction và stem-side theo convention.
11. Sau 50 lượt begin/settle, observer không còn theo dõi host đã detach.
12. Đổi sang Memories trong khi stream chạy: không chèn status/staff vào Memories; quay lại Chat thấy session đã cập nhật.
13. Không NDJSON line nào chứa user text, tool arguments/results, prompt hoặc exception stack.
14. `/api/sessions/{id}/turn` JSON cũ vẫn pass toàn bộ test hiện tại.

Baseline trước implementation (2026-08-26): focused staff tests `14 passed`; full suite `214 passed, 1 failed`. Failure sẵn có là `tests/test_cli.py::test_debug_prints_prompt_flags` chờ `tools=7` nhưng runtime có `tools=8`, không thuộc scope plan này. Implementation không được tạo thêm failure; không sửa baseline đó trong cùng diff.

Manual browser check:

- Lượt <3 giây: accepted xuất hiện ở beat 1 ô đầu; trailing rests vẫn làm rõ beat 3; reply có terminal measure `V half → I half` đủ 4/4.
- Lượt có tool dài: status ghi đúng tool đang chạy; nốt chỉ đổi khi event đến, không đổi đều mỗi 3 giây.
- Tool lỗi nhưng loop phục hồi: `vii°` xuất hiện rồi trace tiếp tục.
- Provider lỗi: block inline kết dominant mở, hint có public error, không có stack/path.
- Reload: không dựng lại khuông cũ.

## Assumptions

1. Target là browser hiện đại có Fetch `ReadableStream` và `TextDecoder`; không làm fallback streaming cho browser cũ.
2. Một `ChatApp` vẫn serialize turn bằng `_turn_lock`; stream không thay đổi policy nhiều tab phải chờ.
3. Client disconnect không cancel LLM/tool vì cancellation có thể gây trạng thái session dở dang; đây là decision riêng ngoài scope.
4. Không persist operational events. Nếu sau này cần Trace history, phải có plan/schema riêng; không nhét event vào `Message.meta`.
5. Không sửa `ToolRegistry` để telemetry hóa toàn hệ thống. Ownership đúng của per-turn tool events là `Act`, vì registry còn được caller khác dùng độc lập.
6. Không stream token hoặc assistant partial text. Terminal `detail` vẫn là full session payload như endpoint cũ.
7. Không thêm package, websocket, SSE framework hay đổi `ThreadingHTTPServer`.
8. Code/identifier bằng English; status UI bằng Vietnamese.
9. Ở đây “đúng âm nhạc” nghĩa là meter/measure duration, rest grouping, tonal pitch set, harmonic rhythm, cadence và engraving convention đúng. Trace không cần tạo thành giai điệu biểu cảm độc lập.

## Research basis

Quy ước được dùng để chốt grammar:

- Open Music Theory — simple meter: simple quadruple có 4 beats; trong simple meter mẫu số 4 nghĩa quarter note nhận một beat: https://openmusictheory.github.io/meter.html
- University of Puget Sound — rhythmic notation phải làm beat/downbeat rõ; trong 4/4 beat 1 và 3 là strong beats, syncopation là ngoại lệ chứ không phải default: https://musictheory.pugetsound.edu/mt21c/CommonRhythmicNotationErrors.html
- Open Music Theory — authentic cadence là root-position `V–I`; kết trên V không về I là half cadence: https://openmusictheory.github.io/cadenceTypes.html
- University of Puget Sound — voice leading ưu tiên independence, voicing rõ và economy of motion: https://musictheory.pugetsound.edu/mt21c/VoiceLeading.html
- SMuFL — canonical glyph names/codepoints cho clef, time signature, noteheads và rests: https://github.com/w3c-cg/smufl/blob/gh-pages/metadata/glyphnames.json
- SMuFL — restWhole và restHalf có placement khác nhau nên không thay bằng một path tự chế: https://w3c-cg.github.io/smufl/releases/1.4/tables/rests.html
- SMuFL engraving defaults — line widths/spacing được biểu diễn theo staff spaces; staff lines nên vẽ bằng primitives: https://www.w3.org/2019/03/smufl13/specification/engravingdefaults.html
- Bravura là reference SMuFL font, có WOFF/WOFF2/SVG và phát hành theo SIL OFL: https://www.w3.org/2021/03/smufl14/about/implementations.html
- Elaine Gould, *Behind Bars* — tham chiếu bổ sung cho stem/grouping/engraving; chỉ dùng official sample, không phụ thuộc bản scan không rõ license: https://www.behindbarsnotation.co.uk/contents/sample_pages.pdf

Các mục là **design choice**, không phải luật lý thuyết phổ quát: key C major, motif I–vi–IV–V, activity voicings/inversions, event density một quarter/event, `vii°` cho tool error, terminal measure V-half→I-half, tối đa 16 measures.
