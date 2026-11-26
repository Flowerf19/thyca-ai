"""Live Thyca harness: real provider turns against an isolated tmp root.

Seed → ``ChatApp.turn`` → assert tool calls + 3-ledger disk state.
Never writes into ``~/.thyca`` runtime paths; only reads ``config.json``.

Run::

    THYCA_LIVE=1 .venv/bin/python -m tests.live.memory_agent
    THYCA_LIVE=1 .venv/bin/pytest -q -m live tests/test_live_memory_agent.py
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tests.live.behavior_scenarios import (
    NATURAL_SCENARIOS,
    SAFETY_SCENARIOS,
    LiveHarnessOps,
    build_behavior_runners,
)
from tests.live.edge_scenarios import (
    EDGE_SCENARIOS,
    build_edge_runners,
)
from thyca.app.chat_app import ChatApp
from thyca.bots.card import CARD_FILENAME
from thyca.bots.registry import SYSTEM_BOT_ID
from thyca.configs import Config, load, save
from thyca.llms.prompt_manager import PromptManager
from thyca.memories.identity import bind_bot_id, reset_bot_id

LIVE_ENV = "THYCA_LIVE"
_MEMORY_ALLOWLIST = (
    "memory_remember",
    "memory_search",
    "memory_recent",
    "memory_get",
    "memory_tasks",
    "memory_update",
    "memory_forget",
    "memory_reinforce",
    "compact",
    "read",
    "edit",
    "ask_user",
    "message_user",
)


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    result: str
    is_error: bool = False
    round: int = 0


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    reply: str = ""
    today_md: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    suite: str = ""


def live_enabled() -> bool:
    return os.environ.get(LIVE_ENV, "").strip() in {"1", "true", "yes", "on"}


def live_config_path() -> Path:
    override = os.environ.get("THYCA_LIVE_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".thyca" / "config.json"


def live_config() -> Config:
    """Load provider config from the user's Thyca install (read-only source)."""
    path = live_config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"live config missing at {path}; set THYCA_LIVE_CONFIG or configure ~/.thyca"
        )
    return replace(load(path), mcpServers={})


def _seed_system_card(root: Path) -> None:
    """Restrict the system bot to memory tools so live runs stay on-ledger."""
    packaged = (
        Path(__file__).resolve().parents[2]
        / "thyca"
        / "seeds"
        / "bots"
        / SYSTEM_BOT_ID
        / CARD_FILENAME
    )
    text = packaged.read_text(encoding="utf-8")
    if "bash: ask\n" not in text or "system: true\n" not in text:
        raise RuntimeError("packaged Thyca card missing expected frontmatter")
    text = text.replace("bash: ask\n", "bash: deny\n", 1)
    tools_block = "tools:\n" + "".join(f"  - {name}\n" for name in _MEMORY_ALLOWLIST)
    text = text.replace("system: true\n", f"system: true\n{tools_block}", 1)
    dest_dir = root / "bots" / SYSTEM_BOT_ID
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / CARD_FILENAME).write_text(text, encoding="utf-8")
    (dest_dir / CARD_FILENAME).chmod(0o600)


def bootstrap_live_root(root: Path, cfg: Config) -> Config:
    """Write isolated root files: config, system card, real SOUL template, minimal USER."""
    root.mkdir(parents=True, exist_ok=True)
    save(cfg, root / "config.json")
    loaded = load(root / "config.json")
    (root / "SOUL.md").write_text(
        PromptManager().template("soul"),
        encoding="utf-8",
    )
    (root / "USER.md").write_text("# User\nLive memory harness user.\n", encoding="utf-8")
    (root / "memory").mkdir(parents=True, exist_ok=True)
    _seed_system_card(root)
    return loaded


def make_live_app(root: Path, cfg: Config | None = None) -> ChatApp:
    """Build ``ChatApp`` on ``root`` using live provider credentials."""
    cfg = bootstrap_live_root(root, cfg or live_config())
    return ChatApp(root, cfg)


def day_strings(cfg: Config) -> tuple[str, str, ZoneInfo]:
    tz = ZoneInfo(cfg.timeline.timezone)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return today, yesterday, tz


