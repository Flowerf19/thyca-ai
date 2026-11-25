"""Record a scripted WebUI demo video of Thyca (thyca, searcher, march7th) via Chrome CDP and ffmpeg."""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from thyca.serve.static import content_type, safe_file

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "thyca" / "webui"
DEFAULT_OUTPUT = ROOT / "docs" / "demo" / "thyca-demo.mp4"


def _find_chrome() -> str:
    env_bin = os.environ.get("THYCA_CHROME", "").strip()
    if env_bin and Path(env_bin).is_file():
        return env_bin
    for candidate in (
        "/home/bazzite/.local/share/flatpak/exports/bin/com.google.Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError("Chrome/Chromium binary not found; set THYCA_CHROME")


def _iso(offset_minutes: int = 0) -> str:
    return (
        (datetime.now(UTC) + timedelta(minutes=offset_minutes))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")


class DemoState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.bots = [
            {
                "id": "thyca",
                "name": "thyca",
                "role": "Quản lý đội bot",
                "description": "Điều phối công việc, giao nhiệm vụ giữa các bot, quản lý bộ nhớ và hồ sơ chung.",
                "tint": "accent",
                "avatar": "star",
                "system": True,
                "bash": "ask",
                "memory": {"read": True, "write": True},
                "model": {"id": "gemini-2.5-pro", "thinking": "high"},
                "persona": (
                    "## Vai trò\n\n"
                    "- Điều phối đội bot theo đúng thế mạnh từng thành viên.\n"
                    "- Giao việc nghiên cứu có nguồn cho `searcher` và biên tập cộng đồng cho `march7th`.\n"
                    "- Chỉ ghi bộ nhớ dài hạn khi người dùng yêu cầu (`memory_remember`) hoặc khi nén ngữ cảnh.\n"
                ),
            },
            {
                "id": "searcher",
                "name": "searcher",
                "role": "Chuyên viên tìm kiếm thông tin",
                "description": "Tra cứu web qua Tavily (search, extract, research), luôn kèm nguồn URL kiểm chứng.",
                "tint": "success-ink",
                "avatar": "double-flag",
                "system": False,
                "bash": "deny",
                "memory": {"read": True, "write": False},
                "model": {"id": "claude-sonnet-4-6", "thinking": "medium"},
                "persona": (
                    "## Vai trò\n\n"
                    "- Tìm kiếm, trích xuất và đối chiếu thông tin kỹ thuật bằng bộ skill Tavily.\n"
                    "- Luôn gắn URL chính thức và phân biệt rõ dữ liệu đã kiểm chứng với suy luận.\n"
                ),
            },
            {
                "id": "march7th",
                "name": "march7th",
                "role": "Chuyên viên Discord chuẩn mực",
                "description": "Hỗ trợ cộng đồng Discord: biên tập thông báo, giải đáp thân thiện, rõ ràng và đúng chuẩn.",
                "tint": "botanical",
                "avatar": "hollow",
                "system": False,
                "bash": "deny",
                "memory": {"read": True, "write": False},
                "model": {"id": "gpt-5.2", "thinking": "low"},
                "persona": (
                    "## Vai trò\n\n"
                    "- Soạn thông báo Discord ngắn gọn, dễ đọc, thân thiện và chuẩn mực.\n"
                    "- Kiểm tra cấu trúc tin trước khi đăng để thành viên nắm ý chính trong 10 giây.\n"
                ),
            },
        ]
        self.settings: dict[str, dict] = {
            "thyca": {
                "bot_id": "thyca",
                "model": {"id": "gemini-2.5-pro", "thinking": "high"},
                "limits": None,
                "effective": {
                    "model": "gemini-2.5-pro",
                    "provider": "google-demo",
                    "thinking": "high",
                    "limits": {
                        "loopMax": 200,
                        "hotTailKB": 4,
                        "contextTokens": 272000,
                        "softTimeoutS": 60,
                    },
                },
            },
            "searcher": {
                "bot_id": "searcher",
                "model": {"id": "claude-sonnet-4-6", "thinking": "medium"},
                "limits": {"loopMax": 40, "contextTokens": 128000},
                "effective": {
                    "model": "claude-sonnet-4-6",
                    "provider": "anthropic-demo",
                    "thinking": "medium",
                    "limits": {
                        "loopMax": 40,
                        "hotTailKB": 4,
                        "contextTokens": 128000,
                        "softTimeoutS": 60,
                    },
                },
            },
            "march7th": {
                "bot_id": "march7th",
                "model": {"id": "gpt-5.2", "thinking": "low"},
                "limits": None,
                "effective": {
                    "model": "gpt-5.2",
                    "provider": "openai-demo",
                    "thinking": "low",
                    "limits": {
                        "loopMax": 200,
                        "hotTailKB": 4,
                        "contextTokens": 272000,
                        "softTimeoutS": 60,
                    },
                },
            },
        }
        self.dm_messages: dict[str, list[dict]] = {
            "thyca": [],
            "searcher": [],
            "march7th": [],
        }
        self.group_messages: list[dict] = [
            {
                "role": "assistant",
                "content": "Chào mọi người! Phòng **Bản tin cộng đồng** đã sẵn sàng — có thể gọi `@searcher` tra cứu hoặc `@march7th` biên tập ngay tại đây.",
                "ts": _iso(-25),
                "meta": {"bot_id": "thyca"},
            }
        ]
        self.exchanges: dict[str, list[dict]] = {}
        self.leaves: list[dict] = [
            {
                "chunk_id": "mem-compaction-1",
                "memory_id": "m-compact-01",
                "heading": "21:40 — Tóm tắt quy chuẩn đăng bài Discord (từ Nén ngữ cảnh)",
                "snippet": "Bài đăng Discord ưu tiên tối đa 5 gạch đầu dòng, kèm nguồn URL chính thức ở cuối bài và không dùng thuật ngữ gây rối người mới.",
                "timeline_day": _yesterday(),
                "is_today": False,
                "kind": "checkpoint",
                "status": "done",
                "bots": ["thyca", "march7th"],
                "get_count": 3,
                "search_count": 2,
                "last_get_at": _iso(-120),
                "expires_at": None,
            },
            {
                "chunk_id": "mem-compaction-2",
                "memory_id": "m-compact-02",
                "heading": "19:15 — Nguồn benchmark ưu tiên cho Searcher (từ Nén ngữ cảnh)",
                "snippet": "Khi so sánh model reasoning/decision, đối chiếu tài liệu chính thức của OpenAI, Anthropic, Google DeepMind và bảng đánh giá công khai.",
                "timeline_day": _yesterday(),
                "is_today": False,
                "kind": "habit",
                "habit": "nghiên cứu",
                "status": None,
                "bots": ["searcher"],
                "get_count": 2,
                "search_count": 4,
                "last_get_at": _iso(-60),
                "expires_at": None,
            },
        ]
        self.canonical = {
            "USER.md": "Người dùng điều hành cộng đồng Discord kỹ thuật AI và muốn các bản tin ngắn gọn, có nguồn rõ ràng.",
            "SOUL.md": "Trò chuyện thẳng thắn, rõ ràng, không rườm rà; ưu tiên số liệu và nguồn kiểm chứng.",
            "IDENTITY.md": "Thyca — trợ lý điều phối đội bot chạy local-first trên máy cá nhân.",
        }

    def add_remembered_habit(self) -> None:
        with self.lock:
            if any(leaf["memory_id"] == "m-remember-03" for leaf in self.leaves):
                return
            self.leaves.insert(
                0,
                {
                    "chunk_id": "mem-remember-3",
                    "memory_id": "m-remember-03",
                    "heading": f"{datetime.now(UTC).strftime('%H:%M')} — Lịch đăng bản tin model thứ Sáu (memory_remember)",
                    "snippet": "Tối thứ Sáu hàng tuần đăng bản tin Discord về model mới: Searcher kiểm tra nguồn chính thức trước, sau đó March 7th biên tập bản chuẩn mực.",
                    "timeline_day": _today(),
                    "is_today": True,
                    "kind": "habit",
                    "habit": "bản tin cộng đồng",
                    "status": None,
                    "bots": ["thyca", "searcher", "march7th"],
                    "get_count": 1,
                    "search_count": 0,
                    "last_get_at": _iso(0),
                    "expires_at": None,
                },
            )

    def config_payload(self) -> dict:
        return {
            "values": {
                "providers": {
                    "google-demo": {
                        "api": "google_gemini",
                        "baseUrl": "https://generativelanguage.googleapis.com/v1beta",
                        "hasKey": True,
                        "keySource": "auth.json",
                    },
                    "anthropic-demo": {
                        "api": "anthropic_messages",
                        "baseUrl": "https://api.anthropic.com/v1",
                        "hasKey": True,
                        "keySource": "auth.json",
                    },
                    "openai-demo": {
                        "api": "openai_responses",
                        "baseUrl": "https://api.openai.com/v1",
                        "hasKey": True,
                        "keySource": "auth.json",
                    },
                },
                "models": {
                    "gemini-2.5-pro": {
                        "provider": "google-demo",
                        "reasoningEfforts": ["low", "medium", "high"],
                    },
                    "claude-sonnet-4-6": {
                        "provider": "anthropic-demo",
                        "reasoningEfforts": ["low", "medium", "high", "max"],
                    },
                    "gpt-5.2": {
                        "provider": "openai-demo",
                        "reasoningEfforts": ["low", "medium", "high", "xhigh"],
                    },
                },
                "defaultProvider": "google-demo",
                "defaultModel": "gemini-2.5-pro",
                "limits": {
                    "loopMax": 200,
                    "hotTailKB": 4,
                    "contextTokens": 272000,
                    "softTimeoutS": 60,
                },
            },
            "schema": {
                "sections": [
                    {
                        "key": "provider",
                        "label": "Provider",
                        "fields": [
                            {
                                "key": "provider.reasoningEffort",
                                "choices": ["low", "medium", "high", "xhigh", "max"],
                            }
                        ],
                    },
                    {
                        "key": "limits",
                        "label": "Giới hạn",
                        "fields": [
                            {"key": "limits.loopMax", "min": 1, "max": 200, "default": 200},
                            {"key": "limits.hotTailKB", "min": 1, "max": 64, "default": 4},
                            {
                                "key": "limits.contextTokens",
                                "min": 32000,
                                "max": 1000000,
                                "default": 272000,
                            },
                            {"key": "limits.softTimeoutS", "min": 1, "max": 300, "default": 60},
                        ],
                    },
                ]
            },
            "defaults": {},
        }

    def traces_payload(self) -> dict:
        return {
            "traces": [
                {
                    "kind": "dm",
                    "bot_id": "thyca",
                    "turn_index": 0,
                    "title": "Tìm các model reasoning / decision mạnh nhất & soạn bản tin Discord",
                    "started_at": _iso(-2),
                    "status": "completed",
                    "model": "gemini-2.5-pro",
                    "requests": 5,
                    "tool_calls": 3,
                    "total_tokens": 18420,
                    "cost_usd": 0.0412,
                    "latency_ms": 6420,
                    "bot_breakdown": [
                        {"bot_id": "thyca", "requests": 3, "total_tokens": 9120},
                        {"bot_id": "searcher", "requests": 1, "total_tokens": 5840},
                        {"bot_id": "march7th", "requests": 1, "total_tokens": 3460},
                    ],
                },
                {
                    "kind": "group",
                    "group_id": "grp-community",
                    "turn_index": 1,
                    "title": "Bản tin cộng đồng",
                    "started_at": _iso(-1),
                    "status": "completed",
                    "model": "claude-sonnet-4-6",
                    "requests": 3,
                    "tool_calls": 1,
                    "total_tokens": 9650,
                    "cost_usd": 0.0235,
                    "latency_ms": 3890,
                    "bot_breakdown": [
                        {"bot_id": "searcher", "requests": 2, "total_tokens": 6100},
                        {"bot_id": "march7th", "requests": 1, "total_tokens": 3550},
                    ],
                },
                {
                    "kind": "dm",
                    "bot_id": "thyca",
                    "turn_index": 1,
                    "title": "Lưu quy trình bản tin thứ Sáu (memory_remember)",
                    "started_at": _iso(0),
                    "status": "completed",
                    "model": "gemini-2.5-pro",
                    "requests": 2,
                    "tool_calls": 1,
                    "total_tokens": 4180,
                    "cost_usd": 0.0094,
                    "latency_ms": 1840,
                    "bot_breakdown": [{"bot_id": "thyca", "requests": 2, "total_tokens": 4180}],
                },
            ],
            "total": 3,
        }

    def trace_stats_payload(self) -> dict:
        today = _today()
        yesterday = _yesterday()
        return {
            "totals": {
                "requests": 10,
                "tool_calls": 5,
                "prompt_tokens": 24850,
                "cached_tokens": 11200,
                "completion_tokens": 7400,
                "total_tokens": 32250,
                "cost_usd": 0.0741,
                "latency_ms_p50": 1840,
                "latency_ms_p90": 6420,
            },
            "by_day": [
                {"day": yesterday, "requests": 4, "total_tokens": 14200, "cost_usd": 0.031},
                {"day": today, "requests": 6, "total_tokens": 18050, "cost_usd": 0.0431},
            ],
            "by_bot": [
                {
                    "bot_id": "thyca",
                    "requests": 5,
                    "tool_calls": 3,
                    "total_tokens": 13300,
                    "cost_usd": 0.0298,
                },
                {
                    "bot_id": "searcher",
                    "requests": 3,
                    "tool_calls": 2,
                    "total_tokens": 11940,
                    "cost_usd": 0.0281,
                },
                {
                    "bot_id": "march7th",
                    "requests": 2,
                    "tool_calls": 0,
                    "total_tokens": 7010,
                    "cost_usd": 0.0162,
                },
            ],
            "by_model": [
                {
                    "model": "gemini-2.5-pro",
                    "requests": 5,
                    "total_tokens": 13300,
                    "cost_usd": 0.0298,
                    "latency_ms_p50": 1840,
                },
                {
                    "model": "claude-sonnet-4-6",
                    "requests": 3,
                    "total_tokens": 11940,
                    "cost_usd": 0.0281,
                    "latency_ms_p50": 1950,
                },
                {
                    "model": "gpt-5.2",
                    "requests": 2,
                    "total_tokens": 7010,
                    "cost_usd": 0.0162,
                    "latency_ms_p50": 1420,
                },
            ],
            "by_hour": [],
            "models": ["gemini-2.5-pro", "claude-sonnet-4-6", "gpt-5.2"],
            "by_status": [{"status": "completed", "requests": 10}],
        }

    def trace_detail_payload(self) -> dict:
        return {
            "kind": "dm",
            "bot_id": "thyca",
            "turn_index": 0,
            "title": "Tìm các model reasoning / decision mạnh nhất & soạn bản tin Discord",
            "started_at": _iso(-2),
            "ended_at": _iso(-1),
            "status": "completed",
            "model": "gemini-2.5-pro",
            "requests": 5,
            "rounds": 3,
            "tool_calls": 3,
            "prompt_tokens": 13800,
            "cached_tokens": 6400,
            "completion_tokens": 4620,
            "total_tokens": 18420,
            "cost_usd": 0.0412,
            "latency_ms": 6420,
            "trace_id": "tr-demo-decision-01",
            "bot_breakdown": [
                {"bot_id": "thyca", "requests": 3, "total_tokens": 9120},
                {"bot_id": "searcher", "requests": 1, "total_tokens": 5840},
                {"bot_id": "march7th", "requests": 1, "total_tokens": 3460},
            ],
            "messages": [
                {
                    "role": "system",
                    "content": "Đã nén ngữ cảnh phiên trước và trích xuất 2 ghi nhớ dài hạn vào sổ Bộ nhớ.",
                    "ts": _iso(-3),
                    "meta": {"kind": "compaction"},
                },
                {
                    "role": "user",
                    "content": "Tìm các model reasoning / decision mạnh nhất hiện giờ rồi nhờ March 7th soạn bản tin ngắn cho Discord nhé.",
                    "ts": _iso(-2),
                },
                {
                    "role": "assistant",
                    "content": "Mình sẽ giao cho `searcher` tra cứu nguồn chính thức trước, rồi chuyển sang `march7th` biên tập bản tin Discord.",
                    "ts": _iso(-2),
                    "meta": {
                        "bot_id": "thyca",
                        "model": "gemini-2.5-pro",
                        "round": 1,
                        "latency_ms": 1450,
                        "finish_reason": "tool_calls",
                        "reasoning": "Cần chia 2 bước: 1) nhờ searcher kiểm chứng danh sách model reasoning/decision mới nhất kèm URL; 2) nhờ march7th định dạng bản tin Discord ngắn gọn.",
                        "usage": {"total_tokens": 4120},
                    },
                    "tool_calls": [
                        {
                            "id": "call-searcher-1",
                            "name": "bot_send",
                            "arguments": {
                                "to": "searcher",
                                "message": "Tra cứu top model reasoning / ra quyết định mạnh nhất hiện nay kèm URL tài liệu chính thức.",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-searcher-1",
                    "content": "Đã tổng hợp 3 nhóm dẫn đầu: OpenAI o3/GPT-5.2, Claude Opus/Sonnet 4.6 Adaptive Thinking, và Gemini 2.5 Pro Deep Think kèm nguồn chính thức.",
                    "ts": _iso(-2),
                },
                {
                    "role": "assistant",
                    "content": None,
                    "ts": _iso(-2),
                    "meta": {
                        "bot_id": "searcher",
                        "model": "claude-sonnet-4-6",
                        "round": 1,
                        "latency_ms": 1820,
                        "finish_reason": "tool_calls",
                        "usage": {"total_tokens": 5840},
                    },
                    "tool_calls": [
                        {
                            "id": "call-tavily-1",
                            "name": "tavily-search",
                            "skill": "tavily-search",
                            "arguments": {
                                "query": "top frontier reasoning decision LLM models OpenAI Anthropic Gemini official docs"
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-tavily-1",
                    "content": "3 kết quả chính thức từ openai.com, anthropic.com, ai.google.dev.",
                    "ts": _iso(-2),
                },
                {
                    "role": "assistant",
                    "content": None,
                    "ts": _iso(-1),
                    "meta": {
                        "bot_id": "thyca",
                        "model": "gemini-2.5-pro",
                        "round": 2,
                        "latency_ms": 1120,
                        "finish_reason": "tool_calls",
                        "usage": {"total_tokens": 2650},
                    },
                    "tool_calls": [
                        {
                            "id": "call-march-1",
                            "name": "bot_send",
                            "arguments": {
                                "to": "march7th",
                                "message": "Biên tập tóm tắt top model reasoning/decision từ Searcher thành bản tin Discord 4 dòng rõ ràng, thân thiện.",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-march-1",
                    "content": "Bản tin Discord đã sẵn sàng kèm 3 gạch đầu dòng và liên kết nguồn.",
                    "ts": _iso(-1),
                },
                {
                    "role": "assistant",
                    "content": "Đã hoàn tất! `searcher` đã đối chiếu nguồn chính thức và `march7th` đã chốt bản tin ngắn gọn sẵn sàng đăng Discord.",
                    "ts": _iso(-1),
                    "meta": {
                        "bot_id": "thyca",
                        "model": "gemini-2.5-pro",
                        "round": 3,
                        "latency_ms": 980,
                        "finish_reason": "stop",
                        "usage": {"total_tokens": 2350},
                    },
                },
            ],
        }


def _make_handler(state: DemoState):
    class DemoHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, events: list[tuple[float, dict]]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for delay, ev in events:
                if delay > 0:
                    time.sleep(delay)
                line = (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/api/config/status":
                self._json({"ready": True, "version": "0.88.0.dev4"})
                return
            if path == "/api/config":
                self._json(state.config_payload())
                return
            if path == "/api/bots":
                self._json({"bots": state.bots})
                return
            if path == "/api/conversations":
                convs = [
                    {
                        "kind": "dm",
                        "bot_id": "thyca",
                        "turns": max(1, len(state.dm_messages["thyca"]) // 2),
                        "updated_at": _iso(0),
                    },
                    {
                        "kind": "group",
                        "group_id": "grp-community",
                        "title": "Bản tin cộng đồng",
                        "bots": ["thyca", "searcher", "march7th"],
                        "turns": max(1, len(state.group_messages) // 2),
                        "updated_at": _iso(-1),
                    },
                ]
                self._json({"conversations": convs})
                return
            if path.startswith("/api/bots/") and path.endswith("/dm"):
                bot_id = path.split("/")[3]
                self._json(
                    {
                        "kind": "dm",
                        "bot_id": bot_id,
                        "messages": list(state.dm_messages.get(bot_id, [])),
                        "running": False,
                    }
                )
                return
            if path.startswith("/api/bots/") and path.endswith("/dm/operations"):
                self._json({"context_tokens": 18420, "context_limit": 272000})
                return
            if path.startswith("/api/bots/") and path.endswith("/tasks"):
                self._json({"tasks": []})
                return
            if path == "/api/groups/grp-community":
                self._json(
                    {
                        "kind": "group",
                        "group_id": "grp-community",
                        "title": "Bản tin cộng đồng",
                        "bots": ["thyca", "searcher", "march7th"],
                        "messages": list(state.group_messages),
                        "running": False,
                    }
                )
                return
            if path == "/api/groups/grp-community/operations":
                self._json({"context_tokens": 9650, "context_limit": 272000})
                return
            if path.startswith("/api/bots/") and path.endswith("/exchanges"):
                bot_id = path.split("/")[3]
                if bot_id == "thyca" and state.exchanges:
                    items = [
                        {
                            "partner": partner,
                            "count": len(msgs),
                            "last_ts": msgs[-1]["ts"],
                        }
                        for partner, msgs in state.exchanges.items()
                    ]
                    self._json({"exchanges": items})
                else:
                    self._json({"exchanges": []})
                return
            if "/exchanges/" in path:
                partner = path.split("/exchanges/")[1]
                self._json({"messages": list(state.exchanges.get(partner, []))})
                return
            if path.startswith("/api/bots/") and path.endswith("/settings"):
                bot_id = path.split("/")[3]
                self._json(state.settings[bot_id])
                return
            if path == "/api/memory/stats":
                qs = parse_qs(parsed.query)
                bot_scope = (qs.get("bot_id") or [None])[0]
                with state.lock:
                    leaves = list(state.leaves)
                if bot_scope:
                    leaves = [
                        item for item in leaves if bot_scope in (item.get("bots") or [])
                    ]
                used_count = sum(1 for item in leaves if (item.get("get_count") or 0) > 0)
                searched_count = sum(1 for item in leaves if (item.get("search_count") or 0) > 0)
                untouched_count = sum(
                    1
                    for item in leaves
                    if not (item.get("get_count") or 0) and not (item.get("search_count") or 0)
                )
                self._json(
                    {
                        "bot_id": bot_scope,
                        "files": [
                            {"name": name, "content": content}
                            for name, content in state.canonical.items()
                        ],
                        "leaves": leaves,
                        "expiring": [],
                        "suggest_removal": [],
                        "total": len(leaves),
                        "used": used_count,
                        "searched": searched_count,
                        "untouched": untouched_count,
                        "counts": {
                            "total": len(leaves),
                            "habit": sum(1 for item in leaves if item.get("kind") == "habit"),
                            "checkpoint": sum(
                                1 for item in leaves if item.get("kind") == "checkpoint"
                            ),
                        },
                    }
                )
                return
            if path == "/api/traces/stats":
                self._json(state.trace_stats_payload())
                return
            if path == "/api/traces":
                self._json(state.traces_payload())
                return
            if path.startswith("/api/traces/"):
                self._json(state.trace_detail_payload())
                return

            target = safe_file(WEBUI, path)
            if target is None:
                self.send_error(404)
                return
            body = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type(target))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")

            if path == "/api/bots/thyca/dm/turn/stream":
                text = str(payload.get("text") or "")
                now_ts = _iso(0)
                if "nhớ" in text.lower() or "quy trình" in text.lower():
                    reply_text = (
                        "Đã ghi vào **Bộ nhớ dài hạn** (`memory_remember`):\n\n"
                        "- **Lịch:** Tối thứ Sáu hàng tuần đăng bản tin Discord về model mới.\n"
                        "- **Quy trình:** `searcher` đối chiếu nguồn chính thức trước → `march7th` biên tập bản đăng Discord.\n\n"
                        "*(Bộ nhớ chỉ được lưu khi bạn chủ động yêu cầu hoặc khi hệ thống nén ngữ cảnh.)*"
                    )
                    state.dm_messages["thyca"].append({"role": "user", "content": text, "ts": now_ts})
                    state.dm_messages["thyca"].append(
                        {
                            "role": "assistant",
                            "content": reply_text,
                            "ts": _iso(0),
                            "meta": {"bot_id": "thyca"},
                            "tool_calls": [{"name": "memory_remember"}],
                        }
                    )
                    state.add_remembered_habit()
                    events = [
                        (0.05, {"type": "turn.accepted", "bot_id": "thyca"}),
                        (0.25, {"type": "llm.started", "bot_id": "thyca"}),
                        (0.35, {"type": "llm.thinking", "bot_id": "thyca"}),
                        (
                            0.45,
                            {
                                "type": "tool.started",
                                "bot_id": "thyca",
                                "name": "memory_remember",
                                "call_id": "mem-1",
                            },
                        ),
                        (
                            0.85,
                            {
                                "type": "tool.finished",
                                "bot_id": "thyca",
                                "name": "memory_remember",
                                "call_id": "mem-1",
                            },
                        ),
                        (0.25, {"type": "llm.started", "bot_id": "thyca"}),
                    ]
                    events.extend(
                        (0.18, {"type": "llm.content", "bot_id": "thyca", "delta": piece})
                        for piece in (
                            "Đã ghi vào **Bộ nhớ dài hạn** (`memory_remember`):\n\n",
                            "- **Lịch:** Tối thứ Sáu hàng tuần đăng bản tin Discord về model mới.\n",
                            "- **Quy trình:** `searcher` đối chiếu nguồn chính thức trước → `march7th` biên tập bản đăng Discord.\n\n",
                            "*(Bộ nhớ chỉ được lưu khi bạn chủ động yêu cầu hoặc khi hệ thống nén ngữ cảnh.)*",
                        )
                    )
                    events.append(
                        (
                            0.25,
                            {
                                "type": "turn.completed",
                                "bot_id": "thyca",
                                "detail": {
                                    "kind": "dm",
                                    "bot_id": "thyca",
                                    "messages": list(state.dm_messages["thyca"]),
                                },
                            },
                        )
                    )
                    self._stream(events)
                    return

                reply_text = (
                    "Đội đã phối hợp xong — **`searcher`** đã kiểm chứng nguồn và **`march7th`** đã biên tập bản tin ngắn cho Discord:\n\n"
                    "### Top model reasoning / decision nổi bật hiện nay\n"
                    "1. **OpenAI o3 & GPT-5.2** — mạnh về lập luận nhiều bước, tối ưu ra quyết định và gọi tool phức tạp.\n"
                    "2. **Claude Opus / Sonnet 4.6 (Adaptive Thinking)** — cân bằng sâu sắc giữa phân tích kỹ thuật, viết mã và tuân thủ quy tắc.\n"
                    "3. **Gemini 2.5 Pro (Deep Think)** — ngữ cảnh dài tới 1M tokens, rất tốt khi tổng hợp tài liệu lớn trước khi quyết định.\n\n"
                    "> Bạn có thể bấm vào khối **tin nhắn với Searcher / March 7th** bên dưới để xem chi tiết cách các bot trao đổi nội bộ."
                )
                t0 = _iso(-1)
                t1 = _iso(0)
                state.dm_messages["thyca"].append({"role": "user", "content": text, "ts": t0})
                state.dm_messages["thyca"].append(
                    {
                        "role": "assistant",
                        "content": reply_text,
                        "ts": t1,
                        "meta": {"bot_id": "thyca"},
                        "tool_calls": [
                            {"name": "bot_send"},
                            {"name": "tavily-search"},
                            {"name": "bot_send"},
                        ],
                    }
                )
                state.exchanges["searcher"] = [
                    {
                        "author": "thyca",
                        "ts": t0,
                        "content": "Nhờ Searcher tra nhanh các model reasoning / ra quyết định (decision) mạnh nhất hiện giờ, kèm nguồn chính thức nhé.",
                    },
                    {
                        "author": "searcher",
                        "ts": t0,
                        "content": (
                            "Đã kiểm tra qua `tavily-search`:\n"
                            "- **OpenAI o3 / GPT-5.2**: dẫn đầu chuỗi lập luận nhiều bước & tool-use (`openai.com`).\n"
                            "- **Claude 4.6 (Adaptive Thinking)**: mạnh phân tích hệ thống & tuân thủ tiêu chí (`anthropic.com`).\n"
                            "- **Gemini 2.5 Pro**: cửa sổ ngữ cảnh 1M + Deep Think cho bài toán nhiều tài liệu (`ai.google.dev`)."
                        ),
                    },
                ]
                state.exchanges["march7th"] = [
                    {
                        "author": "thyca",
                        "ts": t0,
                        "content": "March 7th biên tập danh sách model từ Searcher thành bản tin Discord ngắn gọn, thân thiện nhé.",
                    },
                    {
                        "author": "march7th",
                        "ts": t0,
                        "content": (
                            "Bản nháp Discord đã xong!\n\n"
                            "**[Cập nhật AI] Top 3 dòng model reasoning / decision đáng chú ý**\n"
                            "- **OpenAI o3 / GPT-5.2** — lập luận nhiều bước & ra quyết định tự động.\n"
                            "- **Claude 4.6** — phân tích sâu, trình bày rõ ràng, giữ chuẩn tốt.\n"
                            "- **Gemini 2.5 Pro** — đọc tài liệu dài tới 1M tokens mượt mà."
                        ),
                    },
                ]
                events = [
                    (0.05, {"type": "turn.accepted", "bot_id": "thyca"}),
                    (0.25, {"type": "llm.started", "bot_id": "thyca"}),
                    (0.35, {"type": "llm.thinking", "bot_id": "thyca"}),
                    (
                        0.45,
                        {
                            "type": "tool.started",
                            "bot_id": "thyca",
                            "name": "bot_send → searcher",
                            "call_id": "c-s1",
                        },
                    ),
                    (
                        0.25,
                        {
                            "type": "skill.started",
                            "bot_id": "searcher",
                            "name": "tavily-search",
                            "call_id": "s-t1",
                        },
                    ),
                    (
                        1.10,
                        {
                            "type": "skill.finished",
                            "bot_id": "searcher",
                            "name": "tavily-search",
                            "call_id": "s-t1",
                        },
                    ),
                    (
                        0.20,
                        {
                            "type": "tool.finished",
                            "bot_id": "thyca",
                            "name": "bot_send → searcher",
                            "call_id": "c-s1",
                        },
                    ),
                    (
                        0.30,
                        {
                            "type": "tool.started",
                            "bot_id": "thyca",
                            "name": "bot_send → march7th",
                            "call_id": "c-m1",
                        },
                    ),
                    (0.20, {"type": "llm.thinking", "bot_id": "march7th"}),
                    (0.55, {"type": "llm.content", "bot_id": "march7th", "delta": "Đang biên tập bản tin Discord..."}),
                    (
                        0.55,
                        {
                            "type": "tool.finished",
                            "bot_id": "thyca",
                            "name": "bot_send → march7th",
                            "call_id": "c-m1",
                        },
                    ),
                    (0.25, {"type": "llm.started", "bot_id": "thyca"}),
                ]
                events.extend(
                    (0.20, {"type": "llm.content", "bot_id": "thyca", "delta": piece})
                    for piece in (
                        "Đội đã phối hợp xong — **`searcher`** đã kiểm chứng nguồn và **`march7th`** đã biên tập bản tin ngắn cho Discord:\n\n",
                        "### Top model reasoning / decision nổi bật hiện nay\n",
                        "1. **OpenAI o3 & GPT-5.2** — mạnh về lập luận nhiều bước, tối ưu ra quyết định và gọi tool phức tạp.\n",
                        "2. **Claude Opus / Sonnet 4.6 (Adaptive Thinking)** — cân bằng sâu sắc giữa phân tích kỹ thuật, viết mã và tuân thủ quy tắc.\n",
                        "3. **Gemini 2.5 Pro (Deep Think)** — ngữ cảnh dài tới 1M tokens, rất tốt khi tổng hợp tài liệu lớn trước khi quyết định.\n\n",
                        "> Bạn có thể bấm vào khối **tin nhắn với Searcher / March 7th** bên dưới để xem chi tiết cách các bot trao đổi nội bộ.",
                    )
                )
                events.append(
                    (
                        0.25,
                        {
                            "type": "turn.completed",
                            "bot_id": "thyca",
                            "detail": {
                                "kind": "dm",
                                "bot_id": "thyca",
                                "messages": list(state.dm_messages["thyca"]),
                            },
                        },
                    )
                )
                self._stream(events)
                return

            if path == "/api/groups/grp-community/turn/stream":
                text = str(payload.get("text") or "")
                t_now = _iso(0)
                searcher_msg = (
                    "Mình đã gom đủ 3 liên kết tài liệu chính thức cho bài đăng:\n"
                    "- OpenAI Reasoning Models: `https://platform.openai.com/docs/guides/reasoning`\n"
                    "- Anthropic Extended & Adaptive Thinking: `https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking`\n"
                    "- Google Gemini Thinking: `https://ai.google.dev/gemini-api/docs/thinking`"
                )
                march_msg = (
                    "Cảm ơn `@searcher`! Bản chốt đăng Discord đã sẵn sàng:\n\n"
                    "**Bản tin cuối tuần · Top model reasoning / decision**\n"
                    "- **OpenAI o3 / GPT-5.2**: tối ưu lập luận & quyết định nhiều bước.\n"
                    "- **Claude 4.6**: phân tích sâu, linh hoạt mức suy luận.\n"
                    "- **Gemini 2.5 Pro**: xử lý ngữ cảnh dài đến 1M tokens.\n"
                    "Nguồn chính thức đã gắn kèm từ Searcher — cả nhà phản hồi bên dưới nếu muốn đội test thêm model nào nhé!"
                )
                state.group_messages.append({"role": "user", "content": text, "ts": t_now})
                state.group_messages.append(
                    {
                        "role": "assistant",
                        "content": searcher_msg,
                        "ts": t_now,
                        "meta": {"bot_id": "searcher"},
                        "tool_calls": [{"name": "tavily-search"}],
                    }
                )
                state.group_messages.append(
                    {
                        "role": "assistant",
                        "content": march_msg,
                        "ts": t_now,
                        "meta": {"bot_id": "march7th"},
                    }
                )
                events = [
                    (0.05, {"type": "turn.accepted", "bot_id": "searcher"}),
                    (
                        0.25,
                        {
                            "type": "skill.started",
                            "bot_id": "searcher",
                            "name": "tavily-search",
                            "call_id": "g-s1",
                        },
                    ),
                    (
                        0.90,
                        {
                            "type": "skill.finished",
                            "bot_id": "searcher",
                            "name": "tavily-search",
                            "call_id": "g-s1",
                        },
                    ),
                    (0.20, {"type": "llm.started", "bot_id": "searcher"}),
                    (
                        0.25,
                        {
                            "type": "llm.content",
                            "bot_id": "searcher",
                            "delta": "Đã kiểm tra 3 liên kết tài liệu chính thức (OpenAI, Anthropic, Google Gemini) — chuyển `@march7th` chốt bản đăng Discord...",
                        },
                    ),
                    (0.45, {"type": "turn.accepted", "bot_id": "march7th"}),
                    (0.25, {"type": "llm.started", "bot_id": "march7th"}),
                    (0.35, {"type": "llm.thinking", "bot_id": "march7th"}),
                    (
                        0.30,
                        {
                            "type": "llm.content",
                            "bot_id": "march7th",
                            "delta": "Đang hoàn thiện bản tin cuối tuần cho server Discord...",
                        },
                    ),
                    (
                        0.55,
                        {
                            "type": "turn.completed",
                            "bot_id": "march7th",
                            "detail": {
                                "kind": "group",
                                "group_id": "grp-community",
                                "title": "Bản tin cộng đồng",
                                "bots": ["thyca", "searcher", "march7th"],
                                "messages": list(state.group_messages),
                            },
                        },
                    ),
                ]
                self._stream(events)
                return

            if path.startswith("/api/bots/") and path.endswith("/settings"):
                bot_id = path.split("/")[3]
                cur = state.settings[bot_id]
                if isinstance(payload.get("model"), dict):
                    cur["model"] = payload["model"]
                    cur["effective"]["model"] = payload["model"].get("id") or cur["effective"]["model"]
                    cur["effective"]["thinking"] = payload["model"].get("thinking")
                if "limits" in payload:
                    cur["limits"] = payload["limits"]
                self._json(cur)
                return

            self._json({"ok": True})

    return DemoHandler


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("CDP websocket closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _ws_send(sock: socket.socket, payload: bytes) -> None:
    mask = secrets.token_bytes(4)
    size = len(payload)
    header = bytes([0x81, 0x80 | (size if size < 126 else 126 if size < 65536 else 127)])
    if size >= 126:
        header += struct.pack("!H" if size < 65536 else "!Q", size)
    sock.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))


def _ws_recv(sock: socket.socket) -> dict:
    while True:
        first, second = _read_exact(sock, 2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", _read_exact(sock, 2))[0]
        elif size == 127:
            size = struct.unpack("!Q", _read_exact(sock, 8))[0]
        mask = _read_exact(sock, 4) if second & 0x80 else b""
        payload = _read_exact(sock, size)
        if mask:
            payload = bytes(v ^ mask[i % 4] for i, v in enumerate(payload))
        opcode = first & 0x0F
        if opcode == 8:
            raise ConnectionError("CDP websocket closed")
        if opcode == 1:
            return json.loads(payload)


class CDPSession:
    def __init__(self, ws_url: str, frames_dir: Path) -> None:
        parsed = urlsplit(ws_url)
        self._sock = socket.create_connection((parsed.hostname, parsed.port), timeout=15)
        self._sock.settimeout(30)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(req.encode())
        header = b""
        while b"\r\n\r\n" not in header:
            header += self._sock.recv(1)
        self._send_lock = threading.Lock()
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, dict] = {}
        self._next_id = 0
        self._frames_dir = frames_dir
        self.frames: list[tuple[float, Path]] = []
        self._recording = False
        self._stop = False
        self._reader = threading.Thread(target=self._loop, daemon=True)
        self._reader.start()

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        with self._send_lock:
            self._next_id += 1
            cid = self._next_id
            ev = threading.Event()
            self._pending[cid] = ev
            msg = {"id": cid, "method": method, "params": params or {}}
            _ws_send(self._sock, json.dumps(msg).encode("utf-8"))
        if not ev.wait(timeout):
            raise TimeoutError(f"CDP timeout on {method}")
        return self._results.pop(cid, {})

    def start_recording(self) -> None:
        self._recording = True
        self.call(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 92,
                "maxWidth": 1280,
                "maxHeight": 720,
                "everyNthFrame": 1,
            },
        )

    def stop_recording(self) -> None:
        self._recording = False
        try:
            self.call("Page.stopScreencast")
        except Exception:
            pass

    def _loop(self) -> None:
        while not self._stop:
            try:
                msg = _ws_recv(self._sock)
            except Exception:
                break
            if "id" in msg:
                cid = msg["id"]
                self._results[cid] = msg
                ev = self._pending.pop(cid, None)
                if ev:
                    ev.set()
            elif msg.get("method") == "Page.screencastFrame":
                params = msg.get("params") or {}
                sid = params.get("sessionId")
                if self._recording and params.get("data"):
                    ts = time.monotonic()
                    idx = len(self.frames) + 1
                    frame_path = self._frames_dir / f"frame_{idx:05d}.jpg"
                    frame_path.write_bytes(base64.b64decode(params["data"]))
                    self.frames.append((ts, frame_path))
                if sid is not None:
                    with self._send_lock:
                        self._next_id += 1
                        ack = {
                            "id": self._next_id,
                            "method": "Page.screencastFrameAck",
                            "params": {"sessionId": sid},
                        }
                        try:
                            _ws_send(self._sock, json.dumps(ack).encode("utf-8"))
                        except Exception:
                            pass

    def close(self) -> None:
        self._stop = True
        try:
            self._sock.close()
        except Exception:
            pass


DEMO_CHOREOGRAPHY_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms = 12000, label = 'condition') => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const val = fn();
      if (val) return val;
      await sleep(50);
    }
    throw new Error('timeout waiting for ' + label);
  };

  const style = document.createElement('style');
  style.textContent = `
    #demo-caption {
      position: fixed; left: 46%; top: 8px; transform: translateX(-50%);
      z-index: 99998; pointer-events: none;
      background: rgba(29, 36, 31, 0.88); color: #f8f5ec;
      padding: 5px 14px; border-radius: 999px;
      font: 500 12px/1.3 system-ui, -apple-system, sans-serif;
      box-shadow: 0 4px 14px rgba(0,0,0,.16);
      border: 1px solid rgba(255,255,255,.14);
      letter-spacing: 0.01em; transition: opacity .25s ease;
      max-width: 640px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    #demo-cursor {
      position: fixed; left: 640px; top: 360px; width: 20px; height: 20px;
      margin: -10px 0 0 -10px; border-radius: 50%; z-index: 99999;
      pointer-events: none;
      background: rgba(166, 90, 66, 0.28);
      border: 2px solid #a65a42;
      box-shadow: 0 2px 10px rgba(0,0,0,.22);
      transition: left .42s cubic-bezier(.22,1,.36,1), top .42s cubic-bezier(.22,1,.36,1), transform .14s ease;
    }
    #demo-cursor.click { transform: scale(0.72); background: rgba(166, 90, 66, 0.55); }
  `;
  document.head.appendChild(style);
  const captionEl = document.createElement('div');
  captionEl.id = 'demo-caption';
  document.body.appendChild(captionEl);
  const cursorEl = document.createElement('div');
  cursorEl.id = 'demo-cursor';
  document.body.appendChild(cursorEl);

  const caption = text => { captionEl.textContent = text; };
  const moveTo = async el => {
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    await sleep(180);
    const r = el.getBoundingClientRect();
    cursorEl.style.left = Math.round(r.left + r.width / 2) + 'px';
    cursorEl.style.top = Math.round(r.top + r.height / 2) + 'px';
    await sleep(460);
  };
  const clickEl = async el => {
    await moveTo(el);
    cursorEl.classList.add('click');
    await sleep(110);
    el.click();
    await sleep(140);
    cursorEl.classList.remove('click');
    await sleep(180);
  };
  const typeComposer = async text => {
    const box = await waitFor(() => document.querySelector('#message'));
    await clickEl(box);
    box.focus();
    box.value = '';
    for (let i = 0; i < text.length; i += 3) {
      box.value += text.slice(i, i + 3);
      box.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(24);
    }
    await sleep(300);
  };

  try {
  await waitFor(() => window.ThycaRouter?.currentPage() === 'chat' && document.querySelector('#bot-thyca') && document.querySelector('#bot-searcher') && document.querySelector('#bot-march7th'));
  caption('Thyca · Đội 3 bot local-first: thyca (điều phối), searcher (tìm nguồn), march7th (Discord)');
  await sleep(1600);

  caption('1 · Giao việc cho thyca → tự điều phối searcher (Tavily) & march7th (biên tập Discord)');
  await typeComposer('Tìm các model reasoning / decision mạnh nhất hiện giờ rồi nhờ March 7th soạn bản tin ngắn cho Discord nhé.');
  await clickEl(document.querySelector('#send-message'));
  await waitFor(() => document.querySelector('#message-list [data-exchange-partner="searcher"]'), 15000);
  await sleep(1600);

  caption('1 · Mở lịch sử trao đổi nội bộ giữa các bot (thyca ⇄ searcher)');
  await clickEl(document.querySelector('#message-list [data-exchange-partner="searcher"]'));
  await waitFor(() => document.querySelector('#exchange-modal')?.open);
  await sleep(2400);
  await clickEl(document.querySelector('#exchange-modal .dialog-close'));
  await sleep(450);

  caption('1 · Xem tiếp trao đổi nội bộ (thyca ⇄ march7th)');
  await clickEl(document.querySelector('#message-list [data-exchange-partner="march7th"]'));
  await waitFor(() => document.querySelector('#exchange-modal')?.open);
  await sleep(2200);
  await clickEl(document.querySelector('#exchange-modal .dialog-close'));
  await sleep(450);

  caption('2 · Bộ nhớ dài hạn: chỉ lưu khi chủ động bảo bot (memory_remember) hoặc khi nén ngữ cảnh');
  await typeComposer('Nhớ giùm quy trình: tối thứ Sáu hàng tuần đăng bản tin Discord về model mới, Searcher kiểm tra nguồn trước rồi March 7th biên tập nhé.');
  await clickEl(document.querySelector('#send-message'));
  await waitFor(() => !document.querySelector('#send-message').hidden && document.querySelector('#message-list').textContent.includes('memory_remember'), 12000, 'memory_remember reply');
  await sleep(1400);

  caption('2 · Màn Nhật ký / Bộ nhớ: ghi chú mới vừa lưu cạnh ghi chú trích từ Nén ngữ cảnh');
  await clickEl(document.querySelector('[data-page-link="memories"]'));
  await waitFor(() => ThycaRouter.currentPage() === 'memories' && document.querySelector('#memory-leaves').textContent.includes('memory_remember'), 12000, 'memories leaf');
  await sleep(2500);

  caption('3 · Hội thoại nhóm “Bản tin cộng đồng” — 3 bot phối hợp nối tiếp');
  await clickEl(document.querySelector('[data-page-link="chat"]'));
  await waitFor(() => ThycaRouter.currentPage() === 'chat', 12000, 'back to chat');
  const grpBtn = await waitFor(() => document.querySelector('[data-group-open="grp-community"]'), 12000, 'group button');
  await clickEl(grpBtn);
  await waitFor(() => document.querySelector('#chat-title')?.textContent.includes('Bản tin cộng đồng'), 12000, 'group title');
  await sleep(600);
  await typeComposer('@searcher bổ sung link tài liệu chính thức, rồi @march7th chốt bản đăng Discord gọn gàng nhé.');
  await clickEl(document.querySelector('#send-message'));
  await waitFor(() => !document.querySelector('#send-message').hidden && document.querySelectorAll('#message-list .message-assistant').length >= 3, 15000, 'group replies');
  await sleep(2200);

  caption('4 · Hồ sơ từng bot: model riêng, mức suy luận & mục Nâng cao (32k – 1M tokens)');
  await clickEl(document.querySelector('[data-page-link="profile"]'));
  await waitFor(() => ThycaRouter.currentPage() === 'profile');
  await clickEl(document.querySelector('#bot-searcher [data-action="select"]'));
  await waitFor(() => document.querySelector('#bot-advanced'));
  await sleep(900);
  await clickEl(document.querySelector('#bot-advanced summary'));
  await sleep(2000);

  caption('4 · Xưởng biểu cảm của march7th: thử nhịp làm việc & tùy biến nhân vật');
  await clickEl(document.querySelector('#bot-march7th [data-action="select"]'));
  await waitFor(() => document.querySelector('#open-studio'));
  await clickEl(document.querySelector('#open-studio'));
  await waitFor(() => document.querySelector('#expressions-dialog')?.open);
  await sleep(700);
  const thinkBtn = document.querySelector('#expression-grid button[data-expression="thinking"]');
  if (thinkBtn) { await clickEl(thinkBtn); await sleep(800); }
  const workBtn = document.querySelector('#expression-grid button[data-expression="working"]');
  if (workBtn) { await clickEl(workBtn); await sleep(800); }
  const doneBtn = document.querySelector('#lab-done');
  if (doneBtn) { await clickEl(doneBtn); await sleep(900); }
  await clickEl(document.querySelector('#expressions-dialog .dialog-close'));
  await sleep(400);

  caption('5 · Provider đa chuẩn: OpenAI, Anthropic Messages & Google Gemini');
  await clickEl(document.querySelector('[data-page-link="provider"]'));
  await waitFor(() => ThycaRouter.currentPage() === 'provider');
  await sleep(2000);

  caption('5 · Sử dụng & Trace chi tiết từng vòng gọi model, bot_send, tavily-search & nén ngữ cảnh');
  await clickEl(document.querySelector('[data-page-link="dashboard"]'));
  await waitFor(() => ThycaRouter.currentPage() === 'dashboard' && document.querySelector('[data-usage-view="trace"]'));
  await sleep(1100);
  await clickEl(document.querySelector('[data-usage-view="trace"]'));
  const firstTurn = await waitFor(() => document.querySelector('#trace-conversations button.tl-turn'));
  await sleep(700);
  await clickEl(firstTurn);
  await waitFor(() => !document.querySelector('#turn-detail-view').hidden);
  await sleep(1600);
  const detailScroll = document.querySelector('[data-page-workspace="dashboard"]');
  if (detailScroll) {
    detailScroll.scrollBy({ top: 340, behavior: 'smooth' });
    await sleep(1800);
  }

  caption('Thyca · Đội bot chạy trên máy bạn — rõ ràng, minh bạch, kiểm soát hoàn toàn');
  await clickEl(document.querySelector('[data-page-link="chat"]'));
  await waitFor(() => ThycaRouter.currentPage() === 'chat');
  await sleep(2000);
  window.__demoDone = { ok: true };
  } catch (err) {
    window.__demoDone = { ok: false, error: String(err && (err.stack || err)) };
  }
})();
"""


LIVE_CHOREOGRAPHY_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (fn, ms = 180000, label = 'condition') => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const val = fn();
      if (val) return val;
      await sleep(100);
    }
    throw new Error('timeout waiting for ' + label);
  };

  const style = document.createElement('style');
  style.textContent = `
    #demo-caption {
      position: fixed; left: 46%; top: 8px; transform: translateX(-50%);
      z-index: 99998; pointer-events: none;
      background: rgba(29, 36, 31, 0.88); color: #f8f5ec;
      padding: 5px 14px; border-radius: 999px;
      font: 500 12px/1.3 system-ui, -apple-system, sans-serif;
      box-shadow: 0 4px 14px rgba(0,0,0,.16);
      border: 1px solid rgba(255,255,255,.14);
      letter-spacing: 0.01em; transition: opacity .25s ease;
      max-width: 640px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    #demo-cursor {
      position: fixed; left: 640px; top: 360px; width: 20px; height: 20px;
      margin: -10px 0 0 -10px; border-radius: 50%; z-index: 99999;
      pointer-events: none;
      background: rgba(166, 90, 66, 0.28);
      border: 2px solid #a65a42;
      box-shadow: 0 2px 10px rgba(0,0,0,.22);
      transition: left .42s cubic-bezier(.22,1,.36,1), top .42s cubic-bezier(.22,1,.36,1), transform .14s ease;
    }
    #demo-cursor.click { transform: scale(0.72); background: rgba(166, 90, 66, 0.55); }
  `;
  document.head.appendChild(style);
  const captionEl = document.createElement('div');
  captionEl.id = 'demo-caption';
  document.body.appendChild(captionEl);
  const cursorEl = document.createElement('div');
  cursorEl.id = 'demo-cursor';
  document.body.appendChild(cursorEl);

  const caption = text => { captionEl.textContent = text; };
  const moveTo = async el => {
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    await sleep(180);
    const r = el.getBoundingClientRect();
    cursorEl.style.left = Math.round(r.left + r.width / 2) + 'px';
    cursorEl.style.top = Math.round(r.top + r.height / 2) + 'px';
    await sleep(460);
  };
  const clickEl = async el => {
    await moveTo(el);
    cursorEl.classList.add('click');
    await sleep(110);
    el.click();
    await sleep(140);
    cursorEl.classList.remove('click');
    await sleep(180);
  };
  const typeComposer = async text => {
    const box = await waitFor(() => document.querySelector('#message'), 10000, 'composer');
    await clickEl(box);
    box.focus();
    box.value = '';
    for (let i = 0; i < text.length; i += 4) {
      box.value += text.slice(i, i + 4);
      box.dispatchEvent(new Event('input', { bubbles: true }));
      await sleep(22);
    }
    await sleep(280);
  };
  const waitTurnDone = async (label, ms = 240000) => {
    await waitFor(() => document.querySelector('#send-message')?.hidden === true, 15000, label + ' start');
    await waitFor(() => document.querySelector('#send-message')?.hidden === false, ms, label + ' finish');
    const sc = document.querySelector('#conversation-scroll');
    if (sc) sc.scrollTop = sc.scrollHeight;
    await sleep(1800);
  };

  try {
    await waitFor(() => window.ThycaRouter?.currentPage() === 'chat' && document.querySelector('#bot-thyca') && document.querySelector('#bot-searcher') && document.querySelector('#bot-march7th'), 20000, 'chat ready');
    await clickEl(document.querySelector('#bot-thyca [data-action="select"]'));
    await sleep(900);
    const sc0 = document.querySelector('#conversation-scroll');
    if (sc0) sc0.scrollTop = sc0.scrollHeight;

    caption('Thyca Live · Đội 3 bot thật: thyca (điều phối), searcher (Tavily MCP), march7th (Discord #m7)');
    await sleep(1600);

    caption('1 · Giao việc live: top model LLM mạnh nhất hiện tại theo Artificial Analysis (AA) → Discord #m7');
    await typeComposer('Nhờ searcher tra nhanh bảng xếp hạng Artificial Analysis (AA) tìm top 3 model LLM mạnh nhất hiện tại (kèm điểm AA Intelligence Index và nguồn URL), rồi nhờ march7th biên tập thành bản tin 4–5 dòng thật gọn (bọc mọi URL trong dấu <...> để Discord không bung embed) và gửi vào kênh Discord m7 giúp mình nhé.');
    await clickEl(document.querySelector('#send-message'));

    await waitFor(() => document.querySelector('#send-message')?.hidden === true, 15000, 'scene1 start');
    const waitDelegateOrDone = async (botId, ms = 120000) => {
      const end = Date.now() + ms;
      while (Date.now() < end) {
        const card = document.querySelector(`#bot-${botId}`);
        const st = card?.dataset?.state || '';
        if (['thinking', 'working', 'writing'].includes(st)) return 'active';
        if (document.querySelector('#send-message')?.hidden === false) return 'done';
        await sleep(120);
      }
      return 'timeout';
    };

    const waitExchangeLoaded = async () => {
      await waitFor(() => document.querySelector('#exchange-modal')?.open && !document.querySelector('#exchange-modal-body .exchange-loading'), 8000, 'exchange body loaded');
      await sleep(300);
    };

    const sState = await waitDelegateOrDone('searcher', 90000);
    if (sState === 'active') {
      caption('1 · thyca giao task → chuyển qua xem searcher đang tra bảng xếp hạng AA');
      await sleep(700);
      await clickEl(document.querySelector('#bot-searcher [data-action="select"]'));
      await sleep(400);
      const scS = document.querySelector('#conversation-scroll');
      if (scS) scS.scrollTop = scS.scrollHeight;
      await sleep(2000);
      await clickEl(document.querySelector('#bot-thyca [data-action="select"]'));
      await sleep(500);
    }

    const mState = await waitDelegateOrDone('march7th', 140000);
    if (mState === 'active') {
      caption('1 · thyca chuyển kết quả sang march7th → qua xem march7th soạn & gửi Discord #m7');
      await sleep(700);
      await clickEl(document.querySelector('#bot-march7th [data-action="select"]'));
      await sleep(400);
      const scM = document.querySelector('#conversation-scroll');
      if (scM) scM.scrollTop = scM.scrollHeight;
      await sleep(2000);
      await clickEl(document.querySelector('#bot-thyca [data-action="select"]'));
      await sleep(500);
    }

    await waitFor(() => document.querySelector('#send-message')?.hidden === false, 260000, 'scene1 finish');
    const sc1 = document.querySelector('#conversation-scroll');
    if (sc1) sc1.scrollTop = sc1.scrollHeight;
    await sleep(1500);

    for (const partner of ['searcher', 'march7th']) {
      const exBtn = [...document.querySelectorAll(`#message-list [data-exchange-partner="${partner}"]`)].at(-1);
      if (exBtn) {
        caption(`1 · Mở trao đổi nội bộ (thyca ⇄ ${partner}) — tự cuộn tới tin mới nhất`);
        await clickEl(exBtn);
        await waitExchangeLoaded();
        await sleep(2600);
        await clickEl(document.querySelector('#exchange-modal .dialog-close'));
        await sleep(450);
      }
      const partnerCard = document.querySelector(`#bot-${partner} [data-action="select"]`);
      if (partnerCard) {
        caption(`1 · Chuyển sang không gian của ${partner} (agent được giao task)`);
        await clickEl(partnerCard);
        await sleep(700);
        const scP = document.querySelector('#conversation-scroll');
        if (scP) scP.scrollTop = scP.scrollHeight;
        await sleep(1200);
        const peerEx = [...document.querySelectorAll('#message-list [data-exchange-partner="thyca"]')].at(-1);
        if (peerEx) {
          await clickEl(peerEx);
          await waitExchangeLoaded();
          await sleep(1800);
          await clickEl(document.querySelector('#exchange-modal .dialog-close'));
          await sleep(400);
        }
        await clickEl(document.querySelector('#bot-thyca [data-action="select"]'));
        await sleep(600);
      }
    }

    caption('2 · Bộ nhớ dài hạn: chủ động bảo thyca lưu quy trình qua memory_remember');
    await typeComposer('Hãy dùng memory_remember lưu lại quy trình: mỗi tối thứ Sáu cập nhật bảng xếp hạng top LLM theo Artificial Analysis (AA) vào kênh Discord m7, luôn bọc URL trong <...> để không bung embed.');
    await clickEl(document.querySelector('#send-message'));
    await waitTurnDone('scene2 memory turn', 120000);

    caption('2 · Mở màn Nhật ký / Bộ nhớ xem ghi chú thật vừa lưu & ghi chú từ Nén ngữ cảnh');
    await clickEl(document.querySelector('[data-page-link="memories"]'));
    await waitFor(() => ThycaRouter.currentPage() === 'memories' && document.querySelector('#memory-loading')?.hidden === true, 15000, 'memories loaded');
    await sleep(2400);

    caption('3 · Hội thoại nhóm 3 bot thật: searcher & march7th phối hợp trực tiếp');
    await clickEl(document.querySelector('[data-page-link="chat"]'));
    await waitFor(() => ThycaRouter.currentPage() === 'chat', 10000, 'chat return');
    const createGroupBtn = document.querySelector('#team-shared-create');
    if (createGroupBtn) {
      await clickEl(createGroupBtn);
      await waitFor(() => document.querySelector('#group-dialog')?.open, 8000, 'group dialog');
      const nameInput = document.querySelector('#group-name');
      if (nameInput) {
        nameInput.value = 'Top LLM theo AA · m7';
        nameInput.dispatchEvent(new Event('input', { bubbles: true }));
      }
      await sleep(400);
      await clickEl(document.querySelector('#group-form button[type="submit"]'));
      await waitFor(() => !document.querySelector('#group-dialog')?.open && document.querySelector('#chat-title')?.textContent.includes('Top LLM theo AA · m7'), 12000, 'group opened');
      await sleep(600);
      await typeComposer('@searcher chốt nhanh 1 câu model đang dẫn đầu bảng Artificial Analysis (AA) có ưu thế gì nhất, rồi @march7th xác nhận bài đã gửi lên kênh m7 nhé.');
      await clickEl(document.querySelector('#send-message'));
      await waitTurnDone('scene3 group turn', 200000);
      caption('3 · Chuyển từ Hội thoại nhóm về lại DM của thyca trực tiếp ở sidebar');
      await clickEl(document.querySelector('#bot-thyca [data-action="select"]'));
      await waitFor(() => document.querySelector('#chat-title')?.textContent.toLowerCase().includes('thyca'), 10000, 'back to thyca DM');
      await sleep(900);
    }

    caption('4 · Hồ sơ từng bot: cấu hình model riêng, mục Nâng cao (32k–1M tokens) & Xưởng biểu cảm');
    await clickEl(document.querySelector('[data-page-link="profile"]'));
    await waitFor(() => ThycaRouter.currentPage() === 'profile', 10000, 'profile page');
    await clickEl(document.querySelector('#bot-searcher [data-action="select"]'));
    await waitFor(() => document.querySelector('#bot-advanced'), 10000, 'searcher advanced');
    await sleep(700);
    await clickEl(document.querySelector('#bot-advanced summary'));
    await sleep(1800);
    await clickEl(document.querySelector('#bot-march7th [data-action="select"]'));
    await waitFor(() => document.querySelector('#open-studio'), 10000, 'studio btn');
    await clickEl(document.querySelector('#open-studio'));
    await waitFor(() => document.querySelector('#expressions-dialog')?.open, 8000, 'studio open');
    await sleep(600);
    for (const sel of ['#expression-grid button[data-expression="thinking"]', '#expression-grid button[data-expression="working"]', '#lab-done']) {
      const btn = document.querySelector(sel);
      if (btn) { await clickEl(btn); await sleep(700); }
    }
    await clickEl(document.querySelector('#expressions-dialog .dialog-close'));
    await sleep(400);

    caption('5 · Sử dụng & Trace thật: toàn bộ vòng gọi model, tool call, token & độ trễ thực tế');
    await clickEl(document.querySelector('[data-page-link="dashboard"]'));
    await waitFor(() => ThycaRouter.currentPage() === 'dashboard' && document.querySelector('[data-usage-view="trace"]'), 12000, 'dashboard');
    await sleep(1200);
    await clickEl(document.querySelector('[data-usage-view="trace"]'));
    const firstTurn = await waitFor(() => document.querySelector('#trace-conversations button.tl-turn'), 15000, 'trace list');
    await sleep(700);
    await clickEl(firstTurn);
    await waitFor(() => !document.querySelector('#turn-detail-view').hidden, 12000, 'trace detail');
    await sleep(1600);
    const detailScroll = document.querySelector('[data-page-workspace="dashboard"]');
    if (detailScroll) {
      detailScroll.scrollBy({ top: 360, behavior: 'smooth' });
      await sleep(1800);
    }

    caption('Thyca · Đội bot chạy thật trên máy bạn — rõ ràng, minh bạch, kiểm soát hoàn toàn');
    await clickEl(document.querySelector('[data-page-link="chat"]'));
    await waitFor(() => ThycaRouter.currentPage() === 'chat', 10000, 'final chat');
    await sleep(2000);
    window.__demoDone = { ok: true };
  } catch (err) {
    window.__demoDone = { ok: false, error: String(err && (err.stack || err)) };
  }
})();
"""


DISCORD_TAB_CHOREOGRAPHY_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const style = document.createElement('style');
  style.id = 'thyca-demo-discord-style';
  style.textContent = `
    #thyca-demo-discord-caption {
      position: fixed; left: 50%; top: 10px; transform: translateX(-50%);
      z-index: 99999; pointer-events: none;
      background: rgba(29, 36, 31, 0.92); color: #f8f5ec;
      padding: 6px 16px; border-radius: 999px;
      font: 500 13px/1.35 system-ui, -apple-system, sans-serif;
      box-shadow: 0 6px 20px rgba(0,0,0,.28);
      border: 1px solid rgba(255,255,255,.16);
      white-space: nowrap;
    }
  `;
  document.head.appendChild(style);
  const cap = document.createElement('div');
  cap.id = 'thyca-demo-discord-caption';
  cap.textContent = '6 · Kết quả thực tế trên Discord #🤖｜bé-bảy (march7th vừa gửi)';
  document.body.appendChild(cap);

  await sleep(600);
  const msgs = [...document.querySelectorAll('li[id^="chat-messages-"]')];
  const latest = msgs.at(-1);
  if (latest) {
    latest.scrollIntoView({ block: 'start', behavior: 'smooth' });
    await sleep(700);
    const scroller = latest.closest('[class*="scroller"]');
    if (scroller) scroller.scrollTop = Math.max(0, scroller.scrollTop - 48);
  }
  await sleep(4200);
  cap.remove();
  style.remove();
})();
"""


def _capture_discord_still(mode: str, out_png: Path) -> bool:
    if not shutil.which("spectacle"):
        return False
    flag = "-a" if mode == "active" else "-m"
    cmd = ["spectacle", "-b", "-n", flag, "-e", "-S", "-o", str(out_png)]
    res = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 1000:
        return True
    if flag == "-a":
        fallback = ["spectacle", "-b", "-n", "-m", "-o", str(out_png)]
        res2 = subprocess.run(
            fallback, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return res2.returncode == 0 and out_png.is_file() and out_png.stat().st_size > 1000
    return False


def _prepare_discord_frame(src_png: Path, out_jpg: Path) -> bool:
    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src_png),
            "-vf",
            (
                "scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,"
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=#1e1f22"
            ),
            "-q:v",
            "2",
            str(out_jpg),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return res.returncode == 0 and out_jpg.is_file()


def _encode_video(
    frames: list[tuple[float, Path]],
    output: Path,
    frames_dir: Path,
    *,
    max_gap: float = 2.0,
    ending_still: tuple[Path, float] | None = None,
) -> None:
    if len(frames) < 2:
        raise RuntimeError(f"Too few frames captured ({len(frames)})")
    concat_path = frames_dir / "frames.txt"
    lines: list[str] = []
    for idx, (ts, frame_path) in enumerate(frames):
        dur = max(0.016, min(frames[idx + 1][0] - ts, max_gap)) if idx + 1 < len(frames) else 1.0
        lines.append(f"file '{frame_path.as_posix()}'")
        lines.append(f"duration {dur:.4f}")
    if ending_still is not None:
        still_path, still_dur = ending_still
        lines.append(f"file '{still_path.as_posix()}'")
        lines.append(f"duration {still_dur:.4f}")
        lines.append(f"file '{still_path.as_posix()}'")
    else:
        lines.append(f"file '{frames[-1][1].as_posix()}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-vf",
            "scale=1280:720:flags=lanczos",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "cfr",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "24",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({res.returncode}): {res.stderr[-800:]}")


def record(
    output: Path,
    *,
    live_url: str | None = None,
    verify_dir: Path | None = None,
    discord_capture: str | None = None,
    cdp_port: int | None = None,
) -> dict:
    server: ThreadingHTTPServer | None = None
    srv_thread: threading.Thread | None = None
    if live_url:
        target_url = live_url.rstrip("/") + "/"
        script = LIVE_CHOREOGRAPHY_JS
        eval_timeout = 600.0
        max_gap = 1.6
    else:
        state = DemoState()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()
        target_url = f"http://127.0.0.1:{server.server_port}/"
        script = DEMO_CHOREOGRAPHY_JS
        eval_timeout = 120.0
        max_gap = 2.5

    with tempfile.TemporaryDirectory(prefix="thyca-demo-") as tmp_str:
        tmp = Path(tmp_str)
        profile_dir = tmp / "chrome-profile"
        frames_dir = tmp / "frames"
        frames_dir.mkdir(parents=True)

        proc: subprocess.Popen | None = None
        if cdp_port is not None:
            debug_port = cdp_port
        else:
            chrome = _find_chrome()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                debug_port = sock.getsockname()[1]
            proc = subprocess.Popen(
                [
                    chrome,
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-gpu-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-background-timer-throttling",
                    "--disable-renderer-backgrounding",
                    "--window-size=1280,720",
                    f"--remote-debugging-port={debug_port}",
                    f"--user-data-dir={profile_dir}",
                    "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        session: CDPSession | None = None
        discord_session: CDPSession | None = None
        try:
            deadline = time.monotonic() + 30.0
            pages: list[dict] = []
            while True:
                if proc is not None and proc.poll() is not None:
                    raise RuntimeError(f"Chrome exited early ({proc.returncode})")
                try:
                    with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=3) as resp:
                        pages = json.loads(resp.read().decode("utf-8"))
                    thyca_page = next(
                        (
                            p
                            for p in pages
                            if p.get("type") == "page"
                            and (
                                "127.0.0.1:19502" in (p.get("url") or "")
                                or p.get("url") == "about:blank"
                            )
                        ),
                        None,
                    )
                    if thyca_page is None:
                        thyca_page = next(
                            (
                                p
                                for p in pages
                                if p.get("type") == "page"
                                and "discord.com" not in (p.get("url") or "")
                            ),
                            None,
                        )
                    if thyca_page and thyca_page.get("webSocketDebuggerUrl"):
                        ws_url = thyca_page["webSocketDebuggerUrl"]
                        break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Timed out waiting for Chrome DevTools")
                time.sleep(0.15)

            session = CDPSession(ws_url, frames_dir)
            session.call("Page.enable")
            session.call("Runtime.enable")
            session.call("Page.bringToFront")
            session.call(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False},
            )
            session.call("Page.navigate", {"url": target_url})
            time.sleep(1.5)

            session.start_recording()
            started = time.monotonic()
            session.call(
                "Runtime.evaluate",
                {"expression": script, "awaitPromise": True},
                timeout=eval_timeout,
            )
            elapsed = time.monotonic() - started
            session.stop_recording()

            check = session.call(
                "Runtime.evaluate",
                {"expression": "JSON.stringify(window.__demoDone || null)", "returnByValue": True},
            )
            done_val = check.get("result", {}).get("result", {}).get("value")
            if done_val != '{"ok":true}':
                raise RuntimeError(f"Demo choreography did not complete cleanly: {done_val}")

            session.call(
                "Runtime.evaluate",
                {
                    "expression": (
                        "document.querySelector('#demo-caption')?.remove();"
                        "document.querySelector('#demo-cursor')?.remove();"
                    )
                },
            )

            all_frames = list(session.frames)
            ending_still: tuple[Path, float] | None = None
            discord_captured = False

            if cdp_port is not None:
                with urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=5) as resp:
                    cur_pages = json.loads(resp.read().decode("utf-8"))
                discord_page = next(
                    (
                        p
                        for p in cur_pages
                        if p.get("type") == "page"
                        and "discord.com" in (p.get("url") or "")
                        and p.get("webSocketDebuggerUrl")
                    ),
                    None,
                )
                if discord_page is not None:
                    discord_frames_dir = tmp / "discord-frames"
                    discord_frames_dir.mkdir(parents=True, exist_ok=True)
                    discord_session = CDPSession(
                        discord_page["webSocketDebuggerUrl"], discord_frames_dir
                    )
                    discord_session.call("Page.enable")
                    discord_session.call("Runtime.enable")
                    discord_session.call("Page.bringToFront")
                    discord_session.call(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False},
                    )
                    time.sleep(0.6)
                    discord_session.start_recording()
                    discord_session.call(
                        "Runtime.evaluate",
                        {"expression": DISCORD_TAB_CHOREOGRAPHY_JS, "awaitPromise": True},
                        timeout=30.0,
                    )
                    discord_session.stop_recording()
                    if discord_session.frames:
                        all_frames.extend(discord_session.frames)
                        discord_captured = True
                        if verify_dir is not None:
                            verify_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(
                                discord_session.frames[-1][1], verify_dir / "discord-ending.jpg"
                            )
            elif discord_capture:
                raw_png = tmp / "discord-raw.png"
                fitted_jpg = frames_dir / "discord_ending.jpg"
                if _capture_discord_still(discord_capture, raw_png) and _prepare_discord_frame(
                    raw_png, fitted_jpg
                ):
                    ending_still = (fitted_jpg, 5.0)
                    discord_captured = True
                    if verify_dir is not None:
                        verify_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(fitted_jpg, verify_dir / "discord-ending.jpg")

            _encode_video(
                all_frames,
                output,
                frames_dir,
                max_gap=max_gap,
                ending_still=ending_still,
            )
            if verify_dir is not None:
                verify_dir.mkdir(parents=True, exist_ok=True)
                total = len(session.frames)
                for idx, frac in enumerate((0.10, 0.30, 0.55, 0.78, 0.92), start=1):
                    src = session.frames[min(total - 1, int(total * frac))][1]
                    shutil.copy2(src, verify_dir / f"live-frame-{idx}.jpg")
            size_kb = round(output.stat().st_size / 1024, 1)
            return {
                "output": str(output),
                "mode": "live" if live_url else "demo",
                "frames": len(all_frames),
                "elapsed_s": round(elapsed, 1),
                "size_kb": size_kb,
                "discord_captured": discord_captured,
            }
        finally:
            if discord_session is not None:
                discord_session.close()
            if session is not None:
                session.close()
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if server is not None:
                server.shutdown()
                server.server_close()
            if srv_thread is not None:
                srv_thread.join(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Thyca WebUI demo video")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--live-url", type=str, default=None)
    parser.add_argument("--verify-dir", type=Path, default=None)
    parser.add_argument("--discord-capture", choices=("active", "monitor"), default=None)
    parser.add_argument("--cdp-port", type=int, default=None)
    args = parser.parse_args()
    result = record(
        args.output,
        live_url=args.live_url,
        verify_dir=args.verify_dir,
        discord_capture=args.discord_capture,
        cdp_port=args.cdp_port,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
