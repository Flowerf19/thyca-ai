---
status: done
created: 2026-09-22
last_updated: 2026-09-22
---

# M3 — config module plan

## Summary

Module config đã là package tách sẵn (`thyca/config/`, 17 files, lớn nhất
`parsing.py` 248 dòng — dưới ngưỡng oversize 400). Công việc M3 chủ yếu là
cơ học: gom file lẻ `thyca/config_schema.py` (121 dòng) vào package với tên
đích `thyca/config/schema.py`, sửa imports tương đối để tránh cycle, giữ
nguyên behavior và public API (`from thyca.config import ...` +
`config_schema()`). Không tách file oversize (không có), không thêm
abstraction, không đổi contract phục vụ WebUI settings panel.

Tên đích đề xuất cho `config_schema.py`: `thyca/config/schema.py`. Lý do:
ngắn gọn, khớp quy ước tên một từ của các sibling (`root`, `store`,
`parsing`, `auth`, `secrets`); tên hàm `config_schema()` giữ nguyên nên
consumers chỉ đổi import path. Bác `settings_schema.py` (serve API gọi nó là
`schema` chung, không riêng settings) và `ui_schema.py` (backend không đặt
tên theo frontend; WebUI ngoài scope refactor).

## Tasks

### GOAL-001: Physical layout — gom `config_schema.py` vào package

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | `git mv thyca/config_schema.py thyca/config/schema.py`; viết lại import đầu file từ `from thyca.config import (REASONING_EFFORTS, LimitsCfg, ProviderEntry, TimelineCfg)` sang relative sibling imports (`from .defaults import REASONING_EFFORTS`, `from .limits import LimitsCfg`, `from .providers import ProviderEntry`, `from .timeline import TimelineCfg`) để `thyca/config/__init__.py` re-export được mà không cycle | | |
| TASK-002 | Re-export trong `thyca/config/__init__.py`: thêm `from .schema import config_schema` + `"config_schema"` vào `__all__` (đặt sau entity imports); cập nhật 2 consumers: `thyca/serve.py:29` → `from thyca.config import config_schema` (gộp vào import `thyca/serve.py:28`), `tests/test_serve_config.py:15` → `from thyca.config import config_schema`; không để lại shim `thyca/config_schema.py` (plan tổng yêu cầu top-level chỉ còn `__init__.py` + `__main__.py; chỉ 2 consumers nội bộ) | | |

### GOAL-002: SOLID micro-cleanups (bounded, behavior-neutral)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Xóa dead code trong `thyca/config/schema.py`: `_HINTS: dict = {}` (`config_schema.py:51`) và nhánh `if field.name in _HINTS` trong `_field_entry` — dict rỗng nên nhánh không bao giờ chạy, xóa không đổi behavior | | |
| TASK-004 | Trích helper `_migrate_pricing_to_models(config)` trong `thyca/config/parsing.py` từ khối legacy migration (`parsing.py:238-248`, `if config.pricing and not config.models: replace(...)`); `_parse_dict` (`parsing.py:206`) chỉ còn orchestrate 6 section parsers + gọi helper — không đổi logic, không tách file | | |

### GOAL-003: Verify + bàn giao

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Chạy focused gate: `uv run pytest tests/test_config.py tests/test_serve_config.py tests/test_onboarding.py tests/test_cli.py tests/test_serve_errors.py -q` xanh; `python -c "import thyca.config.schema, thyca.serve, thyca.chat_app, thyca.bridge"` không circular import; `git diff --check` sạch; round-trip `default_config().to_dict()` → `_parse_dict` → `to_dict()` byte-identical trước/sau move | | |

## Test Plan

- Focused gate (module + consumers trực tiếp):
  `tests/test_config.py` (657 dòng — entities, parsing, store, auth split,
  guide 0600, lock), `tests/test_serve_config.py` (614 dòng — schema khớp
  Config, mask apiKey, POST save/keep-key, invalid limit 422),
  `tests/test_onboarding.py`, `tests/test_cli.py`, `tests/test_serve_errors.py`
  (config error message không lộ path/secret).
- Full suite `uv run pytest -q` phải xanh bằng hoặc hơn baseline; failure đã
  biết `tests/test_cli.py::test_debug_prints_prompt_flags` (`tools=7` vs thực
  tế) giữ nguyên — tuyệt đối không sửa số này (AGENT_RULES).
- Không sửa test để pass; contract đổi duy nhất được phép là import path của
  `config_schema` (đã cập nhật ở TASK-002).

## Assumptions

1. Không file oversize: file lớn nhất `parsing.py` 248 dòng, `store.py`
   156 dòng, `root.py` 115 dòng — dưới ngưỡng 400 của plan tổng nên không
   tách file, chỉ micro-cleanup TASK-003/004.
2. `thyca/config/compat.py` (shims `thyca_dir`, `default_dict`, `_lock_path`
   deprecated) giữ nguyên — xóa shim là behavior change với external
   importers, ngoài scope; orchestrator quyết ở phase docs nếu muốn gỡ.
3. Lazy import `from thyca.config import _parse_dict` trong
   `thyca/serve.py:107` giữ nguyên — đó là chi tiết nội bộ của serve (M7),
   M3 chỉ đảm bảo `_parse_dict` vẫn re-export từ `thyca.config`.
4. Config là leaf module (chỉ import stdlib + `filelock`, không import bất kỳ
   `thyca.*` nào khác) nên move này không thể gây cycle mới; rủi ro duy nhất
   là self-cycle `__init__` ↔ `schema` nếu giữ `from thyca.config import`
   tuyệt đối — đã chặn bằng relative sibling imports ở TASK-001.

## SOLID findings (evidence)

- **SRP — package đã đạt, giữ nguyên.** Mỗi entity một file (`providers`,
  `mcp`, `pricing`, `models`, `timeline`, `limits`), `root.py:14-22` là
  `Config` aggregate, `parsing.py` parse+migration, `store.py` file I/O,
  `auth.py` secrets `auth.json`. Không gộp/tách thêm.
- **SRP — `_HINTS` dead trong `config_schema.py:51`.** `dict` rỗng + nhánh
  `if field.name in _HINTS` trong `_field_entry` (`config_schema.py:100-101`)
  không bao giờ chạy → xóa (TASK-003).
- **SRP — `_parse_dict` (`parsing.py:206`) kiêm migration
  (`parsing.py:238-248`).** 6 section parsers + khối pricing→models
  `replace(...)` trong cùng hàm; trích helper, không tách file vì tổng mới
  248 dòng (TASK-004).
- **SRP — `root.py` kiêm wire form (`to_dict`, `root.py:36`) + resolution
  policy (`effective_provider_for`/`effective_limits`, `root.py:58-130`).**
  Đây là aggregate root có chủ ý (mọi resolution đọc cùng `providers` +
  `models`), tách ra sẽ làm implementation team phải truyền cả hai dict
  qua lại — giữ nguyên, ghi nhận để không ai "sửa giúp".
- **DIP — không thêm abstraction.** Mọi consumer (`chat_app.py:21`,
  `bridge.py:25`, `cli.py:19`, `onboarding.py:16`, `session_wire.py:17`,
  `llm/*`, `sessions/manager.py:10`, `memory/active.py:12`,
  `memory/archived.py:12`, `tools/mcp.py:16`) import trực tiếp frozen
  dataclasses — đúng vì config là value objects ổn định, leaf, không có
  chiều phụ thuộc ngược để đảo. Không `Protocol`, không factory mới.
- **ISP/OCP — không có evidence vi phạm.** Không fat interface (mỗi consumer
  chỉ import 1-3 names mình cần), mở rộng bằng field optional additive
  (`ModelCfg` sparse wire form `_model_to_dict`, `models.py:81`) — không can
  thiệp.

## Cycle / import risks

| Rủi ro | Evidence | Cách tránh |
|--------|----------|------------|
| Self-cycle `config/__init__` ↔ `schema` | `config_schema.py:14` dùng `from thyca.config import ...` (qua package root); nếu `__init__` thêm `from .schema import config_schema` sẽ thành vòng tròn | TASK-001 đổi sang relative sibling imports; giữ import `.schema` sau entity imports trong `__init__` |
| Schema import cost cho mọi consumer config | `sessions/manager.py:10`, `memory/*`, `llm/*` chỉ cần 1-2 constants nhưng `import thyca.config` chạy cả `__init__` | `schema.py` nhẹ (stdlib `dataclasses` + 4 sibling leaf), không import `store`/`auth`/`filelock` — re-export ở `__init__` không tăng chi phí đáng kể; không lazy-import |
| Tranh file với M7-serve | `thyca/serve.py:28-29,107,327` là consumer duy nhất ngoài tests (`tests/test_serve_config.py:15,62,78`) | M3 chỉ sửa 2 dòng import của serve (TASK-002), không đụng logic `_parse_config_payload`/`_merge_saved_key` — thuộc M7 |
| `filelock` dependency | `store.py:12-13` import trực tiếp `filelock` | Giữ nguyên (đã là dependency project), không wrap abstraction mới |

## Success criteria (đo được)

1. `git status` cho thấy đúng 1 rename `thyca/config_schema.py` →
   `thyca/config/schema.py` + sửa import tại `thyca/config/__init__.py`,
   `thyca/serve.py`, `tests/test_serve_config.py`; không còn file
   `thyca/config_schema.py`; `thyca/` top-level không thêm file lẻ.
2. `from thyca.config import config_schema` hoạt động;
   `from thyca.config_schema import ...` cũ không còn (không shim, đã thống
   nhất với orchestrator).
3. Focused gate TASK-005 xanh 100% (trừ baseline `tools=7` đã biết nếu nó nằm
   trong `test_cli.py` — vẫn fail y như baseline, không hơn không kém).
4. `python -c "import thyca.config.schema"` và import chéo các consumer
   (`serve`, `chat_app`, `bridge`) không `ImportError`/circular.
5. `git diff --check` sạch; diff ngoài 4 nhóm file ở tiêu chí 1 + 2
   micro-cleanup = 0 (không broad rewrite).

## Close-out (2026-09-22, orchestrator)
All module tasks landed and verified: branch refactor/backend-solid-M3-config commit c725758, test 719/719, review approve. Merged into refactor/backend-solid, full suite 719 passed, plan status done.
