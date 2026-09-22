from __future__ import annotations

import sys

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_CYAN = "\033[36m"


class ChatUi:
    """Minimal terminal chat surface. No TUI framework."""

    def __init__(self, stdout=None, stderr=None, *, color: bool | None = None) -> None:
        self._out = stdout if stdout is not None else sys.stdout
        self._err = stderr if stderr is not None else sys.stderr
        if color is None:
            color = bool(getattr(self._out, "isatty", lambda: False)())
        self._color = color

    def banner(self, session_id: str, model: str) -> None:
        line = f"thyca  session {session_id}  model {model}"
        print(self._paint(_DIM, line), file=self._out)

    def prompt(self) -> None:
        print(self._paint(_CYAN, "you> "), end="", file=self._out, flush=True)

    def assistant(self, text: str) -> None:
        label = self._paint(_BOLD, "thyca")
        print(f"\n{label}\n{text}\n", file=self._out)

    def error(self, text: str) -> None:
        print(self._paint(_RED, f"thyca: {text}"), file=self._err)

    def debug(self, text: str) -> None:
        print(self._paint(_DIM, f"debug {text}"), file=self._err)

    def goodbye(self) -> None:
        print(file=self._out)

    def _paint(self, code: str, text: str) -> str:
        if not self._color:
            return text
        return f"{code}{text}{_RESET}"
