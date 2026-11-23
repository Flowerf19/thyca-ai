#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
import re
import sys
from pathlib import Path

root = Path.cwd()
errors: list[str] = []

patterns = [
    (
        re.compile(r"pytest\.raises\(\s*Exception\s*[\),]"),
        "blind pytest.raises(Exception)",
        "assert the specific expected exception type (and match= when applicable)",
    ),
    (
        re.compile(r"^\s*assert\s+(True|1\s*==\s*1)\s*(#.*)?$"),
        "tautological assertion",
        "assert real observed behavior instead of a constant truthy expression",
    ),
    (
        re.compile(r"@pytest\.mark\.xfail\b"),
        "xfail marker weakens test gate",
        "fix the underlying defect or track separately instead of xfail",
    ),
    (
        re.compile(r"#\s*noqa\s*$"),
        "bare # noqa suppression",
        "fix the root lint issue or use an explicit code with reason",
    ),
    (
        re.compile(r"#\s*type:\s*ignore\s*$"),
        "bare # type: ignore suppression",
        "fix the type error or specify an explicit error code",
    ),
]

for py in sorted((root / "tests").rglob("*.py")):
    for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), start=1):
        for regex, cause, fix in patterns:
            if regex.search(line):
                errors.append(f"{py.relative_to(root)}:{lineno}: {cause} (fix: {fix})")

for py in sorted((root / "thyca").rglob("*.py")):
    for lineno, line in enumerate(py.read_text(encoding="utf-8").splitlines(), start=1):
        for regex, cause, fix in patterns[3:]:
            if regex.search(line):
                errors.append(f"{py.relative_to(root)}:{lineno}: {cause} (fix: {fix})")

if errors:
    for err in errors:
        print(f"FAIL: {err}", file=sys.stderr)
    sys.exit(1)
print("OK: check-no-weakened-tests passed")
PY