def seed_yesterday_search_fixture(root: Path, yesterday: str) -> None:
    """Two closed-day leaves: Bazzite heading + Đà Nẵng trip."""
    path = root / "memory" / f"{yesterday}.md"
    path.write_text(
        f"# {yesterday}\n"
        f'## 09:00 — BazziteSmokeHeading '
        f'<!-- thyca {{"id":"smoke001","imp":4,"chat":"thyca","bots":["thyca"]}} -->\n'
        "- Dự án đang chạy kiểm thử bộ nhớ phiên bản mới trên Fedora Bazzite.\n"
        f'## 10:00 — Lịch cuối tuần '
        f'<!-- thyca {{"id":"smoke002","imp":4,"chat":"thyca","bots":["thyca"]}} -->\n'
        "- Cuối tuần đi Đà Nẵng cùng team.\n",
        encoding="utf-8",
    )


def collect_tool_calls(app: ChatApp, bot_id: str, dm_id: str) -> list[ToolCallRecord]:
    """Read tool calls from the bot's private internal transcript."""
    binding = app._runtime.workspaces.store.get_binding(bot_id, dm_id)
    if binding is None or not binding.internal_conversation_id:
        return []
    room_conv = app._conversations.store.load(dm_id)
    window = app._webui.window_for(room_conv)
    internal = app._runtime._canonical_store(
        bot_id, window, binding.internal_conversation_id
    ).load(binding.internal_conversation_id)
    results: dict[str, str] = {}
    tool_meta: dict[str, dict[str, Any]] = {}
    ordered: list[tuple[str, dict[str, Any]]] = []
    for msg in internal.messages:
        if msg.role == "tool" and getattr(msg, "tool_call_id", None):
            cid = str(msg.tool_call_id)
            results[cid] = str(msg.content or "")
            tool_meta[cid] = dict(getattr(msg, "meta", None) or {})
        for tc in getattr(msg, "tool_calls", None) or []:
            args = getattr(tc, "arguments", {}) or {}
            if not isinstance(args, dict):
                args = {}
            ordered.append((str(tc.id), {"name": str(tc.name), "args": dict(args)}))
    return [
        ToolCallRecord(
            name=str(item["name"]),
            args=dict(item["args"]),
            result=results.get(cid, ""),
            is_error=bool(tool_meta.get(cid, {}).get("is_error", False)),
            round=int(tool_meta.get(cid, {}).get("round") or 0),
        )
        for cid, item in ordered
    ]


def find_tools(
    calls: list[ToolCallRecord],
    name: str,
    *,
    pred: Callable[[dict[str, Any]], bool] | None = None,
) -> list[ToolCallRecord]:
    out = [c for c in calls if c.name == name]
    if pred is not None:
        out = [c for c in out if pred(c.args)]
    return out


def _today_text(root: Path, today: str) -> str:
    path = root / "memory" / f"{today}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _run_turn(
    app: ChatApp, dm_id: str, prompt: str
) -> tuple[str, list[ToolCallRecord]]:
    payload = app.turn(dm_id, prompt)
    reply = str(payload.get("reply") or "")
    calls = collect_tool_calls(app, SYSTEM_BOT_ID, dm_id)
    return reply, calls


def scenario_write_other(app: ChatApp, root: Path, today: str) -> ScenarioResult:
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Chỉ ghi nhớ một fact vào nhật ký Other (không mở task, không habit): "
        "Máy phát triển chạy Fedora Bazzite LiveHarnessOS. "
        "Dùng memory_remember, không sửa file hồ sơ."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    today_md = _today_text(root, today)
    reasons: list[str] = []
    remembers = find_tools(calls, "memory_remember")
    if not remembers:
        reasons.append("expected memory_remember for other-journal fact")
    else:
        bad = [
            c
            for c in remembers
            if c.args.get("kind") or c.args.get("task") or c.args.get("new_task")
        ]
        if bad:
            reasons.append(
                "other fact used kind/task/new_task: "
                + json.dumps([c.args for c in bad], ensure_ascii=False)
            )
    if "LiveHarnessOS" not in today_md and "Bazzite" not in today_md:
        reasons.append("today.md missing expected OS fact tokens")
    return ScenarioResult(
        name="write_other",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=today_md,
    )


