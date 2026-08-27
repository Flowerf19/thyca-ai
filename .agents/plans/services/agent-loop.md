---
status: done
created: 2026-08-14
last_updated: 2026-08-27
---

# Service — Agent Loop (`thyca/agent/`)

> 7/7. Bốn pha + `Stage` + `Cli` (TASK-317). Không `thyca/agent.py` shim.

## Summary

`assemble → think → act → observe` trên một `Stage`. `cli.py` REPL / `-p` / `--continue` / `--session` / `--model`.

## Class trong package

| Class | File | Việc |
|-------|------|------|
| `Stage` | `stage.py` | workspace lượt: `messages`, `round`, `reply`, `results`, `llm_latency_ms`, `llm_model`, `llm_cost_usd`, `tool_latencies` |
| `Assemble` | `assemble.py` | `assemble(stage, user_msg)` |
| `Think` | `think.py` | `think(stage)` ghi `stage.reply` |
| `Act` | `act.py` | `act(stage)` gather, ghi `stage.results` |
| `Observe` | `observe.py` | compact / user / assistant / observe / loop_limit |
| `AgentLoop` | `loop.py` | tạo `Stage`, vòng `loopMax` |

`Message` / `ToolCall` / `ToolResult` chỉ ở `thyca/protocol.py`.

## Contracts

### Stage

Dataclass không frozen. `messages` / `results` `default_factory=list`. `round >= 0`. Không I/O.

### Assemble

`assemble(stage, user_msg)`: copy `stage.messages` + user. `hot` trên stage, v1 không inject system — cố ý, tests yêu cầu hot unused (chưa nối `PromptManager`). Non-str → `ValueError`.

### Think

`ChatReply` sống ở `thyca/llm/llm_base.py` (cùng `Connect` ABC + `LLMError`); `think.py` chỉ import lại. Port là `LLMPort` (Protocol, khai báo trong `think.py`): `chat(messages, tools=None) -> ChatReply`. `Think.think(stage)` gọi port với `stage.messages` / `stage.tools`, ghi `stage.reply`.

### Act

`async act(stage)`: gather `stage.reply.tool_calls` → `stage.results`. `parse_error` không dispatch; exception → `is_error`; ép id/name theo call.

### Observe

- `compact()` → `sessions.compact_if_needed()`
- `user(stage)` → append `stage.messages[-1]`
- `assistant(stage)` → append text `stage.reply` với `meta` (`kind`, `round`, `model`, `latency_ms`, `usage`, `cost_usd`, `finish_reason`); return text
- `observe(stage)`: order results theo call id; append assistant+tool; tool `meta.latency_ms` / `meta.round`; `stage.messages.extend`; id lệch → `ValueError`
- `loop_limit(stage)` → append `"loop limit reached"` + `meta.status=loop_limit`

### Events (2026-08-26)

`thyca/agent/events.py`: `TurnEvent`, `EventSink`, `emit_event` (fail-open).
`AgentLoop.run(..., event_sink=None)` emits `turn.accepted` after the user
message is persisted, then `llm.started` / `llm.finished` each round.
`Act.act(..., event_sink=None)` emits `tool.started` / `tool.finished`
(completion order). `turn.completed` / `turn.failed` are transport-only.
Callers that omit `event_sink` are unchanged.

### AgentLoop

```
stage = Stage(messages=copy session, hot=hot, tools=tools)
observe.compact()
assemble.assemble(stage, user_msg)
observe.user(stage)
for _ in 1..loop_max:
  stage.round += 1
  await think.think(stage)
  if not stage.reply.tool_calls:
    return observe.assistant(stage)
  await act.act(stage)
  observe.observe(stage)
  if stage.round == loop_max:
    return observe.loop_limit(stage)
```

Giữ `SessionManager.current`. Không planner / prefetch / subagent.

`AgentLoop.__init__` nhận `tools`, `model`, `pricing`. Sau `think`: `cost_for(stage.llm_model, usage, pricing)` (fallback config model). `Act` đo từng tool song song, merge `stage.tool_latencies`. Naming title (`ChatApp._name_if_needed`) chưa ghi `kind: "naming"` vào JSONL.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-315 | `run.py` RunGate (split cũ) | x | 2026-08-19 |
| TASK-316 | `loop.py` split cũ | x | 2026-08-19 |
| TASK-317 | `thyca/cli.py` REPL / `-p` / `--continue` / `--session` / `--model` | x | 2026-08-20 |
| TASK-318 | compact trong split cũ | x | 2026-08-19 |
| TASK-319 | `Turn` (superseded) | x | 2026-08-19 |
| TASK-320 | `LoopPolicy` (superseded) | x | 2026-08-19 |
| TASK-321 | bốn pha Assemble/Think/Act/Observe | x | 2026-08-19 |
| TASK-322 | `Stage` workspace chung; bốn pha nhận `Stage` | x | 2026-08-19 |

## Test Plan

- Stage: list isolated; round âm raise
- Assemble/Think/Act/Observe nhận `Stage`
- AgentLoop: compact trước think; 2 call lệch thời gian vẫn đúng thứ tự; loop_max; parse_error

## Assumptions

- `Assemble` inject system khi `hot` là `ActiveSnapshot`. Registry + `memory_*` + MCP đã nối CLI/ChatApp.
