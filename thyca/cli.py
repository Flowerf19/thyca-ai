"""CLI — minimal wiring for TASK-301 (TASK-302 owns config).

Full REPL/loop lives in services/agent-loop.md (later). This file only makes
`thyca --help` work and proves pyproject flat layout without src/.
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thyca", description="Thyca — personal terminal harness")
    p.add_argument("--version", action="store_true", help="show version and exit")
    p.add_argument("-p", "--print", dest="print_mode", action="store_true", help="one-shot print mode")
    p.add_argument("--continue", dest="cont", action="store_true", help="continue last session")
    p.add_argument("--session", type=str, default=None, help="session id")
    p.add_argument("--model", type=str, default=None, help="override model")
    p.add_argument("prompt", nargs="*", help="prompt for -p mode")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from thyca import __version__

        print(f"thyca {__version__}")
        return 0

    # Ensure config exists so first run doesn't surprise (TASK-302)
    try:
        from thyca.config import ensure_default

        ensure_default()
    except Exception as e:
        print(f"config init failed: {e}", file=sys.stderr)
        return 1

    # Bare `thyca` / `thyca --help` handled by argparse.
    # Full harness (loop, REPL, sessions) not yet — stub message.
    if args.print_mode:
        prompt = " ".join(args.prompt) if args.prompt else ""
        if not prompt:
            print("thyca: -p requires a prompt", file=sys.stderr)
            return 2
        print(f"thyca: harness not yet wired (prompt: {prompt!r}). See services/agent-loop.md.")
        print("hint: set OPENAI_API_KEY and run again after GOAL-002/004 land.")
        return 0

    # REPL stub — real loop in TASK-316/317
    print("thyca — config ready at ~/.thyca/config.json")
    print("hint: REPL + agent loop lands in services/agent-loop.md. Try: thyca -p \"ping\"")
    return 0
