"""CLI — REPL, one-shot -p, session flags. TASK-317."""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from thyca.agent.think import LLMPort
from thyca.app.chat_ui import ChatUi
from thyca.app.turn_options import InvalidTurnOption
from thyca.config import ConfigError, load
from thyca.llm.llm_base import LLMError
from thyca.memory.facade import MemoryFacade
from thyca.sessions import SessionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thyca", description="Thyca — personal terminal harness")
    parser.add_argument("--version", action="store_true", help="show version and exit")
    parser.add_argument("--seed", action="store_true", help="seed ~/.thyca defaults (copy-if-missing) and exit")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="one-shot print mode")
    parser.add_argument("--continue", dest="cont", action="store_true", help="continue last session")
    parser.add_argument("--session", type=str, default=None, help="session id")
    parser.add_argument("--model", type=str, default=None, help="override model for this request")
    parser.add_argument("--debug", action="store_true", help="print prompt/session diagnostics on stderr")
    parser.add_argument("--serve", action="store_true", help="serve webui + memory stats on 127.0.0.1")
    parser.add_argument("--daemon", action="store_true", help="detach --serve from the terminal")
    parser.add_argument("--stop", action="store_true", help="stop a background --serve")
    parser.add_argument("--port", type=int, default=19502, help="port for --serve (default 19502)")
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
        if args.seed:
            return self._seed()
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
        from thyca.app.chat_app import ChatApp

        root = self._thyca_dir if self._thyca_dir is not None else Path.home() / ".thyca"
        ui = ChatUi(self._stdout, self._stderr)
        try:
            cfg = load(root / "config.json")
        except ConfigError as exc:
            ui.error(str(exc))
            return 1

        app = None
        try:
            app = ChatApp(root, cfg, connect=self._connect, stderr=self._stderr)
            if args.session:
                app.get_payload(args.session)
                session_id = args.session
            else:
                session_id = app.bot_dm("thyca")["id"]
            model = args.model.strip() if args.model else None
            banner_cfg = replace(cfg, defaultModel=model) if model else cfg
            provider = banner_cfg.effective_provider()

            def diagnostics(report: dict) -> None:
                system = report["system"]
                ui.debug(
                    f"session={report['session_id']} model={report['model']} "
                    f"provider={report['provider']} baseUrl={report['base_url']} "
                    f"identity={'</identity>' in system} soul={'</role>' in system} "
                    f"user={'</user>' in system} tools={len(report['tools'])} "
                    f"system_chars={len(system)}"
                )

            async def turn(text: str) -> str:
                detail = await asyncio.to_thread(
                    app.turn,
                    session_id,
                    text,
                    model=model,
                    diagnostic_sink=diagnostics if args.debug else None,
                )
                return detail["reply"]

            if args.print_mode:
                return await self._oneshot(" ".join(args.prompt), turn, ui)
            ui.banner(session_id, provider.model)
            return await self._repl(turn, ui)
        except (ConfigError, LLMError, SessionError, InvalidTurnOption) as exc:
            ui.error(str(exc))
            return 1
        finally:
            if app is not None:
                await asyncio.to_thread(app.shutdown)

    def _seed(self) -> int:
        from thyca.config import GUIDE_NAME, ConfigError, ensure_thyca_dir, write_config_guide
        from thyca.memory.active import ActiveMemory, ActiveMemoryError
        from thyca.skills import SkillStore

        # Install-time seeding: HOME-based ~/.thyca by design (self._thyca_dir
        # is a per-run override, not the install target). Every step below is
        # copy-if-missing, so reinstalls/upgrades never overwrite user edits.
        ui = ChatUi(self._stdout, self._stderr, color=False)
        try:
            root = ensure_thyca_dir()
            ActiveMemory().ensure_files()
            SkillStore().ensure_defaults()
            write_config_guide()
        except (ConfigError, ActiveMemoryError, OSError) as exc:
            ui.error(f"seed failed: {exc}")
            return 1
        print(
            f"seeded {root} (SOUL.md, IDENTITY.md, USER.md, skills, {GUIDE_NAME}; existing files kept)",
            file=self._stdout,
        )
        return 0

    def _serve(self, port: int, *, daemon: bool = False, stop: bool = False) -> int:
        from thyca.app.chat_app import ChatApp
        from thyca.serve import ServeError, default_webui, run
        from thyca.serve.daemon import (
            claim_server_lock,
            daemonize,
            log_file,
            running_pid,
            stop_daemon,
        )

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
        else:
            # One server per data dir. A foreground instance beside a
            # daemon (or another foreground) would share memory.sqlite:
            # both write the archive index, and the losing writer holds
            # its transaction open until every reader wedges. The daemon
            # path guards inside daemonize(); here the pidfile check
            # gives the friendly message and the flock is the hard stop.
            try:
                existing = running_pid(root)
                if existing is not None:
                    raise ServeError(f"already running (pid {existing})")
                # Held for this frame's lifetime, which spans run() below.
                _lock_fd = claim_server_lock(root)
            except ServeError as exc:
                ui.error(str(exc))
                return 1
        try:
            run(
                host="127.0.0.1",
                port=port,
                webui=default_webui(),
                facade=MemoryFacade(root, timezone_name=cfg.timeline.timezone),
                chat=ChatApp(root, cfg, connect=self._connect, stderr=self._stderr),
                stdout=self._stdout,
                config_file=root / "config.json",
            )
        except ServeError as exc:
            ui.error(str(exc))
            return 1
        except OSError as exc:
            ui.error(str(exc))
            return 1
        return 0

    async def _oneshot(self, prompt: str, turn, ui: ChatUi) -> int:
        try:
            print(await turn(prompt), file=self._stdout)
            return 0
        except (LLMError, ConfigError, SessionError, InvalidTurnOption) as exc:
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
            except (LLMError, ConfigError, SessionError, InvalidTurnOption) as exc:
                ui.error(str(exc))


def main(argv: list[str] | None = None) -> int:
    return Cli().main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
