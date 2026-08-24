"""Name untitled or unusable ~/.thyca sessions with notebook titles."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from thyca.config import load
from thyca.llm.llm_factory import ConnectFactory
from thyca.sessions import SessionManager
from thyca.sessions.title import retitle_missing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Đặt tiêu đề sổ tay cho phiên chưa có title dùng được")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.home() / ".thyca",
        help="thyca data dir (default: ~/.thyca)",
    )
    return parser


async def run(root: Path) -> int:
    cfg = load(root / "config.json")
    manager = SessionManager(
        root / "sessions",
        limits=cfg.limits,
        timezone_name=cfg.timeline.timezone,
    )
    connect = ConnectFactory.create("openai_chat", cfg.provider)
    try:
        named = await retitle_missing(connect.chat, manager)
    finally:
        close = getattr(connect, "aclose", None)
        if close is not None:
            await close()
    for session, old, title in named:
        print(f"{session.id}  {old} → {title}")
    print(f"{len(named)} phiên")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args.root))


if __name__ == "__main__":
    sys.exit(main())
