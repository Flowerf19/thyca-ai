# Review: `filtered_tool_schemas` — poll tools `tool_read`/`tool_kill` (repo: /home/flowerf/Projects/thyca-ai)

## 1. Kết luận ngắn

**Không phải bug.** Giả thuyết "bot con không bao giờ thấy `tool_read`/`tool_kill` kể cả khi card có liệt kê" **sai**.

Nhánh `if not card.system:` lọc bằng `keep &= set(card.tools) | set(BASELINE_BOT_TOOLS)` — nó **union** với `card.tools`, nên
`tên đã được card liệt kê (kể cả `tool_read`/`tool_kill`) đều được giữ lại. `BASELINE_BOT_TOOLS` không chứa
`tool_read`/`tool_kill`, nhưng điều đó chỉ có nghĩa là *poll tools không được cấp ngầm*, chứ không có nghĩa là
*bị loại bỏ khi card đã liệt kê*.

Cái thực sự lệch là **ngược lại** (ở nhánh system) và là **sai lệch docstring**, không phải lỗi hành vi với bot con.

## 2. Bằng chứng (đọc code thật + chạy thật)

### 2.1 Mã nguồn liên quan

- `thyca/bots/runtime.py:91` — `def filtered_tool_schemas(schemas, card)`.
- `thyca/bots/runtime.py:134-137` — nhánh bot con:
  ```python
  if not card.system:
      # Fail-closed: the card's list plus the always-kept baseline
      # (see docstring) is exhaustive for child bots.
      keep &= set(card.tools) | set(BASELINE_BOT_TOOLS)
  ```
  → `keep` bị giao với `(card.tools ∪ BASELINE_BOT_TOOLS)`. `tool_read` nằm trong `card.tools` thì **được giữ**.
- `thyca/bots/runtime.py:138-139` — nhánh system (chỉ chạy khi `card.system` **và** `card.tools` khác rỗng):
  ```python
  elif card.tools:
      keep &= set(card.tools) | {"tool_read", "tool_kill"}
  ```
- `thyca/bots/card.py:70-83` — `BASELINE_BOT_TOOLS` = `bot_send, bot_fetch, task_open, message_user, ask_user,
  ask_private, private_followup, report_error, memory_remember, memory_search, memory_get,
  memory_propose_team, memory_tasks` → **không có** `tool_read`/`tool_kill`.
- `thyca/bots/runtime.py:65` — `PEER_TOOL_NAMES`; `ADMIN_TOOL_PREFIX = "bot_"` (runtime.py:50). `tool_read`/`tool_kill`
  không bắt đầu bằng `bot_` nên không bị rơi vào nhánh drop admin tools.
- Chỗ gọi: `thyca/bots/runtime.py:608` (`filtered = filtered_tool_schemas(scope.tools, card)` — nguồn schema là
  snapshot root đầy đủ từ `chat_app.py:1174 tools=self._tools`) và `thyca/app/chat_app.py:1763-1769` (chỉ để
  ước lượng token, cùng một hàm nên kết quả nhất quán).
- Runtime còn bind allowlist cho bot con: `runtime.py:731-735` (`allowed = names in filtered`) → `BotPolicy.check`
  (`thyca/bots/policy.py:79-83`) chặn gọi tool ngoài allowlist. Tức là nếu `tool_read` lọc qua schema filter thì
  nó cũng được phép gọi ở tầng policy (không có tầng nào chặn riêng `tool_read`).

### 2.2 Chạy thật (không sửa file nguồn)

```
child card tools=('read','tool_read','tool_kill')
  -> ['read', 'tool_read', 'tool_kill', 'bot_send']     # poll tools CÓ hiện
child card tools=()  (rỗng)
  -> []                                                # fail-closed đúng như docstring