def scenario_write_habit(app: ChatApp, root: Path, today: str) -> ScenarioResult:
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Ghi một quan sát habit lặp lại bằng memory_remember với kind='habit': "
        "mình uống cà phê đen mỗi sáng (label ngắn hợp lệ). Không mở task."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    today_md = _today_text(root, today)
    reasons: list[str] = []
    habits = find_tools(
        calls,
        "memory_remember",
        pred=lambda a: a.get("kind") == "habit",
    )
    if not habits:
        reasons.append("expected memory_remember with kind=habit")
    else:
        if any(not str(c.args.get("habit") or "").strip() for c in habits):
            reasons.append("habit remember missing non-empty habit label")
    if (
        '"kind": "habit"' not in today_md
        and "kind\":\"habit\"" not in today_md
        and "habit" not in today_md.lower()
    ):
        reasons.append("today.md missing habit marker")
    return ScenarioResult(
        name="write_habit",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=today_md,
    )


def scenario_write_task(app: ChatApp, root: Path, today: str) -> ScenarioResult:
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Mở một task mới tên gần với 'LiveHarnessTask' để theo dõi kiểm thử bộ nhớ. "
        "Dùng memory_remember với new_task=true (hoặc task id mới). "
        "Ghi checkpoint trạng thái hiện tại."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    today_md = _today_text(root, today)
    reasons: list[str] = []
    task_writes = find_tools(
        calls,
        "memory_remember",
        pred=lambda a: bool(a.get("new_task")) or bool(a.get("task")),
    )
    if not task_writes:
        reasons.append("expected memory_remember with new_task or task")
    tok = bind_bot_id(SYSTEM_BOT_ID)
    try:
        tasks = app._memory_facade.tasks()
    finally:
        reset_bot_id(tok)
    open_rows = tasks if isinstance(tasks, list) else tasks.get("tasks", tasks)
    if not open_rows:
        reasons.append("MemoryFacade.tasks() returned no open tasks")
    if "task-" not in today_md and "checkpoint" not in today_md:
        reasons.append("today.md missing task/checkpoint markers")
    return ScenarioResult(
        name="write_task",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=today_md,
        details={"tasks": open_rows},
    )


def scenario_search_seeded(
    app: ChatApp, root: Path, today: str, yesterday: str
) -> ScenarioResult:
    seed_yesterday_search_fixture(root, yesterday)
    app._memory_facade.archive.reindex()
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Tìm trong trí nhớ đã lưu (không đoán): "
        "(1) heading BazziteSmokeHeading nói gì? "
        "(2) lịch Đà Nẵng / Da Nang cuối tuần? "
        "Dùng memory_search với query phù hợp."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    today_md = _today_text(root, today)
    reasons: list[str] = []
    searches = find_tools(calls, "memory_search")
    if not searches:
        reasons.append("expected memory_search against seeded archive")
    blob = " ".join([c.result for c in searches] + [c.args.get("query", "") for c in searches] + [reply])
    gold_ok = any(
        token in blob for token in ("Bazzite", "Đà Nẵng", "Da Nang", "Nang", "SmokeHeading")
    )
    index_hits = app._memory_facade.search("BazziteSmokeHeading", limit=5)
    if searches and not gold_ok:
        if index_hits.hits:
            qblob = " ".join(str(c.args.get("query") or "") for c in searches)
            if not any(tok.lower() in qblob.lower() for tok in ("bazzite", "nang", "đà", "da nang", "smoke")):
                reasons.append("memory_search queries miss seeded topics")
        else:
            reasons.append("seeded archive not searchable after reindex")
    return ScenarioResult(
        name="search_seeded",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=today_md,
        details={"yesterday": yesterday},
    )


def scenario_call_must_remember(app: ChatApp, root: Path, today: str) -> ScenarioResult:
    """Must call memory_remember when asked to persist a fact."""
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Hãy lưu vào trí nhớ (memory_remember) fact sau rồi xác nhận ngắn: "
        "Repo LiveCallToolAlpha dùng Python 3.14. "
        "Không cần tìm kiếm trước."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    reasons: list[str] = []
    if not find_tools(calls, "memory_remember"):
        reasons.append("must call memory_remember to persist the fact")
    names = [c.name for c in calls]
    return ScenarioResult(
        name="call_must_remember",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=_today_text(root, today),
        details={"expected": ["memory_remember"], "forbidden": [], "tools": names},
    )


