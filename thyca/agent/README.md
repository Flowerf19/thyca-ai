# Agent — assemble / think / act / observe

Bốn pha thao tác một `Stage` chung. `loop.py` tạo stage rồi gọi bốn class. Không HTTP, không builtin. `Message` / `ToolCall` / `ToolResult` ở `thyca/protocol.py`. `Cli` ở `thyca/cli.py`.

Không `thyca/agent.py` shim.

## Class

| Class | File | Việc |
|-------|------|------|
| `Stage` | `stage.py` | workspace lượt: `messages`, `round`, `reply`, `results` |
| `Assemble` | `assemble.py` | `assemble(stage, user_msg)` |
| `Think` | `think.py` | `think(stage)` → ghi `stage.reply` |
| `Act` | `act.py` | `act(stage)` → ghi `stage.results` |
| `Observe` | `observe.py` | compact / user / assistant / observe / loop_limit |
| `AgentLoop` | `loop.py` | tạo `Stage`, vòng `loopMax` |

```text
thyca/agent/
  stage.py
  assemble.py
  think.py
  act.py
  observe.py
  loop.py
  README.md
```

## Vòng một lượt

```mermaid
flowchart TD
    L["AgentLoop.run"] --> S["Stage từ session.messages"]
    S --> C["Observe.compact"]
    C --> A["Assemble.assemble(stage)"]
    A --> U["Observe.user(stage)"]
    U --> T["Think.think(stage)"]
    T --> D{"stage.reply.tool_calls?"}
    D -->|không| P1["Observe.assistant(stage)"]
    P1 --> OUT["return text"]
    D -->|có| R["Act.act(stage)"]
    R --> O["Observe.observe(stage)"]
    O --> M{"round == loopMax?"}
    M -->|không| T
    M -->|có| LIM["Observe.loop_limit(stage)"]
    LIM --> OUT
```

```mermaid
classDiagram
    class Stage {
        +messages: Message[]
        +round: int
        +reply: ChatReply
        +results: ToolResult[]
    }
    class Assemble {
        +assemble(stage, user_msg) void
    }
    class Think {
        +async think(stage) ChatReply
    }
    class Act {
        +async act(stage) ToolResult[]
    }
    class Observe {
        +compact() bool
        +user(stage) void
        +assistant(stage) str
        +observe(stage) void
        +loop_limit(stage) str
    }
    class AgentLoop {
        +async run(user_msg) str
    }
    AgentLoop --> Stage
    AgentLoop --> Assemble
    AgentLoop --> Think
    AgentLoop --> Act
    AgentLoop --> Observe
    Assemble --> Stage
    Think --> Stage
    Act --> Stage
    Observe --> Stage
    Think ..> LLMPort
    Observe --> SessionManager
    Act --> ToolDispatcher
```

## Ranh giới

| Việc | Không nằm đây |
|------|----------------|
| Session JSONL I/O thô | `thyca/sessions/` |
| Hot files | `thyca/memory/active.py` |
| Tool handlers | `thyca/tools/` |
| OpenAI HTTP | `thyca/llm/client.py` (sau) |
| REPL / `-p` | `thyca/cli.py` |
