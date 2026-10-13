"""Compaction summary prompt policy: build summarizer input, accept output.

Mirrors ``title.py``: the packaged ``compaction.md`` is the only
instruction source (missing/blank means skip the model call and let the
caller fall back to the rule-based excerpt). Summarizer input is
user/assistant text plus tool names only — raw tool bodies are bulky,
often re-runnable, and would dominate the summary budget.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from thyca.core.protocol import Message

ChatFn = Callable[[list[Message], list | None], Awaitable]

SUMMARY_MAX_CHARS = 4000
SUMMARY_INPUT_MAX_CHARS = 120_000
INSTRUCTIONS_MAX_CHARS = 500

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "seeds" / "prompts"
_INSTRUCTION_CACHE: str | None = None
_TRUNCATED = "[... earliest text truncated for summarizer input ...]\n"


def summary_instruction() -> str | None:
    """Standalone compaction system message, or None to skip the call."""
    global _INSTRUCTION_CACHE
    if _INSTRUCTION_CACHE is not None:
        return _INSTRUCTION_CACHE
    try:
        text = (_PROMPTS_DIR / "compaction.md").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    _INSTRUCTION_CACHE = text
    return text


def _tool_hint(call: object) -> str:
    name = getattr(call, "name", "") or "tool"
    arguments = getattr(call, "arguments", None)
    if isinstance(arguments, dict):
        for key in ("path", "file", "filename"):
            value = arguments.get(key)
            if isinstance(value, str) and value and len(value) <= 200:
                return f"{name}({value})"
    return str(name)


def _conversation_text(fresh: list[Message]) -> str:
    lines: list[str] = []
    for message in fresh:
        if message.role == "user" and message.content:
            lines.append(f"User: {message.content}")
        elif message.role == "assistant":
            if message.content:
                lines.append(f"Assistant: {message.content}")
            if message.tool_calls:
                hints = ", ".join(_tool_hint(call) for call in message.tool_calls)
                lines.append(f"Assistant used tools: {hints}")
        # Tool results and system rows never enter the summarizer input.
    return "\n".join(lines)


def omitted_text(fresh: list[Message], prior_text: str = "") -> str:
    """Summarizer input: prior summary block + capped conversation text.

    The prior body keeps its own space (it is already capped upstream);
    the conversation keeps its tail when over budget, since the newest
    omitted turns sit closest to the kept tail and matter most for
    continuity.
    """
    prior_block = f"Prior summary to merge:\n{prior_text}\n\n" if prior_text else ""
    conversation = _conversation_text(fresh)
    budget = SUMMARY_INPUT_MAX_CHARS - len(prior_block)
    if budget <= len(_TRUNCATED):
        conversation = ""
    elif len(conversation) > budget:
        keep = conversation[-(budget - len(_TRUNCATED)):]
        if keep and "\udc00" <= keep[0] <= "\udfff":
            keep = keep[1:]
        conversation = _TRUNCATED + keep
    return prior_block + conversation


def summary_messages(
    fresh: list[Message],
    prior_text: str = "",
    instructions: str | None = None,
) -> list[Message] | None:
    instruction = summary_instruction()
    if instruction is None:
        return None
    body = omitted_text(fresh, prior_text)
    if not body.strip():
        return None
    focus = (instructions or "").strip()[:INSTRUCTIONS_MAX_CHARS]
    if focus:
        body += f"\n\nUser focus for what comes next: {focus}"
    return [
        Message(role="system", content=instruction),
        Message(role="user", content=body),
    ]


def accept_summary(raw: object) -> str | None:
    """Cleaned summary capped for the marker, or None when unusable."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS].rstrip()
        if text and "\ud800" <= text[-1] <= "\udbff":
            text = text[:-1].rstrip()
    return text or None


async def propose_summary(
    chat: ChatFn,
    fresh: list[Message],
    prior_text: str = "",
    instructions: str | None = None,
) -> str | None:
    prompt = summary_messages(fresh, prior_text, instructions)
    if prompt is None:
        return None
    reply = await chat(prompt, None)
    return accept_summary(getattr(reply, "content", None) or "")