def scenario_call_must_search(
    app: ChatApp, root: Path, today: str, yesterday: str
) -> ScenarioResult:
    """Must call memory_search when asked about seeded archive (not invent)."""
    seed_yesterday_search_fixture(root, yesterday)
    app._memory_facade.archive.reindex()
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Trong trí nhớ đã lưu hôm trước có heading BazziteSmokeHeading. "
        "Nội dung là gì? Bắt buộc dùng memory_search (không đoán)."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    reasons: list[str] = []
    if not find_tools(calls, "memory_search"):
        reasons.append("must call memory_search for seeded recall")
    if find_tools(calls, "memory_remember"):
        reasons.append("must not memory_remember when only asked to recall")
    return ScenarioResult(
        name="call_must_search",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=_today_text(root, today),
        details={
            "expected": ["memory_search"],
            "forbidden": ["memory_remember"],
            "tools": [c.name for c in calls],
        },
    )


def scenario_call_no_remember_chitchat(
    app: ChatApp, root: Path, today: str
) -> ScenarioResult:
    """Must not persist memory on a pure chitchat / arithmetic question."""
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Chỉ trả lời số: 7 nhân 8 bằng bao nhiêu? "
        "Không ghi nhớ, không mở task, không search trí nhớ."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    names = [c.name for c in calls]
    reasons = [
        f"must not call {forbidden} on chitchat/math"
        for forbidden in ("memory_remember", "memory_search", "memory_tasks", "memory_update")
        if forbidden in names
    ]
    return ScenarioResult(
        name="call_no_remember_chitchat",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=_today_text(root, today),
        details={"expected": [], "forbidden": ["memory_remember", "memory_search"], "tools": names},
    )


def scenario_call_must_tasks(app: ChatApp, root: Path, today: str) -> ScenarioResult:
    """Must call memory_tasks (or search) when asked for open task catalog."""
    dm_id = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
    prompt = (
        "Liệt kê các task đang open trong catalog trí nhớ. "
        "Dùng memory_tasks (ưu tiên) — không bịa danh sách."
    )
    reply, calls = _run_turn(app, dm_id, prompt)
    reasons: list[str] = []
    names = [c.name for c in calls]
    if "memory_tasks" not in names and "memory_search" not in names:
        reasons.append("must call memory_tasks (or memory_search) for open-task listing")
    return ScenarioResult(
        name="call_must_tasks",
        passed=not reasons,
        reasons=reasons,
        tool_calls=calls,
        reply=reply,
        today_md=_today_text(root, today),
        details={
            "expected_any": ["memory_tasks", "memory_search"],
            "tools": names,
        },
    )


WRITE_SCENARIOS: tuple[str, ...] = (
    "write_other",
    "write_habit",
    "write_task",
    "search_seeded",
)
TOOL_CALL_SCENARIOS: tuple[str, ...] = (
    "call_must_remember",
    "call_must_search",
    "call_no_remember_chitchat",
    "call_must_tasks",
)
SCENARIOS: tuple[str, ...] = (
    WRITE_SCENARIOS
    + TOOL_CALL_SCENARIOS
    + NATURAL_SCENARIOS
    + SAFETY_SCENARIOS
    + EDGE_SCENARIOS
)


def _suite_for_scenario(name: str) -> str:
    if name in WRITE_SCENARIOS:
        return "write"
    if name in TOOL_CALL_SCENARIOS:
        return "tool_call"
    if name in NATURAL_SCENARIOS:
        return "natural"
    if name in SAFETY_SCENARIOS:
        return "safety"
    if name in EDGE_SCENARIOS:
        return "edge"
    return "unknown"


