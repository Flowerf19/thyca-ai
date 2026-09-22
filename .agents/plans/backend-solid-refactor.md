---
status: in-progress
created: 2026-09-22
last_updated: 2026-09-22
---

# Backend SOLID refactor — plan tổng (orchestrator)

## Summary

Refactor + tối ưu toàn bộ backend `thyca/` (trừ `thyca/webui/` để phase sau): code sạch, chuẩn SOLID,
không đổi behavior. Mọi file lẻ `thyca/*.py` phải chuyển vào thư mục module riêng — sau refactor
`thyca/` chỉ còn `__init__.py` + `__main__.py` ở top-level.

- Nhánh: `refactor/backend-solid` (từ `main` @ 21e608a). Plans cũ đã đóng hết vào `plans/done/`.
- Orchestrator: main session (`meta/muse-spark-1.3`). Teams: planning/coding/test chạy `meta/muse-spark-1.3-contributor`; **review agents chạy `meta/muse-spark-1.3` effort max** (chốt chất lượng 2026-09-22).
- Mỗi module 1 team, mỗi team đi 4 chặng có gate: **planning → coding → test → review**.
- Coding chạy `isolation: worktree`, mỗi team 1 nhánh `refactor/backend-solid/M<n>-<name>`;
  orchestrator merge sau khi team pass review.

### Module-table (8 modules, backend-only)

| # | Module | Thư mục đích | Nguồn hiện tại | Dòng ~ | Team |
|---|--------|--------------|----------------|--------|------|
| M1 | agent | `thyca/agent/` (giữ) | `agent/*.py` | ~720 | Team-agent |
| M2 | llm | `thyca/llm/` (giữ) | `llm/*.py` | ~1.200 | Team-llm |
| M3 | config | `thyca/config/` + gom `config_schema.py` vào | `config/*.py`, `config_schema.py` | ~1.200 | Team-config |
| M4 | memory | `thyca/memory/` (giữ) | `memory/*.py` | ~1.800 | Team-memory |
| M5 | sessions | `thyca/sessions/` + gom `session_wire.py` vào | `sessions/*.py`, `session_wire.py` | ~1.250 | Team-sessions |
| M6 | tools | `thyca/tools/` + gom `skills.py` vào (hoặc `skills/` mới — team quyết) | `tools/**/*.py`, `skills.py` | ~1.850 | Team-tools |
| M7 | serve | `thyca/serve/` **mới** | `serve.py` 600, `bridge.py` 486, `serve_daemon.py`, `serve_memory.py`, `trace.py`, `trace_api.py`, `turn_state.py` | ~1.830 | Team-serve |
| M8 | app | `thyca/app/` **mới** + `thyca/core/` **mới** (chỉ `protocol.py` — leaf stdlib-only) | `chat_app.py` 537, `chat_ui.py`, `cli.py`, `onboarding.py`, `protocol.py`→`core/` | ~1.370 | Team-app |

File oversize (>400 dòng) phải tách: `serve.py`, `bridge.py`, `chat_app.py` (mục tiêu đầu tiên);
`archive_store.py` 396, `memory.py` 353 sát ngưỡng — team tự quyết tách hay giữ + lý do.

### GOAL-001: Planning per-module (8 plans)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | 8 planning agents đọc sâu module được giao (code + tests + consumers), viết `.agents/plans/modules/M<n>-<name>.md` theo format skill implementation-planner, ghi rõ target layout + thứ tự tách file + rủi ro cycle | x (8/8 plans, wf_b110a1f870a0) | 2026-09-22 |
| TASK-002 | Orchestrator duyệt 8 module plans (layout nhất quán, không tranh file, không cycle chéo) rồi mới mở GOAL-002 | x (orchestrator approved 8/8 + 5 layout decisions §dưới; chờ user duyệt mới chạy layout agent) | 2026-09-22 |

### GOAL-002: Physical layout (1 agent, mechanical)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Layout agent `git mv` file lẻ vào thư mục đích theo layout đã duyệt, sửa imports (runtime + tests + scripts), không refactor logic trong bước này | x (15 files + 4 __init__, ~60 import sites, commit f2702a7) | 2026-09-22 |
| TASK-004 | Verify sau move: `uv run pytest -q` xanh như baseline, check không circular import, `git diff --check` sạch → 1 commit `chore(layout): ...` | x (719 passed parity, imports ok, diff clean — orchestrator re-verified) | 2026-09-22 |

### GOAL-003: SOLID refactor per-module (8 teams song song)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | M1 agent: refactor theo module plan → pytest module → review độc lập | x (code 527f939 + test 719 + review approve, 1 minor accepted) | 2026-09-22 |
| TASK-006 | M2 llm: refactor theo module plan → pytest module → review độc lập | x (code 3286e01 + test 719 + review approve; I001 fixed at merge) | 2026-09-22 |
| TASK-007 | M3 config: refactor theo module plan → pytest module → review độc lập | x (code c725758 + test 719 + review approve) | 2026-09-22 |
| TASK-008 | M4 memory: refactor theo module plan → pytest module → review độc lập (đọc decision L2-hybrid-v1 trước khi đụng memory contract) | x (code 341a473 + test 719 + review approve, zero findings) | 2026-09-22 |
| TASK-009 | M5 sessions: refactor theo module plan → pytest module → review độc lập (giữ 4-class SOLID Session/Store/Compactor/Manager) | x (code 203bcc0 + test 719 + review approve) | 2026-09-22 |
| TASK-010 | M6 tools: refactor theo module plan → pytest module → review độc lập (giữ memory tools contract + MCP stdio) | x (code feb784c + test 719 + review approve, zero findings) | 2026-09-22 |
| TASK-011 | M7 serve: refactor theo module plan → pytest module → review độc lập (serve chỉ loopback; API không lộ secret/path/stack) | x (code 39f293f + test 719 + review approve; turn_stream deviation accepted; cycle fix verified) | 2026-09-22 |
| TASK-012 | M8 app: refactor theo module plan → pytest module → review độc lập | x (code c3f734d + test 719 + review approve; toolchain.py accepted) | 2026-09-22 |
| TASK-013 | Orchestrator merge 8 nhánh team vào `refactor/backend-solid`, giải quyết conflict (ưu tiên giữ behavior + tests xanh) | x (8 merges + conflict chat_app.py resolved M8-side + ruff I001 8a1dd7c; cycle probes all pass) | 2026-09-22 |

