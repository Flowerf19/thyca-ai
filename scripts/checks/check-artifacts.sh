#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ARTIFACTS_DIR="${1:-$ROOT/.agents/artifacts}"
MANIFEST="$ARTIFACTS_DIR/manifest.md"

if [[ ! -f "$MANIFEST" ]]; then
    echo "FAIL: missing $MANIFEST (root cause: Tier 2 verification manifest not exported; fix: write populated manifest.md in $ARTIFACTS_DIR)" >&2
    exit 1
fi

if [[ ! -s "$MANIFEST" ]]; then
    echo "FAIL: empty $MANIFEST (root cause: manifest has 0 bytes; fix: populate commands, results, and linked evidence files)" >&2
    exit 1
fi

python3 - "$MANIFEST" <<'PY'
import re
import sys
from pathlib import Path

manifest = Path(sys.argv[1]).resolve()
text = manifest.read_text(encoding="utf-8")
errors: list[str] = []

for placeholder in ("TODO", "TBD", "PLACEHOLDER", "<command>", "<path>", "<result>"):
    if placeholder in text:
        errors.append(f"unfilled placeholder {placeholder!r} in {manifest}")

links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
if not links:
    errors.append(f"no linked artifact files found in {manifest}")

for raw_target in links:
    if raw_target.startswith(("http://", "https://", "#")):
        continue
    target = (manifest.parent / raw_target.split("#", 1)[0]).resolve()
    if not target.is_file():
        errors.append(f"missing linked artifact: {raw_target} (resolved {target})")
    elif target.stat().st_size == 0:
        errors.append(f"empty linked artifact: {raw_target}")

if errors:
    for err in errors:
        print(f"FAIL: {err}", file=sys.stderr)
    sys.exit(1)
print(f"OK: verified {len(links)} artifact link(s) in {manifest}")
PY