def run_scenario(name: str, *, keep_dir: Path | None = None) -> ScenarioResult:
    """Run one live scenario in a fresh tmp root (or ``keep_dir``)."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
    cfg = live_config()
    owns_tmp = keep_dir is None
    fake_home = keep_dir or Path(tempfile.mkdtemp(prefix=f"thyca_live_{name}_"))
    root = fake_home / ".thyca"
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    app: ChatApp | None = None
    t0 = time.perf_counter()
    try:
        app = make_live_app(root, cfg)
        today, yesterday, _tz = day_strings(cfg)
        ops = LiveHarnessOps(
            run_turn=_run_turn,
            collect_tool_calls=collect_tool_calls,
            find_tools=find_tools,
            today_text=_today_text,
            seed_yesterday_search_fixture=seed_yesterday_search_fixture,
            make_result=ScenarioResult,
        )
        runners: dict[str, Callable[[], ScenarioResult]] = {
            "write_other": lambda: scenario_write_other(app, root, today),
            "write_habit": lambda: scenario_write_habit(app, root, today),
            "write_task": lambda: scenario_write_task(app, root, today),
            "search_seeded": lambda: scenario_search_seeded(app, root, today, yesterday),
            "call_must_remember": lambda: scenario_call_must_remember(app, root, today),
            "call_must_search": lambda: scenario_call_must_search(
                app, root, today, yesterday
            ),
            "call_no_remember_chitchat": lambda: scenario_call_no_remember_chitchat(
                app, root, today
            ),
            "call_must_tasks": lambda: scenario_call_must_tasks(app, root, today),
            **build_behavior_runners(app, root, today, yesterday, ops),
            **build_edge_runners(app, root, today, ops),
        }
        result = runners[name]()
        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        if not result.suite:
            result.suite = _suite_for_scenario(name)
        return result
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if app is not None:
            app.shutdown()
        if owns_tmp:
            shutil.rmtree(fake_home, ignore_errors=True)


def run_all(
    *, keep_dir: Path | None = None, suite: str = "all"
) -> list[ScenarioResult]:
    """Run scenarios in ``suite``: all | write | tool_call | natural | safety | edge."""
    suite_map: dict[str, tuple[str, ...]] = {
        "write": WRITE_SCENARIOS,
        "tool_call": TOOL_CALL_SCENARIOS,
        "natural": NATURAL_SCENARIOS,
        "safety": SAFETY_SCENARIOS,
        "edge": EDGE_SCENARIOS,
        "all": SCENARIOS,
    }
    names = suite_map.get(suite, SCENARIOS)
    results: list[ScenarioResult] = []
    for name in names:
        case_dir = None if keep_dir is None else keep_dir / name
        if case_dir is not None:
            case_dir.mkdir(parents=True, exist_ok=True)
        results.append(run_scenario(name, keep_dir=case_dir))
    return results


def _clip(text: str, limit: int = 160) -> str:
    clean = text.replace("\n", " ").strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "…"


def format_report(results: list[ScenarioResult], *, verbose: bool = False) -> str:
    lines = ["## Live Thyca memory-agent scenarios", ""]
    passed = sum(1 for r in results if r.passed)
    total_err = sum(1 for r in results for c in r.tool_calls if c.is_error)
    lines.append(f"**{passed}/{len(results)}** passed · tool errors: {total_err}")
    lines.append("")
    lines.append("| Scenario | Suite | Result | Errors | Time | Tools called |")
    lines.append("|----------|-------|--------|--------|------|--------------|")
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        suite = r.suite or _suite_for_scenario(r.name)
        err_count = sum(1 for c in r.tool_calls if c.is_error)
        tools = [f"{c.name}!" if c.is_error else c.name for c in r.tool_calls]
        dur = f"{r.duration_ms}ms" if r.duration_ms else "—"
        lines.append(
            f"| `{r.name}` | {suite} | {mark} | {err_count} | {dur} | {tools or '—'} |"
        )
    lines.append("")
    for r in results:
        if r.passed and not verbose:
            continue
        lines.append(f"### `{r.name}` details")
        lines.extend(f"- reason: {reason}" for reason in r.reasons)
        for idx, c in enumerate(r.tool_calls, start=1):
            err_tag = " [ERROR]" if c.is_error else ""
            args_s = _clip(json.dumps(c.args, ensure_ascii=False), 180)
            res_s = _clip(c.result, 160)
            lines.append(f"- call#{idx} (r{c.round}) `{c.name}`{err_tag} args={args_s} -> {res_s}")
        if r.reply:
            lines.append(f"- reply: {_clip(r.reply, 240)}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:

    parser = argparse.ArgumentParser(description="Live Thyca memory-agent scenarios")
    parser.add_argument(
        "--suite",
        choices=("all", "write", "tool_call", "natural", "safety", "edge"),
        default="all",
        help="write=ledger writes; tool_call=explicit tool prompts; natural=no tool hints; safety=guardrails; edge=lifecycle & failure cases",
    )
    parser.add_argument(
        "--scenario",
        choices=("all", *SCENARIOS),
        default="all",
    )
    parser.add_argument("--keep-dir", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not live_enabled():
        print(f"Set {LIVE_ENV}=1 to run live scenarios against your configured provider.")
        return 2
    try:
        live_config()
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    if args.scenario != "all":
        results = [run_scenario(args.scenario, keep_dir=args.keep_dir)]
    else:
        results = run_all(keep_dir=args.keep_dir, suite=args.suite)
    print(format_report(results, verbose=args.verbose))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
