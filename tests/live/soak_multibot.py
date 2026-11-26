"""Multi-bot + group live lifecycle soak & interactive inspector.

Runs 3 real bots (thyca, march7th, searcher) across private DMs and a shared
group room with ~45 seeded multi-day entries, real idle sweeps, compaction,
drop_prior, and post-compact recall. Never touches ~/.thyca runtime data.

Run automated 5-phase soak + report::

    THYCA_LIVE=1 .venv/bin/python -m tests.live.soak_multibot --report /tmp/soak_report.md

Run interactive sandbox chat::

    THYCA_LIVE=1 .venv/bin/python -m tests.live.soak_multibot --interactive
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from tests.live.memory_agent import (
    bootstrap_live_root,
    collect_tool_calls,
    day_strings,
    live_config,
    live_enabled,
)
from thyca.app.chat_app import ChatApp
from thyca.bots.card import BotCard, BotMemoryPolicy
from thyca.bots.registry import SYSTEM_BOT_ID
from thyca.conversations.runtime import ConversationManager
from thyca.conversations.runtime.compaction import PRIOR_CLEARED_MARKER, context_usage
from thyca.core.protocol import Message
from thyca.memories import jobs as mem_jobs
from thyca.memories.heading import HeadingMeta, expiry_ts, parse_heading, render_heading

_SAFE_CHILD_TOOLS: tuple[str, ...] = (
    "memory_search",
    "memory_get",
    "memory_note",
    "memory_task",
    "memory_forget",
    "compact",
    "message_user",
    "ask_user",
)


@dataclass
class TurnTrace:
    step: int
    phase: str
    room_label: str
    room_id: str
    bot_hint: str
    user_text: str
    reply: str
    duration_ms: int
    new_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def _tag_extract(system_text: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", system_text, re.DOTALL)
    return match.group(1).strip() if match else "(absent)"


def _seed_child_bots(app: ChatApp) -> None:
    app._bots.create(
        BotCard(
            bot_id="march7th",
            name="Bé Bảy",
            role="Trợ lý tổng hợp bản tin & lịch nhóm",
            description="Tổng hợp bản tin cuối tuần, giữ quy chuẩn định dạng ngắn gọn.",
            persona=(
                "Bạn là Bé Bảy (March 7th), trợ lý vui vẻ, gọn gàng, nói tiếng Việt tự nhiên. "
                "Luôn tuân thủ quy chuẩn định dạng user đặt ra."
            ),
            tools=_SAFE_CHILD_TOOLS,
            bash="deny",
            memory=BotMemoryPolicy(read=True, write=True),
        )
    )
    app._bots.create(
        BotCard(
            bot_id="searcher",
            name="Searcher",
            role="Trợ lý tra cứu & đối chiếu tài liệu",
            description="Tra cứu thông tin kỹ thuật, tóm tắt gọn và kiểm tra lịch sử nhóm.",
            persona=(
                "Bạn là Searcher, chuyên tra cứu chính xác, trả lời súc tích bằng tiếng Việt, "
                "chỉ lưu những quyết định bền lâu, không lưu rác tin tức thời sự."
            ),
            tools=_SAFE_CHILD_TOOLS,
            bash="deny",
            memory=BotMemoryPolicy(read=True, write=True),
        )
    )


def _fmt_entry(
    *,
    hour: str,
    title: str,
    entry_id: str,
    body: list[str],
    imp: int = 3,
    moment: datetime,
    bots: tuple[str, ...] | None = None,
    chat: str | None = None,
    task: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    habit: str | None = None,
    habit_explicit: bool = False,
    expired: bool = False,
) -> str:
    exp = "2020-01-01T00:00:00Z" if expired else expiry_ts(imp, moment)
    meta = HeadingMeta(
        time=hour,
        title=title,
        entry_id=entry_id,
        importance=imp,
        expires_at=exp,
        bots=bots,
        chat=chat,
        task=task,
        kind=kind,
        status=status,
        habit=habit,
        habit_explicit=habit_explicit,
    )
    return render_heading(meta) + "\n".join(body) + "\n"


def _seed_multiday_history(
    root: Path,
    now: datetime,
    *,
    dm_thyca: str,
    dm_m7: str,
    dm_searcher: str,
    group_id: str,
) -> dict[str, Any]:
    mem_dir = root / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    empty_past_day = (now - timedelta(days=12)).strftime("%Y-%m-%d")
    (mem_dir / f"{empty_past_day}.md").write_text(f"# {empty_past_day}\n", encoding="utf-8")

    day_offsets = (10, 8, 5, 3, 1)
    total_entries = 0
    seeded_days: list[str] = []

    for offset in day_offsets:
        dt = now - timedelta(days=offset)
        day_str = dt.strftime("%Y-%m-%d")
        seeded_days.append(day_str)
        blocks: list[str] = [f"# {day_str}\n"]

        if offset == 10:
            blocks.append(
                _fmt_entry(
                    hour="09:00",
                    title="Dọn tủ cáp phòng lab",
                    entry_id="aa100001",
                    body=["- Đã đánh nhãn 12 dây LAN, còn phần switch tầng 2"],
                    imp=5,
                    moment=dt,
                    bots=("thyca",),
                    chat=dm_thyca,
                    task="task-d0100001",
                    kind="checkpoint",
                    status="open",
                )
            )
            blocks.append(
                _fmt_entry(
                    hour="10:15",
                    title="Ghi chú cũ hết hạn",
                    entry_id="aa100002",
                    body=["- Vé xem phim tuần trước đã hết hạn"],
                    imp=1,
                    moment=dt,
                    bots=("thyca",),
                    chat=dm_thyca,
                    expired=True,
                )
            )
            blocks.append(
                _fmt_entry(
                    hour="14:00",
                    title="Tối ưu ảnh cover blog",
                    entry_id="aa100003",
                    body=["- Chuyển toàn bộ ảnh PNG sang WebP cho blog cá nhân"],
                    imp=5,
                    moment=dt,
                    bots=("march7th",),
                    chat=dm_m7,
                    task="task-d0100002",
                    kind="checkpoint",
                    status="open",
                )
            )
        elif offset == 8:
            blocks.append(
                _fmt_entry(
                    hour="08:30",
                    title="Cấu hình WireGuard server nhà",
                    entry_id="aa080001",
                    body=[
                        "- Server nhà chạy WireGuard VPN tại cổng UDP 51827, subnet 10.77.0.0/24."
                    ],
                    imp=4,
                    moment=dt,
                    bots=("thyca",),
                    chat=dm_thyca,
                )
            )
            blocks.append(
                _fmt_entry(
                    hour="11:20",
                    title="Nâng cấp SSD NVMe",
                    entry_id="aa080002",
                    body=["- Đã lắp xong ổ Samsung 990 Pro 2TB và clone Fedora Bazzite thành công."],
                    imp=5,
                    moment=dt,
                    bots=("thyca",),
                    chat=dm_thyca,
                    task="task-d0080001",
                    kind="checkpoint",
                    status="done",
                )
            )
        elif offset == 5:
            blocks.append(
                _fmt_entry(
                    hour="15:10",
                    title="Chuẩn nén log cluster staging",
                    entry_id="aa050001",
                    body=[
                        "- Team chốt dùng chuẩn nén Zstandard (zstd level 6) cho log cluster staging thay vì gzip để giảm 45% dung lượng."
                    ],
                    imp=4,
                    moment=dt,
                    bots=("team",),
                    chat=group_id,
                )
            )
            blocks.append(
                _fmt_entry(
                    hour="16:00",
                    title="Bí mật riêng của Bé Bảy",
                    entry_id="aa050002",
                    body=["- Mã kiểm tra riêng tư của march7th là M7_PRIVATE_TOKEN_771."],
                    imp=4,
                    moment=dt,
                    bots=("march7th",),
                    chat=dm_m7,
                )
            )
        elif offset == 3:
            blocks.append(
                _fmt_entry(
                    hour="09:40",
                    title="Thói quen uống cà phê",
                    entry_id="aa030001",
                    body=["- User thích cà phê phin đen ít đá buổi sáng."],
                    imp=4,
                    moment=dt,
                    bots=("thyca",),
                    chat=dm_thyca,
                    kind="habit",
                    habit="uong ca phe phin den it da buoi sang",
                    habit_explicit=False,
                )
            )
        elif offset == 1:
            blocks.append(
                _fmt_entry(
                    hour="18:00",
                    title="Quy ước đặt tên nhánh git",
                    entry_id="aa010001",
                    body=["- Nhóm dùng tiền tố feat/, fix/, chore/ kèm mô tả ngắn không dấu."],
                    imp=4,
                    moment=dt,
                    bots=("team",),
                    chat=group_id,
                )
            )

        for idx in range(7):
            owner_bot, owner_chat = (
                ("thyca", dm_thyca),
                ("march7th", dm_m7),
                ("searcher", dm_searcher),
                ("team", group_id),
            )[(offset + idx) % 4]
            blocks.append(
                _fmt_entry(
                    hour=f"{10 + idx:02d}:{(offset * 3) % 60:02d}",
                    title=f"Ghi chép nền ngày -{offset} mục {idx + 1}",
                    entry_id=f"bb{offset:02d}00{idx:02d}",
                    body=[
                        f"- Ghi nhận vận hành định kỳ số {idx + 1} ngày {day_str} "
                        f"cho {owner_bot}: kiểm tra service, quota disk ổn định ở mức {40 + idx}%."
                    ],
                    imp=3,
                    moment=dt,
                    bots=(owner_bot,),
                    chat=owner_chat,
                )
            )

        total_entries += len(blocks) - 1
        (mem_dir / f"{day_str}.md").write_text("".join(blocks), encoding="utf-8")

    return {
        "empty_past_day": empty_past_day,
        "seeded_days": seeded_days,
        "total_seeded_entries": total_entries,
    }


def _seed_prior_dm_history(
    app: ChatApp,
    bot_id: str,
    dm_id: str,
    now: datetime,
    *,
    turns: int = 11,
) -> int:
    """Seed realistic earlier Q&A turns into a bot DM so compaction triggers naturally."""
    room_conv = app._conversations.store.load(dm_id)
    window = app._webui.window_for(room_conv)
    binding = app._runtime.workspaces.workspace(bot_id).bind_window(window)
    internal_id = binding.internal_conversation_id
    assert internal_id is not None
    manager = ConversationManager(
        limits=app._current_cfg().effective_limits(),
        timezone_name=app._current_cfg().timeline.timezone,
        store=app._runtime._canonical_store(bot_id, window, internal_id),
    )
    manager.load(internal_id)
    base_ts = now - timedelta(hours=2)
    for idx in range(turns):
        t_u = (base_ts + timedelta(minutes=idx * 4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        t_a = (base_ts + timedelta(minutes=idx * 4 + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        user_body = (
            f"[Phiên cũ Docker/Nginx #{idx + 1}] Kiểm tra cấu hình reverse proxy và container network đợt sáng: "
            + " ".join(
                f"upstream_node_{idx}_{k}=10.10.{idx}.{k}:8080 keepalive=64 proxy_read_timeout=90s "
                f"tls_cipher=TLS_AES_256_GCM_SHA384 worker_connections=4096"
                for k in range(1, 22)
            )
        )
        asst_body = (
            f"[Phân tích Docker/Nginx #{idx + 1}] Đã đối chiếu thông số pool kết nối và header forwarding: "
            + " ".join(
                f"check_{idx}_{k}=ok latency_p95={12 + k}ms err_rate=0.00% buffer_pool=stable"
                for k in range(1, 22)
            )
        )
        manager.append(Message(role="user", content=user_body, ts=t_u))
        manager.append(Message(role="assistant", content=asst_body, ts=t_a))
    cursor = mem_jobs.latest_extraction_cursor(list(manager.current.messages))
    app._runtime.workspaces.store.set_memory_cursor(bot_id, dm_id, cursor)
    return len(manager.current.messages)


def _workspace_snapshot(
    app: ChatApp,
    bot_id: str,
    room_id: str,
    context_tokens: int,
) -> dict[str, Any]:
    binding = app._runtime.workspaces.store.get_binding(bot_id, room_id)
    if binding is None or not binding.internal_conversation_id:
        return {"exists": False}
    room_conv = app._conversations.store.load(room_id)
    window = app._webui.window_for(room_conv)
    internal = app._runtime._canonical_store(
        bot_id, window, binding.internal_conversation_id
    ).load(binding.internal_conversation_id)
    msgs = list(internal.messages)
    usage = context_usage(msgs, context_tokens)
    markers = [
        str(m.content or "")
        for m in msgs
        if m.role == "system" and str(m.content or "").startswith("[compaction: ")
    ]
    return {
        "exists": True,
        "internal_id": binding.internal_conversation_id,
        "memory_cursor": binding.memory_cursor,
        "message_count": len(msgs),
        "estimated_tokens": usage["estimated_tokens"],
        "ratio": round(usage["ratio"], 3),
        "compaction_markers": markers,
        "roles": [m.role for m in msgs],
    }


def _read_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else "(file không tồn tại)"


def _run_monitored_turn(
    app: ChatApp,
    *,
    step: int,
    phase: str,
    room_label: str,
    room_id: str,
    bot_ids: tuple[str, ...],
    user_text: str,
) -> TurnTrace:
    before_counts = {b: len(collect_tool_calls(app, b, room_id)) for b in bot_ids}
    diagnostics: list[dict[str, Any]] = []
    t0 = time.monotonic()
    error = ""
    reply_text = ""
    try:
        res = app.turn(
            room_id,
            user_text,
            diagnostic_sink=lambda diag: diagnostics.append(dict(diag)),
        )
        reply_text = str(res.get("reply") or res.get("assistant") or "")
        if not reply_text:
            conv = app._conversations.store.load(room_id)
            assistant_msgs = [
                m.content
                for m in conv.messages
                if m.role == "assistant" and isinstance(m.content, str) and m.content.strip()
            ]
            reply_text = assistant_msgs[-1] if assistant_msgs else ""
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = int((time.monotonic() - t0) * 1000)

    new_calls: list[dict[str, Any]] = []
    for b in bot_ids:
        all_calls = collect_tool_calls(app, b, room_id)
        start_idx = before_counts[b] if len(all_calls) >= before_counts[b] else 0
        new_calls.extend(
            {
                "bot": b,
                "name": rec.name,
                "args": rec.args,
                "result": rec.result[:260],
                "is_error": rec.is_error,
            }
            for rec in all_calls[start_idx:]
        )

    return TurnTrace(
        step=step,
        phase=phase,
        room_label=room_label,
        room_id=room_id,
        bot_hint=",".join(bot_ids),
        user_text=user_text,
        reply=reply_text,
        duration_ms=elapsed,
        new_tool_calls=new_calls,
        diagnostics=diagnostics,
        error=error,
    )


def _evaluate_checks(
    *,
    root: Path,
    today: str,
    empty_past_day: str,
    traces: list[TurnTrace],
    snap_after_idle1: dict[str, Any],
    snap_after_idle2: dict[str, Any],
    snap_after_drop_prior: dict[str, Any],
) -> list[tuple[str, bool, str]]:
    today_path = root / "memory" / f"{today}.md"
    today_text = _read_if_exists(today_path)
    valid_bots = {"thyca", "march7th", "searcher", "team"}
    headings = [
        meta
        for line in today_text.splitlines()
        if (meta := parse_heading(line)) is not None
    ]
    bad_bots = [
        (m.title, m.bots)
        for m in headings
        if m.bots is not None and any(b not in valid_bots for b in m.bots)
    ]

    d1_ok = not bad_bots
    d1_note = "Tất cả bots đều hợp lệ" if d1_ok else f"Sai bots: {bad_bots}"

    cursors1 = snap_after_idle1.get("cursors", {})
    d2_ok = any(v is not None for v in cursors1.values())
    d2_note = f"Cursors sau Idle #1: {cursors1}"

    group_probe = next((t for t in traces if t.step == 7), None)
    privacy_ok = group_probe is not None and "VAULT-9942" not in group_probe.reply
    privacy_note = (
        "Group KHÔNG rò rỉ VAULT-9942"
        if privacy_ok
        else "CẢNH BÁO: Group trả lời chứa VAULT-9942!"
    )

    m7_user = _read_if_exists(root / "bots" / "march7th" / "USER.md")
    thyca_user = _read_if_exists(root / "bots" / "thyca" / "USER.md")
    shared_user = _read_if_exists(root / "USER.md")
    d4_ok = (
        "file không tồn tại" not in m7_user
        or "file không tồn tại" not in thyca_user
        or "## Profile" in shared_user
    )
    d4_note = (
        f"march7th USER.md={len(m7_user)}B, thyca USER.md={len(thyca_user)}B, "
        f"root USER.md has ## Profile={'## Profile' in shared_user}"
    )

    searcher_blocks = [
        (m.title, m.importance)
        for m in headings
        if m.bots and "searcher" in m.bots
    ]
    d5_ok = len(today_text) < 12000 and not any(
        "nén log" in title.casefold() or "staging" in title.casefold()
        for title, _imp in searcher_blocks
    )
    d5_note = f"today.md={len(today_text)}B, searcher blocks={searcher_blocks}"

    pg_task_blocks = [
        m
        for m in headings
        if m.task is not None and "postgres" in m.title.casefold()
    ]
    no_dup_closed_ok = len(pg_task_blocks) == 1 and pg_task_blocks[0].status == "done"
    no_dup_closed_note = f"postgres task blocks={[(m.task, m.status) for m in pg_task_blocks]}"

    d7_ok = not (root / "memory" / f"{empty_past_day}.md").exists()
    d7_note = (
        f"{empty_past_day}.md đã bị xóa sạch"
        if d7_ok
        else f"{empty_past_day}.md vẫn còn tồn tại"
    )

    tokens_before_dp = snap_after_idle2.get("dm_thyca", {}).get("estimated_tokens", 0)
    tokens_after_dp = snap_after_drop_prior.get("estimated_tokens", 0)
    markers_after_dp = snap_after_drop_prior.get("compaction_markers", [])
    drop_turn = next((t for t in traces if t.step == 12), None)
    called_dp = bool(
        drop_turn
        and any(
            c["name"] == "compact" and c["args"].get("drop_prior") is True
            for c in drop_turn.new_tool_calls
        )
    )
    cleared_prior = any(PRIOR_CLEARED_MARKER in m for m in markers_after_dp)
    shrank_sharply = tokens_before_dp > 10000 and tokens_after_dp < tokens_before_dp // 3
    d2b_ok = called_dp and cleared_prior and shrank_sharply
    d2b_note = (
        f"called_drop_prior={called_dp}, cleared={cleared_prior}, "
        f"tokens {tokens_before_dp} -> {tokens_after_dp}, markers={markers_after_dp}"
    )

    search_turn = next((t for t in traces if t.step == 8), None)
    search_ok = bool(
        search_turn
        and (
            "zstd" in search_turn.reply.casefold()
            or "zstandard" in search_turn.reply.casefold()
        )
        and "M7_PRIVATE_TOKEN_771" not in search_turn.reply
    )
    search_note = (
        "Searcher tìm trúng Zstandard (zstd) & không thấy token riêng của march7th"
        if search_ok
        else f"Reply searcher: {search_turn.reply[:140] if search_turn else 'none'}"
    )

    recall_turn = next((t for t in traces if t.step == 13), None)
    recall_ok = bool(recall_turn and "51827" in recall_turn.reply)
    recall_note = (
        "Nhớ đúng cổng WireGuard 51827 sau compact"
        if recall_ok
        else f"Reply recall: {recall_turn.reply[:140] if recall_turn else 'none'}"
    )

    return [
        ("D1 — Không bịa bots", d1_ok, d1_note),
        ("D2 — Extraction cursor lưu bền", d2_ok, d2_note),
        ("D2b — Compact thật + drop_prior=true", d2b_ok, d2b_note),
        ("D4 — Đường ống Habit → USER.md", d4_ok, d4_note),
        ("D5 — Không rác web-search & không ghi đúp tra cứu cũ", d5_ok, d5_note),
        ("D7 — Xóa file ngày quá khứ rỗng", d7_ok, d7_note),
        ("Sidecar — Không đẻ trùng task đã đóng", no_dup_closed_ok, no_dup_closed_note),
        ("Privacy — Group không lộ bí mật DM", privacy_ok, privacy_note),
        ("Archive — Searcher tra cứu 44 entries & cách ly bot", search_ok, search_note),
        ("Post-Compact — Nhớ chuẩn sau nén", recall_ok, recall_note),
    ]


def _render_report(
    *,
    model: str,
    context_tokens: int,
    root: Path,
    today: str,
    seed_info: dict[str, Any],
    traces: list[TurnTrace],
    idle1_report: dict[str, Any],
    idle2_report: dict[str, Any],
    snap_before_compact: dict[str, Any],
    snap_after_idle1: dict[str, Any],
    snap_after_idle2: dict[str, Any],
    snap_after_drop_prior: dict[str, Any],
    today_after_idle1: str,
    today_after_idle2: str,
    checks: list[tuple[str, bool, str]],
) -> str:
    lines: list[str] = [
        "# Báo cáo Live Soak — 3 Bot (`thyca`, `march7th`, `searcher`) + Group + Compact",
        "",
        f"- **Model:** `{model}` · **Context cap (ép test compact):** `{context_tokens}` tokens",
        f"- **Dữ liệu nền đã seed:** `{seed_info['total_seeded_entries']}` entries trên 5 ngày quá khứ ({', '.join(seed_info['seeded_days'])}) + 1 file rỗng `{seed_info['empty_past_day']}.md`",
        f"- **Thư mục sandbox:** `{root}` *(tách biệt hoàn toàn khỏi `~/.thyca`)*",
        "",
        "## 1. Bảng kiểm tự động",
        "",
        "| Tiêu chí | Kết quả | Chi tiết |",
        "|---|---|---|",
    ]
    for title, ok, detail in checks:
        icon = "PASS" if ok else "CHECK"
        safe_detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {title} | **{icon}** | {safe_detail} |")

    lines.extend(
        [
            "",
            "## 2. Diễn biến từng lượt (DM + Group + Tool calls)",
            "",
        ]
    )
    for tr in traces:
        lines.append(
            f"### Bước {tr.step} — [{tr.phase}] `{tr.room_label}` ({tr.duration_ms}ms)"
        )
        user_preview = tr.user_text if len(tr.user_text) <= 450 else tr.user_text[:450] + "…"
        lines.append(f"- **User:** {user_preview}")
        if tr.new_tool_calls:
            lines.append("- **Tool calls:**")
            for tc in tr.new_tool_calls:
                args_str = json.dumps(tc["args"], ensure_ascii=False)
                err_tag = " `[ERR]`" if tc["is_error"] else ""
                lines.append(
                    f"  - `{tc['bot']}` → `{tc['name']}({args_str})`{err_tag} ⇒ `{tc['result']}`"
                )
        else:
            lines.append("- **Tool calls:** *(không gọi tool)*")
        if tr.error:
            lines.append(f"- **Error:** `{tr.error}`")
        lines.append(f"- **Reply:** {tr.reply.strip() or '(trống)'}")
        lines.append("")

    lines.extend(
        [
            f"## 3. Ảnh chụp `memory/{today}.md` qua các mốc",
            "",
            "### 3a. Sau Idle Sweep #1",
            "```markdown",
            today_after_idle1.strip(),
            "```",
            "",
            "### 3b. Sau Idle Sweep #2 (kiểm tra D2 không trích lặp)",
            "```markdown",
            today_after_idle2.strip(),
            "```",
            "",
            "### 3c. Cuối phiên (sau toàn bộ 5 pha)",
            "```markdown",
            _read_if_exists(root / "memory" / f"{today}.md").strip(),
            "```",
            "",
            "## 4. Đường ống Habit → `bots/*/USER.md` → `USER.md`",
            "",
            "### `bots/thyca/USER.md`",
            "```markdown",
            _read_if_exists(root / "bots" / "thyca" / "USER.md").strip(),
            "```",
            "",
            "### `bots/march7th/USER.md`",
            "```markdown",
            _read_if_exists(root / "bots" / "march7th" / "USER.md").strip(),
            "```",
            "",
            "### `bots/searcher/USER.md`",
            "```markdown",
            _read_if_exists(root / "bots" / "searcher" / "USER.md").strip(),
            "```",
            "",
            "### `USER.md` (chung)",
            "```markdown",
            _read_if_exists(root / "USER.md").strip(),
            "```",
            "",
            "## 5. Ngữ cảnh nội bộ & Compact (`dm:thyca`)",
            "",
            f"- **Idle #1 report:** `{json.dumps(idle1_report, ensure_ascii=False)[:400]}`",
            f"- **Idle #2 report:** `{json.dumps(idle2_report, ensure_ascii=False)[:400]}`",
            f"- **Trước compact (sau bước 11):** messages=`{snap_before_compact.get('message_count')}`, "
            f"tokens≈`{snap_before_compact.get('estimated_tokens')}`, ratio=`{snap_before_compact.get('ratio')}`, "
            f"markers=`{len(snap_before_compact.get('compaction_markers', []))}`",
            f"- **Sau Idle #2 compact thật:** messages=`{snap_after_idle2.get('dm_thyca', {}).get('message_count')}`, "
            f"tokens≈`{snap_after_idle2.get('dm_thyca', {}).get('estimated_tokens')}`, "
            f"ratio=`{snap_after_idle2.get('dm_thyca', {}).get('ratio')}`, "
            f"markers=`{snap_after_idle2.get('dm_thyca', {}).get('compaction_markers', [])}`",
            f"- **Sau `compact(drop_prior=true)` (sau bước 12):** messages=`{snap_after_drop_prior.get('message_count')}`, "
            f"tokens≈`{snap_after_drop_prior.get('estimated_tokens')}`, ratio=`{snap_after_drop_prior.get('ratio')}`, "
            f"markers=`{snap_after_drop_prior.get('compaction_markers', [])}`",
            "",
        ]
    )

    final_dm = next((t for t in reversed(traces) if t.room_label == "dm:thyca" and t.diagnostics), None)
    final_grp = next((t for t in reversed(traces) if t.room_label == "group" and t.diagnostics), None)
    for label, tr in (("Lượt cuối `dm:thyca` (sau compact)", final_dm), ("Lượt cuối `group` (`@march7th`)", final_grp)):
        if tr is None or not tr.diagnostics:
            continue
        sys_prompt = str(tr.diagnostics[-1].get("system") or "")
        lines.extend(
            [
                f"### Ngữ cảnh thực tế LLM nhìn thấy — {label}",
                "",
                "**`<context-budget>`:**",
                "```text",
                _tag_extract(sys_prompt, "context-budget"),
                "```",
                "",
                "**`<memory_tasks>`:**",
                "```text",
                _tag_extract(sys_prompt, "memory_tasks"),
                "```",
                "",
                "**`<today>`:**",
                "```text",
                _tag_extract(sys_prompt, "today"),
                "```",
                "",
            ]
        )

    lines.extend(
        [
            "## 6. Extraction Cursors (`registry.json`)",
            "```json",
            json.dumps(
                {
                    "after_idle1": snap_after_idle1.get("cursors", {}),
                    "after_idle2": snap_after_idle2.get("cursors", {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def run_automated_soak(
    *,
    context_tokens: int = 32000,
    keep_dir: Path | None = None,
    report_path: Path | None = None,
) -> tuple[str, Path]:
    cfg_base = live_config()
    limits = replace(cfg_base.limits, contextTokens=context_tokens)
    cfg = replace(cfg_base, limits=limits)

    if keep_dir is not None:
        root = keep_dir.resolve()
        root.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        root = Path(tempfile.mkdtemp(prefix="thyca_soak_multibot_"))
        cleanup = False

    fake_home = root / "_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        loaded = bootstrap_live_root(root, cfg)
        app = ChatApp(root, loaded)
        try:
            _seed_child_bots(app)
            dm_thyca = app.bot_dm(SYSTEM_BOT_ID)["conversation_id"]
            dm_m7 = app.bot_dm("march7th")["conversation_id"]
            dm_searcher = app.bot_dm("searcher")["conversation_id"]
            group_id = app.create(
                [SYSTEM_BOT_ID, "march7th", "searcher"],
                title="Phòng họp team",
            )["conversation_id"]

            today, _yesterday, tz = day_strings(loaded)
            seed_info = _seed_multiday_history(
                root,
                datetime.now(tz),
                dm_thyca=dm_thyca,
                dm_m7=dm_m7,
                dm_searcher=dm_searcher,
                group_id=group_id,
            )
            app._background_jobs.job_facade()._refresh_index()
            _seed_prior_dm_history(app, "thyca", dm_thyca, datetime.now(tz), turns=12)

            traces: list[TurnTrace] = []
            step = 0

            def do_turn(phase: str, label: str, rid: str, bots: tuple[str, ...], text: str) -> TurnTrace:
                nonlocal step
                step += 1
                tr = _run_monitored_turn(
                    app,
                    step=step,
                    phase=phase,
                    room_label=label,
                    room_id=rid,
                    bot_ids=bots,
                    user_text=text,
                )
                traces.append(tr)
                print(
                    f"[step {step:02d}] {phase} | {label} | tools={[c['name'] for c in tr.new_tool_calls]} | {tr.duration_ms}ms",
                    flush=True,
                )
                return tr

            do_turn(
                "Pha 1: DM riêng",
                "dm:thyca",
                dm_thyca,
                ("thyca",),
                (
                    "Ghi nhớ giúp mình 3 việc nhé:\n"
                    "1. Quy chuẩn làm việc của mình: luôn trả lời ngắn gọn thẳng vào vấn đề, không rào đón.\n"
                    "2. Bí mật cá nhân giữ riêng trong DM này: mã két sắt dự phòng là VAULT-9942.\n"
                    "3. Mở giúp mình 1 task nhiều bước: 'Dựng pipeline backup Postgres lên S3' — "
                    "hiện đã chọn xong bucket s3://prod-pg-backup, bước tiếp theo là viết script pg_dump + mã hóa age."
                ),
            )

            do_turn(
                "Pha 1: DM riêng",
                "dm:march7th",
                dm_m7,
                ("march7th",),
                (
                    "Chào Bé Bảy, em giới thiệu nhanh xem em là ai và team mình có những bot nào nhé? "
                    "Nhân tiện nhớ quy chuẩn này cho anh: bản tin tối thứ Sáu luôn định dạng 4 dòng văn bản thuần "
                    "và mọi đường link URL luôn bọc trong dấu ngoặc nhọn <...>."
                ),
            )

            do_turn(
                "Pha 1: DM riêng",
                "dm:march7th",
                dm_m7,
                ("march7th",),
                (
                    "Ghi lại quy trình bản tin tối thứ Sáu bản nháp 1: "
                    "19h00 gom tin AI và Linux, 19h30 lọc top 4 tin, 20h00 gửi bản nháp cho anh duyệt."
                ),
            )

            do_turn(
                "Pha 1: DM riêng",
                "dm:march7th",
                dm_m7,
                ("march7th",),
                (
                    "À sửa lại quy trình bản tin tối thứ Sáu vừa ghi lúc nãy giúp anh nhé "
                    "(cập nhật đè bản vừa rồi cho gọn, đừng lưu 2 bản): "
                    "đổi lịch sang 20h30 gom tin và 21h00 gửi thẳng bản chính thức 4 dòng."
                ),
            )

            noisy_news = (
                "Đoạn tin thời sự & bảng xếp hạng model tuần này mình vừa copy về, em tóm tắt nhanh đúng 2 ý chính thôi "
                "(đây chỉ là tin đọc lướt, không cần lưu dài vào memory):\n"
                + "\n".join(
                    f"- [Tin #{i}] Bảng xếp hạng Arena tuần 47: Model-X-{i} đạt {1310 + i} Elo, giá API ${0.4 + i * 0.1:.2f}/1M token, "
                    f"thử nghiệm tốc độ {90 + i * 3} tok/s trên H200, biến động {i % 5}% so với tuần trước; "
                    "chi tiết benchmark gồm MMLU-Pro, LiveCodeBench, GPQA-Diamond và tốc độ TTFT trên 14 cụm GPU khu vực châu Á."
                    for i in range(1, 12)
                )
            )
            do_turn(
                "Pha 1: DM riêng",
                "dm:searcher",
                dm_searcher,
                ("searcher",),
                noisy_news,
            )

            do_turn(
                "Pha 2: Group",
                "group",
                group_id,
                ("march7th",),
                "@march7th Mở giúp nhóm 1 task: 'Chuẩn bị bản tin cuối tuần cho nhóm' — cần gom mục AI và Linux rồi chốt trước 21h thứ Sáu.",
            )

            do_turn(
                "Pha 2: Group",
                "group",
                group_id,
                ("thyca",),
                "@thyca Nhắc lại giúp mình mã két sắt dự phòng là gì nhé?",
            )

            do_turn(
                "Pha 2: Group",
                "group",
                group_id,
                ("searcher",),
                "@searcher Tra lại giúp mình hồi 5 hôm trước team quyết định dùng chuẩn nén log gì cho cluster staging? Và em có thấy ghi chú mã riêng tư nào của Bé Bảy không?",
            )

            print("[idle 1] running sweep_idle_conversations(idle_s=0)...", flush=True)
            idle1_report = app.sweep_idle_conversations(idle_s=0)
            today_after_idle1 = _read_if_exists(root / "memory" / f"{today}.md")
            snap_after_idle1 = {
                "cursors": {
                    f"{b}:{lbl}": (
                        app._runtime.workspaces.store.get_binding(b, rid).memory_cursor
                        if app._runtime.workspaces.store.get_binding(b, rid)
                        else None
                    )
                    for b, lbl, rid in (
                        ("thyca", "dm", dm_thyca),
                        ("march7th", "dm", dm_m7),
                        ("searcher", "dm", dm_searcher),
                        ("thyca", "group", group_id),
                        ("march7th", "group", group_id),
                        ("searcher", "group", group_id),
                    )
                }
            }

            do_turn(
                "Pha 4: Tiếp tục + Compact",
                "dm:thyca",
                dm_thyca,
                ("thyca",),
                (
                    "Cập nhật task backup Postgres lên S3: mình đã viết xong script pg_dump + mã hóa age, "
                    "chạy test restore bản backup lên staging thành công 100%, coi như hoàn thành rồi — đóng task đó giúp mình nhé."
                ),
            )

            long_log_a = (
                "Phân tích cực ngắn gọn (tối đa 3 gạch đầu dòng) bảng log tải cụm Postgres đợt sáng sau đây "
                "xem node nào đáng chú ý nhất:\n"
                + "\n".join(
                    f"- [AM #{i:02d}] node-pg-{i % 10:02d} CPU={40 + (i % 45)}% RAM={58 + (i % 30)}% "
                    f"iops={1200 + i * 45} wal_lag={12 + i * 2}ms conn={28 + i}/100 slow={i % 5}"
                    for i in range(1, 32)
                )
            )
            do_turn(
                "Pha 4: Tiếp tục + Compact",
                "dm:thyca",
                dm_thyca,
                ("thyca",),
                long_log_a,
            )

            long_log_b = (
                "Còn đây là bảng log đợt chiều cùng ngày, chốt cực ngắn trong 3 gạch đầu dòng xem tải chiều có giảm không:\n"
                + "\n".join(
                    f"- [PM #{i:02d}] node-pg-{i % 10:02d} CPU={30 + (i % 30)}% RAM={52 + (i % 20)}% "
                    f"iops={900 + i * 35} wal_lag={6 + i}ms conn={20 + i}/100 slow={i % 3}"
                    for i in range(1, 32)
                )
            )
            do_turn(
                "Pha 4: Tiếp tục + Compact",
                "dm:thyca",
                dm_thyca,
                ("thyca",),
                long_log_b,
            )

            snap_before_compact = _workspace_snapshot(app, "thyca", dm_thyca, context_tokens)

            print("[idle 2] running sweep_idle_conversations(idle_s=0)...", flush=True)
            idle2_report = app.sweep_idle_conversations(idle_s=0)
            today_after_idle2 = _read_if_exists(root / "memory" / f"{today}.md")
            snap_after_idle2 = {
                "dm_thyca": _workspace_snapshot(app, "thyca", dm_thyca, context_tokens),
                "cursors": {
                    f"{b}:{lbl}": (
                        app._runtime.workspaces.store.get_binding(b, rid).memory_cursor
                        if app._runtime.workspaces.store.get_binding(b, rid)
                        else None
                    )
                    for b, lbl, rid in (
                        ("thyca", "dm", dm_thyca),
                        ("march7th", "dm", dm_m7),
                        ("searcher", "dm", dm_searcher),
                        ("thyca", "group", group_id),
                        ("march7th", "group", group_id),
                        ("searcher", "group", group_id),
                    )
                }
            }

            do_turn(
                "Pha 4: Tiếp tục + Compact",
                "dm:thyca",
                dm_thyca,
                ("thyca",),
                (
                    "Xong hết việc hệ thống cũ rồi, giờ đổi hẳn sang chủ đề mới: cuối tháng mình định đi Đà Lạt 3 ngày 2 đêm. "
                    "Em gọi tool compact với drop_prior=true để xóa sạch tóm tắt ngữ cảnh cũ đi, rồi gợi ý ngắn 3 điểm đáng đi ở Đà Lạt nhé."
                ),
            )
            snap_after_drop_prior = _workspace_snapshot(app, "thyca", dm_thyca, context_tokens)

            do_turn(
                "Pha 5: Hỏi lại sau compact",
                "dm:thyca",
                dm_thyca,
                ("thyca",),
                "Tiện hỏi lại 2 việc cũ sau khi đã nén ngữ cảnh: (1) cổng WireGuard VPN của server nhà mình ghi chú hồi tuần trước là bao nhiêu? (2) Task backup Postgres lên S3 trạng thái giờ thế nào rồi?",
            )

            do_turn(
                "Pha 5: Hỏi lại sau compact",
                "group",
                group_id,
                ("march7th",),
                "@march7th Nhắc lại tiến độ task bản tin cuối tuần của nhóm hiện đang thế nào rồi em?",
            )

            checks = _evaluate_checks(
                root=root,
                today=today,
                empty_past_day=seed_info["empty_past_day"],
                traces=traces,
                snap_after_idle1=snap_after_idle1,
                snap_after_idle2=snap_after_idle2,
                snap_after_drop_prior=snap_after_drop_prior,
            )

            report = _render_report(
                model=loaded.defaultModel,
                context_tokens=context_tokens,
                root=root,
                today=today,
                seed_info=seed_info,
                traces=traces,
                idle1_report=idle1_report,
                idle2_report=idle2_report,
                snap_before_compact=snap_before_compact,
                snap_after_idle1=snap_after_idle1,
                snap_after_idle2=snap_after_idle2,
                snap_after_drop_prior=snap_after_drop_prior,
                today_after_idle1=today_after_idle1,
                today_after_idle2=today_after_idle2,
                checks=checks,
            )
            if report_path is not None:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(report, encoding="utf-8")
            return report, root
        finally:
            app.shutdown()
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)


def run_interactive(*, context_tokens: int = 32000) -> None:
    cfg_base = live_config()
    cfg = replace(cfg_base, limits=replace(cfg_base.limits, contextTokens=context_tokens))
    root = Path(tempfile.mkdtemp(prefix="thyca_soak_interactive_"))
    fake_home = root / "_home"
    fake_home.mkdir(parents=True, exist_ok=True)
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(fake_home)
    try:
        loaded = bootstrap_live_root(root, cfg)
        app = ChatApp(root, loaded)
        try:
            _seed_child_bots(app)
            rooms = {
                "dm:thyca": (app.bot_dm(SYSTEM_BOT_ID)["conversation_id"], ("thyca",)),
                "dm:march7th": (app.bot_dm("march7th")["conversation_id"], ("march7th",)),
                "dm:searcher": (app.bot_dm("searcher")["conversation_id"], ("searcher",)),
                "group": (
                    app.create([SYSTEM_BOT_ID, "march7th", "searcher"], title="Phòng họp team")[
                        "conversation_id"
                    ],
                    ("thyca", "march7th", "searcher"),
                ),
            }
            today, _y, tz = day_strings(loaded)
            _seed_multiday_history(
                root,
                datetime.now(tz),
                dm_thyca=rooms["dm:thyca"][0],
                dm_m7=rooms["dm:march7th"][0],
                dm_searcher=rooms["dm:searcher"][0],
                group_id=rooms["group"][0],
            )
            app._background_jobs.job_facade()._refresh_index()
            current = "dm:thyca"
            print(f"Sandbox root: {root}")
            print("Lệnh: /room <dm:thyca|dm:march7th|dm:searcher|group>, /idle, /compact, /compact-fresh, /inspect, /quit")
            step = 0
            while True:
                try:
                    raw = input(f"\n[{current}] > ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not raw:
                    continue
                if raw == "/quit":
                    break
                if raw.startswith("/room "):
                    target = raw.split(" ", 1)[1].strip()
                    if target in rooms:
                        current = target
                        print(f"Đã chuyển sang {current}")
                    else:
                        print(f"Phòng hợp lệ: {', '.join(rooms)}")
                    continue
                if raw == "/idle":
                    print(json.dumps(app.sweep_idle_conversations(idle_s=0), ensure_ascii=False, indent=2))
                    continue
                if raw in ("/compact", "/compact-fresh"):
                    rid, bots = rooms[current]
                    bot = bots[0]
                    res = asyncio.run(
                        app._conversations_admin._compact_async(
                            app._conversations, rid, app._current_cfg(), None, bot_id=bot
                        )
                    )
                    print(res)
                    continue
                if raw == "/inspect":
                    rid, bots = rooms[current]
                    print("--- today.md ---")
                    print(_read_if_exists(root / "memory" / f"{today}.md"))
                    for b in bots:
                        print(f"--- workspace {b} ---")
                        print(json.dumps(_workspace_snapshot(app, b, rid, context_tokens), ensure_ascii=False, indent=2))
                        print(f"--- bots/{b}/USER.md ---")
                        print(_read_if_exists(root / "bots" / b / "USER.md"))
                    continue
                step += 1
                rid, bots = rooms[current]
                tr = _run_monitored_turn(
                    app,
                    step=step,
                    phase="interactive",
                    room_label=current,
                    room_id=rid,
                    bot_ids=bots,
                    user_text=raw,
                )
                for tc in tr.new_tool_calls:
                    print(f"  [tool] {tc['bot']} -> {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})")
                print(f"\n{tr.reply}")
        finally:
            app.shutdown()
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-bot + group live memory soak harness.")
    parser.add_argument("--interactive", action="store_true", help="Interactive sandbox REPL.")
    parser.add_argument("--context-tokens", type=int, default=32000)
    parser.add_argument("--keep-dir", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    if not live_enabled():
        print("Set THYCA_LIVE=1 to run against live provider credentials.")
        return 2
    if args.interactive:
        run_interactive(context_tokens=args.context_tokens)
        return 0
    report, root = run_automated_soak(
        context_tokens=args.context_tokens,
        keep_dir=args.keep_dir,
        report_path=args.report,
    )
    print(f"\nSoak finished. Sandbox preserved at: {root}")
    if args.report:
        print(f"Report written to: {args.report}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