system card tools=()  -> ['read', 'tool_read', 'tool_kill', 'bash']   # full snapshot
system card tools=('read','bash') -> ['read', 'tool_read', 'tool_kill', 'bash']  # bị hẹp: mất 'write'
```

### 2.3 Test sẵn có đã khoá hành vi này

`tests/test_bots_runtime.py:98-111` (`test_child_empty_tools_allowlist_is_fail_closed`):

```python
card2 = _card(tools=("read", "tool_read"))
out2 = filtered_tool_schemas(_schemas("read", "tool_read", "tool_kill"), card2)
assert [s["function"]["name"] for s in out2] == ["read", "tool_read"]
```

→ Poll tools xuất hiện khi card liệt kê, biến mất khi card không liệt kê. Đúng như docstring mô tả cho **bot con**.

## 3. Chỗ thực sự lệch (nhỏ, không phá hành vi bot con)

1. **Câu docstring đọc chung, nhưng chỉ đúng cho bot con.**
   "the gateway poll tools (`tool_read`/`tool_kill`) stay visible only when the card lists them explicitly" — với
   **system card có `tools` khác rỗng** thì ngược lại: hai poll tools **luôn** được cấp dù card có liệt kê hay không
   (`runtime.py:139`). Cần scope câu này vào bot con.
2. **"The Thyca system card keeps the full snapshot (root/tools parity)" không đúng khi system card có `tools`.**
   `runtime.py:138-139` hẹp snapshot của system card xuống `card.tools ∪ {tool_read, tool_kill}` — mọi tool khác bị
   rơi (xem kết quả chạy: `write` bị mất). Câu này chỉ đúng khi system card **không** liệt kê `tools`
   (card seed `thyca/seeds/bots/thyca/TWIN.md` hiện không có mục `tools:` → `card.tools == ()` → nhánh `elif`
   không chạy → full snapshot đúng như thiết kế).
3. **"an empty `tools` means NO tools at all"** đã lỗi thời so với đoạn ngay sau nó: baseline luôn được giữ cho bot con
   (được ghi nhận ở commit `a173e80`). Hai câu trong cùng docstring đang mâu thuẫn nhẹ (đoạn "One exception..." mới là
   mô tả đúng).

## 4. Bản vá đề xuất (chưa áp dụng — file nguồn không bị sửa)

### Phương án A — KHỚP RECOMMEND: chỉ sửa docstring (không đổi hành vi)

Rủi ro: không có. Hành vi hiện tại đã đúng với ý đồ (bot con fail-closed; system card seed giữ full snapshot).

```diff
--- a/thyca/bots/runtime.py
+++ b/thyca/bots/runtime.py
@@ -99,9 +99,12 @@ def filtered_tool_schemas(
     ``bash: deny`` drop their tool families.
 
     Fail-closed for non-system bots: ``tools`` *is* the allowlist, so an
-    empty ``tools`` means NO tools at all — nothing is implicit, and the
-    gateway poll tools (``tool_read``/``tool_kill``) stay visible only
-    when the card lists them explicitly. The Thyca system card keeps the
-    full snapshot (root/tools parity).
+    empty ``tools`` means only the always-kept baseline below, nothing
+    else is implicit. The gateway poll tools (``tool_read``/``tool_kill``)
+    are not part of that baseline: for a child bot they stay visible only
+    when the card lists them explicitly. A system card with an empty
+    ``tools`` keeps the full snapshot (root/tools parity); a system card
+    that lists ``tools`` is narrowed to that list plus the two gateway
+    poll tools, which it therefore always sees.
 
     One exception, recorded by the context review: the current
     ``BASELINE_BOT_TOOLS`` (peer send/fetch, ``task_open``, memory
```

Ghi chú: cả 3 hunk trong file này đã được kiểm chứng bằng `git apply --check` (áp dụng sạch, KHÔNG sửa file nguồn).
Hunk `p0` chỉ đổi docstring, `p1` chỉ thêm comment, `p2` thêm một test — không đổi logic lọc tool.

### Phương án B — TUỲ CHỌN (chỉ khi muốn system card luôn full snapshot)

Nếu ý đồ là "system card luôn có full snapshot" thì nhánh `elif` đang hẹp snapshot một cách không chủ ý
(system card tự liệt kê `tools` sẽ mất tool khác). Bản vá hành vi:

```diff
--- a/thyca/bots/runtime.py
+++ b/thyca/bots/runtime.py
@@ -134,8 +134,10 @@ def filtered_tool_schemas(
     if not card.system:
         # Fail-closed: the card's list plus the always-kept baseline
         # (see docstring) is exhaustive for child bots.
         keep &= set(card.tools) | set(BASELINE_BOT_TOOLS)
     elif card.tools:
-        keep &= set(card.tools) | {"tool_read", "tool_kill"}
+        # A system card that lists tools narrows the snapshot to that
+        # list plus the two gateway poll tools (unchanged intent).
+        keep &= set(card.tools) | {"tool_read", "tool_kill"}
     # NOTE: card.bash == "ask" keeps the bash tool: the ask-approval gate
     # is future UX work, not a silent deny (honest default, recorded here).
```

(Phương án B trên chỉ là bình luận; nếu muốn **bỏ** hẹp thì xoá hẳn 2 dòng `elif card.tools:` + `keep &= ...` —
khi đó system card luôn thấy toàn bộ snapshot kể cả `bot_*` admin tools. Đây là thay đổi hành vi, cần quyết định
riêng của maintainer, KHÔNG nên làm trong lần review này.)

### Phương án C — test khoá lại hành vi (tùy chọn, file test, không sửa nguồn)

```diff
--- a/tests/test_bots_runtime.py
+++ b/tests/test_bots_runtime.py
@@ -108,8 +108,19 @@ def test_child_empty_tools_allowlist_is_fail_closed() -> None:
     assert [s["function"]["name"] for s in out2] == ["read", "tool_read"]
     # The Thyca system card keeps its full snapshot with an empty list.
     thyca = _card(bot_id="thyca", name="thyca", system=True)
     out3 = filtered_tool_schemas(_schemas("read", "bash"), thyca)
     assert {s["function"]["name"] for s in out3} == {"read", "bash"}
 
 
+def test_child_bot_sees_poll_tools_when_card_lists_them() -> None:
+    # Regression guard: the poll tools are NOT baseline, but a child card
+    # that lists them explicitly must still see them (card.tools is
+    # unioned into the fail-closed allowlist, not intersected away).
+    card = _card(tools=("read", "tool_read", "tool_kill"))
+    out = filtered_tool_schemas(
+        _schemas("read", "tool_read", "tool_kill", "write"), card)
+    assert [s["function"]["name"] for s in out] == [
+        "read", "tool_read", "tool_kill"]
+
+
 def test_gateway_policy_denies_hidden_by_name() -> None:
```

## 5. Kết luận hành động

- **Không có bug** ở nhánh bot con → không cần sửa logic `keep &= set(card.tools) | set(BASELINE_BOT_TOOLS)`.
- Bản vá nên áp dụng: **Phương án A** (sửa docstring cho khớp hành vi) + tùy chọn **Phương án C** (test khoá).
- Điểm còn rủi ro/cần người duyệt: **Phương án B** — system card tự liệt kê `tools` sẽ mất phần lớn tool
  (kể cả tool root khác `tool_read`/`tool_kill`). Card seed hiện tại không bị ảnh hưởng vì không có mục `tools:`.
