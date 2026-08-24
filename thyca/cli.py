"""CLI — REPL, one-shot -p, session flags. TASK-317."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from thyca.agent.act import Act
from thyca.agent.assemble import Assemble
from thyca.agent.loop import AgentLoop
from thyca.agent.observe import Observe
from thyca.agent.think import LLMPort, Think
from thyca.chat_ui import ChatUi
from thyca.config import ConfigError, load
from thyca.llm.llm_factory import ConnectFactory
from thyca.llm.llm_base import LLMError
from thyca.llm.prompt_manager import PromptManager
from thyca.memory.active import ActiveMemory
from thyca.sessions import SessionError, SessionManager, SessionNotFound
from thyca.tools.builtin import register_file_tools
from thyca.tools.memory import MemoryFacade
from thyca.tools.memory_tools import register_memory_tools
from thyca.tools.path_guard import PathGuard
from thyca.tools.registry import ToolRegistry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thyca", description="Thyca — personal terminal harness")
    parser.add_argument("--version", action="store_true", help="show version and exit")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="one-shot print mode")
    parser.add_argument("--continue", dest="cont", action="store_true", help="continue last session")
    parser.add_argument("--session", type=str, default=None, help="session id")
    parser.add_argument("--model", type=str, default=None, help="override model for this request")
    parser.add_argument("--debug", action="store_true", help="print prompt/session diagnostics on stderr")
    parser.add_argument("--serve", action="store_true", help="serve webui + memory stats on 127.0.0.1")
    parser.add_argument("--daemon", action="store_true", help="detach --serve from the terminal")
    parser.add_argument("--stop", action="store_true", help="stop a background --serve")
    parser.add_argument("--port", type=int, default=8765, help="port for --serve (default 8765)")
    parser.add_argument("prompt", nargs="*", help="prompt for -p mode")
    return parser


class Cli:
    def __init__(
        self,
        *,
        thyca_dir: Path | None = None,
        connect: LLMPort | None = None,
        stdin=None,
        stdout=None,
        stderr=None,
    ) -> None:
        self._thyca_dir = thyca_dir
        self._connect = connect
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr

    def main(self, argv: list[str] | None = None) -> int:
        args = build_parser().parse_args(argv)
        ui = ChatUi(self._stdout, self._stderr, color=False)
        if args.version:
            from thyca import __version__

            print(f"thyca {__version__}", file=self._stdout)
            return 0
        if args.cont and args.session:
            ui.error("--continue and --session are mutually exclusive")
            return 2
        if args.serve and (args.print_mode or args.cont or args.session or args.prompt):
            ui.error("--serve cannot combine with -p/--continue/--session/prompt")
            return 2
        if args.daemon and not args.serve:
            ui.error("--daemon requires --serve")
            return 2
        if args.stop and not args.serve:
            ui.error("--stop requires --serve")
            return 2
        if args.daemon and args.stop:
            ui.error("--daemon and --stop are mutually exclusive")
            return 2
        if args.serve:
            if args.port < 1 or args.port > 65535:
                ui.error("--port must be 1..65535")
                return 2
            return self._serve(args.port, daemon=args.daemon, stop=args.stop)
        if args.prompt and not args.print_mode:
            ui.error("prompt requires -p")
            return 2
        if args.print_mode and not args.prompt:
            ui.error("-p requires a prompt")
            return 2
        if args.model is not None and not args.model.strip():
            ui.error("--model must be non-empty")
            return 2
        try:
            return asyncio.run(self._run(args))
        except KeyboardInterrupt:
            print(file=self._stdout)
            return 0

    async def _run(self, args: argparse.Namespace) -> int:
        root = self._thyca_dir if self._thyca_dir is not None else Path.home() / ".thyca"
        ui = ChatUi(self._stdout, self._stderr)
        try:
            cfg = load(root / "config.json")
        except ConfigError as exc:
            ui.error(str(exc))
            return 1

        provider = replace(cfg.provider, model=args.model) if args.model else cfg.provider
        sessions = SessionManager(
            root / "sessions",
            limits=cfg.limits,
            timezone_name=cfg.timeline.timezone,
        )
        try:
            self._open_session(sessions, args)
        except SessionNotFound as exc:
            ui.error(str(exc))
            return 1
        except SessionError as exc:
            ui.error(str(exc))
            return 1

        memory = ActiveMemory(
            root,
            tail_kb=cfg.limits.hotTailKB,
            timezone_name=cfg.timeline.timezone,
        )
        zone = ZoneInfo(cfg.timeline.timezone)
        state = memory.open_session(datetime.now(zone))
        connect = self._connect or ConnectFactory.create("openai_chat", provider)
        registry = ToolRegistry()
        register_file_tools(registry, PathGuard(root))
        register_memory_tools(
            registry, MemoryFacade(root, timezone_name=cfg.timeline.timezone)
        )
        schema = registry.to_openai_schema()
        loop = AgentLoop(
            sessions=sessions,
            assemble=Assemble(),
            think=Think(connect),
            act=Act(registry),
            observe=Observe(sessions),
            loop_max=cfg.limits.loopMax,
            tools=schema,
        )

        prompts = PromptManager()

        async def turn(text: str) -> str:
            hot = memory.refresh(state, datetime.now(zone))
            if args.debug:
                system = prompts.build(hot)
                ui.debug(
                    f"session={sessions.current.id} model={provider.model} "
                    f"identity={('Name: Thyca' in system)} soul={('You are Thyca' in system)} "
                    f"user={'<user>' in system} tools={len(schema)} system_chars={len(system)}"
                )
            return await loop.run(text, hot=hot)

        try:
            if args.print_mode:
                return await self._oneshot(" ".join(args.prompt), turn, ui)
            ui.banner(sessions.current.id, provider.model)
            return await self._repl(turn, ui)
        finally:
            close = getattr(connect, "aclose", None)
            if close is not None:
                await close()

    def _serve(self, port: int, *, daemon: bool = False, stop: bool = False) -> int:
        from thyca.chat_app import ChatApp
        from thyca.serve import ServeError, default_webui, run
        from thyca.serve_daemon import daemonize, log_file, stop_daemon

        root = self._thyca_dir if self._thyca_dir is not None else Path.home() / ".thyca"
        ui = ChatUi(self._stdout, self._stderr, color=False)
        if stop:
            try:
                pid = stop_daemon(root)
            except ServeError as exc:
                ui.error(str(exc))
                return 1
            print(f"stopped {pid}", file=self._stdout)
            return 0
        try:
            cfg = load(root / "config.json")
        except ConfigError as exc:
            ui.error(str(exc))
            return 1
        if daemon:
            print(f"http://127.0.0.1:{port}/", file=self._stdout, flush=True)
            print(f"log {log_file(root)}", file=self._stdout, flush=True)
            try:
                daemonize(root)
            except ServeError as exc:
                ui.error(str(exc))
                return 1
        try:
            run(
                host="127.0.0.1",
                port=port,
                webui=default_webui(),
                facade=MemoryFacade(root, timezone_name=cfg.timeline.timezone),
                chat=ChatApp(root, cfg, connect=self._connect),
                stdout=self._stdout,
            )
        except ServeError as exc:
            ui.error(str(exc))
            return 1
        except OSError as exc:
            ui.error(str(exc))
            return 1
        return 0

    def _open_session(self, sessions: SessionManager, args: argparse.Namespace) -> None:
        if args.session:
            sessions.load(args.session)
        elif args.cont:
            sessions.continue_last()
        else:
            sessions.create()

    async def _oneshot(self, prompt: str, turn, ui: ChatUi) -> int:
        try:
            print(await turn(prompt), file=self._stdout)
            return 0
        except (LLMError, ConfigError, SessionError) as exc:
            ui.error(str(exc))
            return 1

    async def _repl(self, turn, ui: ChatUi) -> int:
        while True:
            ui.prompt()
            line = self._stdin.readline()
            if line == "":
                ui.goodbye()
                return 0
            text = line.strip()
            if not text:
                continue
            try:
                ui.assistant(await turn(text))
            except (LLMError, ConfigError, SessionError) as exc:
                ui.error(str(exc))


def main(argv: list[str] | None = None) -> int:
    return Cli().main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
