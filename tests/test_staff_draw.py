from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRAW = ROOT / "webui" / "js" / "staff-draw.js"
STAFF = ROOT / "webui" / "js" / "staff.js"

SHIM = r"""
class El {
  constructor(name) {
    this.tagName = String(name).toLowerCase();
    this.attrs = {};
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.dataset = {};
    this.width = 400;
    const self = this;
    this.classList = { contains(c) { return self.className.split(/\s+/).includes(c); } };
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (k === "class") this.className = String(v);
  }
  getAttribute(k) { return this.attrs[k] ?? null; }
  append(...nodes) {
    for (const n of nodes) { n.parentNode = this; this.children.push(n); }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  insertBefore(node, ref) {
    node.parentNode = this;
    const i = this.children.indexOf(ref);
    if (i < 0) this.children.push(node);
    else this.children.splice(i, 0, node);
  }
  getBoundingClientRect() { return { width: this.width }; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      if (match(n, sel, this)) out.push(n);
      for (const c of n.children || []) walk(c);
    };
    if (sel.startsWith(":scope > ")) {
      const inner = sel.slice(9);
      return this.children.filter((c) => match(c, inner, this));
    }
    for (const c of this.children || []) walk(c);
    return out;
  }
}
function match(n, sel) {
  if (sel.startsWith(".")) return (n.className || "").split(/\s+/).includes(sel.slice(1));
  if (sel.includes(".")) {
    const [tag, cls] = sel.split(".");
    return n.tagName === tag && (n.className || "").split(/\s+/).includes(cls);
  }
  return n.tagName === sel.toLowerCase();
}
globalThis.document = {
  createElementNS(_ns, name) { return new El(name); },
  createElement(name) { return new El(name); },
};
globalThis.window = { matchMedia() { return { matches: false }; } };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, imports: str, expression: str) -> object:
    source = (
        SHIM
        + f"import {{ {imports} }} from '{DRAW.as_posix()}';\n"
        + f"console.log(JSON.stringify({expression}));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_empty_staff_has_five_lines_and_clef(node: str) -> None:
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const svg = renderStaff([], { widthPx: 480 });
          const lines = svg.querySelectorAll(".staff-line");
          const clefs = svg.querySelectorAll(".staff-clef");
          return { lines: lines.length, clefs: clefs.length, tall: svg.className.includes("is-tall") };
        })()""",
    )
    assert result == {"lines": 5, "clefs": 1, "tall": False}


def test_long_staff_wraps_second(node: str) -> None:
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const events = Array.from({ length: 40 }, () => ({ kind: "note", duration: "q", steps: [5], chord: "I", sharps: [] }));
          const svg = renderStaff(events, { widthPx: 600 });
          return {
            tall: svg.className.includes("is-tall"),
            lines: svg.querySelectorAll(".staff-line").length,
            clefs: svg.querySelectorAll(".staff-clef").length,
            notes: svg.querySelectorAll(".staff-event").length,
          };
        })()""",
    )
    assert result["tall"] is True
    assert result["lines"] == 10
    assert result["clefs"] == 2
    assert result["notes"] == 40


def test_sync_staffs_only_on_thyca(node: str) -> None:
    source = (
        SHIM
        + f"import {{ syncStaffs }} from '{STAFF.as_posix()}';\n"
        + """
        const root = new El("div");
        const user = new El("article");
        user.className = "entry entry-user";
        const copyU = new El("div");
        copyU.className = "entry-copy";
        user.append(copyU);
        const thyca = new El("article");
        thyca.className = "entry entry-thyca";
        const copyT = new El("div");
        copyT.className = "entry-copy";
        thyca.append(copyT);
        const status = new El("article");
        status.className = "entry entry-thyca entry-status";
        const copyS = new El("div");
        copyS.className = "entry-copy";
        status.append(copyS);
        root.append(user, thyca, status);
        syncStaffs(root);
        const hosts = (el) => el.querySelectorAll(".thyca-staff-host").length;
        console.log(JSON.stringify({ user: hosts(user), thyca: hosts(thyca), status: hosts(status) }));
        """
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert json.loads(result.stdout) == {"user": 0, "thyca": 0, "status": 1}