### GOAL-004: Integration + docs

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-014 | Full `uv run pytest -q` + `git diff --check` trên nhánh tích hợp; mọi finding phải có evidence (log/test/file:line) | x (719 passed, diff clean, max file archive_store.py 400 lines) | 2026-09-22 |
| TASK-015 | Review tổng độc lập 1 lượt toàn diff (scope, SOLID, không behavior change lén) | | |
| TASK-016 | Cập nhật `.agents/AGENT_RULES.md`, `PROJECT_CONTEXT.md`, `README.md`, root `README.md`, `CHANGELOG.md` theo tree mới | | |

### Layout decisions (orchestrator, 2026-09-22 — sau review 8 module plans)

1. **`turn_state.py` → `thyca/serve/`** (M7 thắng): file import `thyca.sessions` (M5) nên không phải leaf, không được vào `core/`. M8 import một chiều `thyca.serve.turn_state`; M7 cấm import runtime `thyca.app` (chỉ `TYPE_CHECKING`, M7-TASK-013).
2. **`protocol.py` → `thyca/core/`** (M8, duyệt): stdlib-only leaf, ~27 importers runtime; đặt dưới `app/` sẽ gây layer inversion. `core/` không được import bất kỳ module `thyca.*` nào khác.
3. **Không shim top-level**: layout agent sửa cơ học mọi import site (runtime + tests + scripts + `pyproject.toml` entry + `__main__.py`) trong 1 commit; `thyca/` top-level chỉ còn `__init__.py` + `__main__.py`. Áp dụng cho cả `protocol` (M8), `skills` (M6 giữ `from thyca.skills import ...` qua package `__init__`), `config_schema` (M3), `session_wire` (M5).
4. **Layout agent chỉ move 1:1, không split**: `serve.py`→`serve/server.py`, `bridge.py`→`serve/bridge.py` (team M7 split sau), `chat_app.py`→`app/chat_app.py` (team M8 split sau), còn lại theo bảng module + module plans. GOAL-001 physical-move trong plans M3/M5/M6/M7/M8 do layout agent thực hiện; teams verify parity rồi mới refactor.
5. **M1-TASK-003** (Assemble inject bắt buộc + patch 2 call-site M8): duyệt, M8 apply patch; fallback giữ signature cũ có ghi lý do nếu M8 từ chối.

## Test Plan

- Baseline trước refactor (ghi lại ở GOAL-002): full pytest pass count + known failure
  `test_debug_prints_prompt_flags` (`tools=7` vs thực tế — không "sửa" số này).
- Mỗi team: focused tests module mình + full suite trước khi xin review.
- Gate merge: full suite xanh bằng hoặc hơn baseline, không circular import
  (`python -c "import thyca.serve, thyca.app, ..."` hoặc tương đương), `git diff --check` sạch.
- Không Fake-green: không sửa test để pass trừ khi contract đổi có ghi trong module plan đã duyệt.

## Assumptions

1. `thyca/webui/` (frontend) ngoài scope — backend giữ nguyên API contract phục vụ WebUI.
2. Không thêm dependency, abstraction, feature ngoài refactor; behavior giữ nguyên.
3. SOLID áp dụng thực dụng: SRP là chính (tách file oversize), DIP cho biên module
   (Protocol thay import trực tiếp khi team chứng minh cycle/rủi ro); không nhồi pattern.
4. Module plans của teams được orchestrator duyệt mới có hiệu lực; layout trong plan tổng
   là đề xuất, teams được đề xuất chỉnh với lý do.
5. Secret/path nội bộ/stack không bao giờ lọt vào API response hay log mới.
6. Mỗi team branch từ `refactor/backend-solid` sau commit layout (GOAL-002); rebase trước merge.
7. Review agents (per-team TASK-005..012 + review tổng TASK-015) luôn dùng `meta/muse-spark-1.3` effort `max`, skill `code-reviewer`, đọc actual diff — không review chay.
8. Contributor agents (planning/coding/test) mặc định effort `xhigh` (user chốt 2026-09-22). Baseline sau layout: **719 passed / 0 fail** (fail `test_debug_prints_prompt_flags` cũ không tái hiện — tools count đã khớp).
9. **Coder KHÔNG commit; reviewer commit khi pass** (user chốt 2026-09-22): coding agents chỉ viết code + chạy test + báo evidence (worktree giữ work trên nhánh team); review agent (`1.3 max`) verify actual diff + test evidence, nếu `approve` thì gộp thành đúng 1 commit sạch `refactor(<module>): ...` trên nhánh team. Batch GOAL-003 đang chạy giữ cách cũ (coders commit) vì đã tung — rule mới áp từ review workflow + mọi fix-round sau.
10. **Agent prompts in English** (user chốt 2026-09-22): mọi prompt cho subagents viết tiếng Anh — tiếng Việt không dấu dễ bị hiểu sai. Áp từ review workflow trở đi. (Báo cáo với user vẫn tiếng Việt.)
