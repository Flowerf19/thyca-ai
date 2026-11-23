#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "[tier1] 1/6 ruff check"
if command -v ruff >/dev/null 2>&1; then
    ruff check .
elif command -v uvx >/dev/null 2>&1; then
    uvx ruff check .
elif command -v uv >/dev/null 2>&1; then
    uv tool run ruff check .
else
    echo "FAIL: ruff not found (root cause: linter binary unavailable; fix: install ruff or uv/uvx)" >&2
    exit 1
fi

echo "[tier1] 2/6 JS syntax check"
if command -v node >/dev/null 2>&1; then
    while IFS= read -r jsfile; do
        node --check "$jsfile"
    done < <(find thyca/webui tests -type f \( -name '*.js' -o -name '*.mjs' \) | sort)
else
    echo "FAIL: node not found (root cause: JS syntax gate requires node; fix: install Node.js 22+)" >&2
    exit 1
fi

echo "[tier1] 3/6 architectural boundaries & version sync"
python3 - <<'PY'
import ast
import re
import sys
from pathlib import Path

root = Path.cwd()
errors: list[str] = []

allowed_top = {"__init__.py", "__main__.py"}
for py in sorted((root / "thyca").glob("*.py")):
    if py.name not in allowed_top:
        errors.append(
            f"TOP-LEVEL FILE: {py.relative_to(root)} "
            f"(root cause: stray module at thyca/ root; fix: move into a thyca/<package>/ module)"
        )

for leaf_pkg in ("core", "platform"):
    pkg_dir = root / "thyca" / leaf_pkg
    for py in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("thyca.") and not alias.name.startswith(f"thyca.{leaf_pkg}"):
                        errors.append(
                            f"LEAF BOUNDARY: {py.relative_to(root)}:{node.lineno} imports {alias.name} "
                            f"(root cause: thyca/{leaf_pkg}/ must stay stdlib-only leaf; fix: invert dependency or move caller)"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("thyca.") and not node.module.startswith(f"thyca.{leaf_pkg}"):
                    errors.append(
                        f"LEAF BOUNDARY: {py.relative_to(root)}:{node.lineno} imports from {node.module} "
                        f"(root cause: thyca/{leaf_pkg}/ must stay stdlib-only leaf; fix: invert dependency or move caller)"
                    )

serve_dir = root / "thyca" / "serve"


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _iter_module_runtime_stmts(stmts: list[ast.stmt]):
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.If):
            if not _is_type_checking(stmt.test):
                yield from _iter_module_runtime_stmts(stmt.body)
            yield from _iter_module_runtime_stmts(stmt.orelse)
            continue
        if isinstance(stmt, ast.Try):
            yield from _iter_module_runtime_stmts(stmt.body)
            for handler in stmt.handlers:
                yield from _iter_module_runtime_stmts(handler.body)
            yield from _iter_module_runtime_stmts(stmt.orelse)
            yield from _iter_module_runtime_stmts(stmt.finalbody)
            continue
        yield stmt


for py in sorted(serve_dir.rglob("*.py")):
    tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
    for stmt in _iter_module_runtime_stmts(tree.body):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name == "thyca.app" or alias.name.startswith("thyca.app."):
                    errors.append(
                        f"SERVE BOUNDARY: {py.relative_to(root)}:{stmt.lineno} top-level runtime import {alias.name} "
                        f"(root cause: serve/ must not import app/ at module load; fix: move import under TYPE_CHECKING or inside function)"
                    )
        elif isinstance(stmt, ast.ImportFrom) and stmt.module:
            if stmt.module == "thyca.app" or stmt.module.startswith("thyca.app."):
                errors.append(
                    f"SERVE BOUNDARY: {py.relative_to(root)}:{stmt.lineno} top-level runtime import from {stmt.module} "
                    f"(root cause: serve/ must not import app/ at module load; fix: move import under TYPE_CHECKING or inside function)"
                )

pyproject_ver = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
init_ver = re.search(r'^__version__\s*=\s*"([^"]+)"', (root / "thyca/__init__.py").read_text(encoding="utf-8"), re.M)
if not pyproject_ver or not init_ver:
    errors.append("VERSION SYNC: missing version in pyproject.toml or thyca/__init__.py")
else:
    expected = pyproject_ver.group(1)
    if init_ver.group(1) != expected:
        errors.append(
            f"VERSION SYNC: thyca/__init__.py has {init_ver.group(1)} != pyproject.toml {expected} "
            f"(fix: sync version across pyproject.toml, thyca/__init__.py, uv.lock, README.md, and .agents/)"
        )
    lock_path = root / "uv.lock"
    if lock_path.is_file():
        lock_match = re.search(
            r'name\s*=\s*"thyca-ai"\s*\nversion\s*=\s*"([^"]+)"',
            lock_path.read_text(encoding="utf-8"),
        )
        if not lock_match or lock_match.group(1) != expected:
            found = lock_match.group(1) if lock_match else "<missing>"
            errors.append(
                f"VERSION SYNC: uv.lock has {found} != pyproject.toml {expected} "
                f"(fix: run uv lock to sync uv.lock with pyproject.toml)"
            )
    for doc_rel in (
        "README.md",
        ".agents/README.md",
        ".agents/AGENT_RULES.md",
        ".agents/PROJECT_CONTEXT.md",
        ".agents/FEATURE_MAP.md",
    ):
        doc_path = root / doc_rel
        if doc_path.is_file():
            text = doc_path.read_text(encoding="utf-8")
            if expected not in text:
                errors.append(
                    f"VERSION SYNC: {doc_rel} does not mention current version {expected} "
                    f"(fix: update version reference in {doc_rel} to {expected})"
                )

if errors:
    for err in errors:
        print(f"FAIL: {err}", file=sys.stderr)
    sys.exit(1)
PY

echo "[tier1] 4/6 test integrity guard"
bash "$ROOT/scripts/checks/check-no-weakened-tests.sh"

echo "[tier1] 5/6 comment ban guard"
bash "$ROOT/scripts/checks/check-comment-ban.sh"

echo "[tier1] 6/6 git diff --check"
git diff --check

echo "[tier1] ALL CHECKS PASSED"
