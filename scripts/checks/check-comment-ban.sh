#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
import io
import re
import sys
import tokenize
from pathlib import Path

root = Path.cwd()
errors: list[str] = []

PY_ALLOWED = re.compile(
    r"^#\s*(?:"
    r"!|-\*-|"
    r"(?:INVARIANT|SECURITY|SAFETY):|"
    r"TODO\(#\d+\):|"
    r"(?:noqa:\s*[A-Z0-9,\s]+|type:\s*ignore\[[^\]]+\]|pragma:\s*no\s+cover)\s+(?:--\s*reason:\s*\S|reason\s*=\s*\"[^\"]+\")"
    r")"
)

JS_LINE_ALLOWED = re.compile(
    r"^//\s*(?:(?:INVARIANT|SECURITY|SAFETY):|TODO\(#\d+\):)"
)

BLOCK_ALLOWED = re.compile(
    r"^/\*(?:\*[\s\S]*\*/|\s*(?:(?:INVARIANT|SECURITY|SAFETY):|TODO\(#\d+\):)[\s\S]*\*/)$"
)


def check_python_file(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            text = tok.string.strip()
            if not PY_ALLOWED.match(text):
                errors.append(
                    f"{path.relative_to(root)}:{tok.start[0]}: disallowed comment {text[:60]!r} "
                    f"(root cause: explanatory/workaround comments violate comment ban; "
                    f"fix: delete comment, promote true invariant to INVARIANT:/SECURITY:/SAFETY:, or add -- reason: <why> to lint override)"
                )


def _prev_allows_regex(prev_char: str, prev_word: str) -> bool:
    if prev_word in {
        "return", "case", "throw", "typeof", "void", "delete",
        "in", "instanceof", "of", "new", "yield", "await", "else", "do",
    }:
        return True
    return prev_char in {"", "(", "[", "{", ",", ";", ":", "=", "!", "?", "&", "|", "^", "~", "+", "-", "*", "%", "<", ">"}


def iter_js_comments(src: str):
    i = 0
    n = len(src)
    line = 1
    prev_char = ""
    prev_word = ""
    template_expr_depth: list[int] = []
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    if i + 1 < n and src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            prev_char = quote
            prev_word = ""
            continue
        if ch == "`":
            i += 1
            while i < n:
                if src[i] == "\\":
                    if i + 1 < n and src[i + 1] == "\n":
                        line += 1
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    template_expr_depth.append(1)
                    i += 2
                    prev_char = "{"
                    prev_word = ""
                    break
                if src[i] == "`":
                    i += 1
                    prev_char = "`"
                    prev_word = ""
                    break
                i += 1
            continue
        if ch == "/" and nxt == "/":
            start = i
            start_line = line
            i += 2
            while i < n and src[i] != "\n":
                i += 1
            yield start_line, "line", src[start:i]
            continue
        if ch == "/" and nxt == "*":
            start = i
            start_line = line
            i += 2
            while i < n - 1 and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 2)
            yield start_line, "block", src[start:i]
            continue
        if ch == "/" and _prev_allows_regex(prev_char, prev_word):
            i += 1
            in_class = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "/" and not in_class:
                    i += 1
                    while i < n and src[i].isalpha():
                        i += 1
                    break
                elif src[i] == "\n":
                    line += 1
                    i += 1
                    break
                i += 1
            prev_char = "/"
            prev_word = ""
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch.isspace():
            i += 1
            continue
        if template_expr_depth:
            if ch == "{":
                template_expr_depth[-1] += 1
            elif ch == "}":
                template_expr_depth[-1] -= 1
                if template_expr_depth[-1] == 0:
                    template_expr_depth.pop()
                    i += 1
                    while i < n:
                        if src[i] == "\\":
                            if i + 1 < n and src[i + 1] == "\n":
                                line += 1
                            i += 2
                            continue
                        if src[i] == "\n":
                            line += 1
                        if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                            template_expr_depth.append(1)
                            i += 2
                            prev_char = "{"
                            prev_word = ""
                            break
                        if src[i] == "`":
                            i += 1
                            prev_char = "`"
                            prev_word = ""
                            break
                        i += 1
                    continue
        if ch.isalpha() or ch in ("_", "$"):
            start = i
            while i < n and (src[i].isalnum() or src[i] in ("_", "$")):
                i += 1
            prev_word = src[start:i]
            prev_char = prev_word[-1]
            continue
        prev_char = ch
        prev_word = ""
        i += 1


def iter_css_comments(src: str):
    i = 0
    n = len(src)
    line = 1
    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch == "/" and nxt == "*":
            start = i
            start_line = line
            i += 2
            while i < n - 1 and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 2)
            yield start_line, src[start:i]
            continue
        if ch == "\n":
            line += 1
        i += 1


for py in sorted(list((root / "thyca").rglob("*.py")) + list((root / "tests").rglob("*.py"))):
    check_python_file(py)

js_paths = sorted(
    list((root / "thyca/webui/ui").rglob("*.js"))
    + list((root / "tests").glob("*.mjs"))
)
for js in js_paths:
    src = js.read_text(encoding="utf-8")
    for lineno, kind, text in iter_js_comments(src):
        ok = JS_LINE_ALLOWED.match(text) if kind == "line" else BLOCK_ALLOWED.match(text)
        if not ok:
            errors.append(
                f"{js.relative_to(root)}:{lineno}: disallowed JS comment {text.splitlines()[0][:60]!r} "
                f"(root cause: explanatory comments violate comment ban; "
                f"fix: remove comment or use INVARIANT:/SECURITY:/SAFETY:/TODO(#id): or /** JSDoc */)"
            )

for css in sorted((root / "thyca/webui/ui").rglob("*.css")):
    src = css.read_text(encoding="utf-8")
    for lineno, text in iter_css_comments(src):
        if not BLOCK_ALLOWED.match(text):
            errors.append(
                f"{css.relative_to(root)}:{lineno}: disallowed CSS comment {text.splitlines()[0][:60]!r} "
                f"(root cause: explanatory comments violate comment ban; "
                f"fix: remove comment or use /* INVARIANT:/SECURITY:/SAFETY:/TODO(#id): */)"
            )

for sh in sorted((root / "scripts").rglob("*.sh")):
    for lineno, raw in enumerate(sh.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith("#") and not PY_ALLOWED.match(stripped):
            errors.append(
                f"{sh.relative_to(root)}:{lineno}: disallowed shell comment {stripped[:60]!r} "
                f"(root cause: explanatory comments violate comment ban; fix: remove comment or tag INVARIANT:/SECURITY:/SAFETY:)"
            )

if errors:
    for err in errors[:50]:
        print(f"FAIL: {err}", file=sys.stderr)
    if len(errors) > 50:
        print(f"... and {len(errors) - 50} more comment-ban violations", file=sys.stderr)
    sys.exit(1)

print("OK: check-comment-ban passed")
PY
