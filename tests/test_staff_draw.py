from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRAW = ROOT / "webui" / "js" / "staff-draw.js"
STAFF = ROOT / "webui" / "js" / "staff.js"
MAP = ROOT / "webui" / "js" / "staff-map.js"

SHIM = r"""
class El {
  constructor(name) {
    this.tagName = String(name).toLowerCase();
    this.attrs = {};
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.textContent = "";
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
    for (const n of nodes) {
      if (typeof n === "string") { this.textContent += n; continue; }
      n.parentNode = this; this.children.push(n);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.textContent = ""; this.append(...nodes); }
  insertBefore(node, ref) {
    node.parentNode = this;
    const i = this.children.indexOf(ref);
    if (i < 0) this.children.push(node);
    else this.children.splice(i, 0, node);
  }
  remove() {
    if (!this.parentNode) return;
    const kids = this.parentNode.children;
    const i = kids.indexOf(this);
    if (i >= 0) kids.splice(i, 1);
    this.parentNode = null;
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
  const have = (n.className || "").split(/\s+/);
  const attr = sel.match(/^\[([\w-]+)(?:='([^']*)')?\]$/);
  if (attr) {
    const val = n.getAttribute(attr[1]);
    return attr[2] === undefined ? val != null : val === attr[2];
  }
  if (sel.startsWith(".")) {
    return sel.slice(1).split(".").filter(Boolean).every((c) => have.includes(c));
  }
  if (sel.includes(".")) {
    const [tag, ...classes] = sel.split(".");
    return n.tagName === tag && classes.every((c) => have.includes(c));
  }
  return n.tagName === sel.toLowerCase();
}
globalThis.document = {
  createElementNS(_ns, name) { return new El(name); },
  createElement(name) { return new El(name); },
};
globalThis.window = { matchMedia() { return { matches: false }; } };
const observed = new Set();
globalThis.__observed = observed;
globalThis.ResizeObserver = class {
  observe(host) { observed.add(host); }
  unobserve(host) { observed.delete(host); }
  disconnect() { observed.clear(); }
};
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
        + f"import {{ scoreFromEvents }} from '{MAP.as_posix()}';\n"
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


def test_empty_score_one_system_with_clef_and_time(node: str) -> None:
    # scoreFromEvents([]) -> one I measure, whole rest, no terminal.
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const svg = renderStaff([], { widthPx: 480 });
          return {
            lines: svg.querySelectorAll(".staff-line").length,
            clefs: svg.querySelectorAll(".staff-clef").length,
            tall: svg.className.includes("is-tall"),
            time: svg.querySelectorAll(".staff-time").length,
            systems: svg.querySelectorAll("g.staff-system").length,
          };
        })()""",
    )
    assert result == {"lines": 5, "clefs": 1, "tall": False, "time": 1, "systems": 1}


def test_measures_wrap_only_when_width_runs_out(node: str) -> None:
    # 128px bars: 900px fits 6, 320px fits 2.
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const tools = (n) => Array.from({ length: n }, () => ({ type: "tool.finished", ok: true }));
          const sixWide = renderStaff(scoreFromEvents(tools(24)), { widthPx: 900 });
          const sevenWide = renderStaff(scoreFromEvents(tools(28)), { widthPx: 900 });
          const threeNarrow = renderStaff(scoreFromEvents(tools(12)), { widthPx: 320 });
          const box = (svg) => svg.getAttribute("viewBox").split(" ").map(Number);
          return {
            sixWideSystems: sixWide.querySelectorAll("g.staff-system").length,
            sevenWideSystems: sevenWide.querySelectorAll("g.staff-system").length,
            threeNarrowSystems: threeNarrow.querySelectorAll("g.staff-system").length,
            sevenClefs: sevenWide.querySelectorAll(".staff-clef").length,
            sevenTime: sevenWide.querySelectorAll(".staff-time").length,
            sixTall: box(sixWide)[3],
            sevenTall: box(sevenWide)[3],
          };
        })()""",
    )
    assert result["sixWideSystems"] == 1
    assert result["sevenWideSystems"] == 2
    assert result["threeNarrowSystems"] == 2
    assert result["sevenClefs"] == 2
    assert result["sevenTime"] == 1
    assert result["sevenTall"] > result["sixTall"]


def test_completed_terminal_has_double_barline(node: str) -> None:
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const events = [
            { type: "turn.accepted" },
            { type: "tool.finished", ok: true },
            { type: "turn.completed", detail: { id: "x" } },
          ];
          const score = scoreFromEvents(events);
          const svg = renderStaff(score, { widthPx: 480 });
          // Count final bar group: the last measure's measureContent.
          const allBars = svg.querySelectorAll(".staff-bar");
          const finalBars = svg.querySelectorAll(".staff-bar.is-final");
          const finalGroup = svg.querySelectorAll("g.staff-bar-group").length;
          return { allBars: allBars.length, finalBars: finalBars.length, finalGroup: finalGroup };
        })()""",
    )
    assert result["finalGroup"] == 1
    assert result["finalBars"] == 1
    # 480px fits 3 bars: 2 singles + thin+thick final at the end of the line.
    assert result["allBars"] == 4


def test_failed_terminal_has_single_barline(node: str) -> None:
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const events = [
            { type: "turn.accepted" },
            { type: "turn.failed", code: "llm_error" },
          ];
          const score = scoreFromEvents(events);
          const svg = renderStaff(score, { widthPx: 480 });
          return {
            allBars: svg.querySelectorAll(".staff-bar").length,
            finalBars: svg.querySelectorAll(".staff-bar.is-final").length,
            finalGroup: svg.querySelectorAll("g.staff-bar-group").length,
          };
        })()""",
    )
    assert result["finalBars"] == 0
    assert result["finalGroup"] == 0
    # failed: 480px fits 3 single barlines, no final group.
    assert result["allBars"] == 3


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


def test_smufl_glyphs_match_duration(node: str) -> None:
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const empty = renderStaff([]);
          const accepted = renderStaff(scoreFromEvents([{type:"turn.accepted"}]));
          const done = renderStaff(scoreFromEvents([
            {type:"turn.accepted"},
            {type:"turn.completed"},
          ]));
          const names = (svg) => [...svg.querySelectorAll("[data-glyph]")].map((n) => n.getAttribute("data-glyph"));
          const codes = (svg) => [...svg.querySelectorAll("[data-glyph]")].map((n) => n.textContent.codePointAt(0));
          return {
            empty: names(empty),
            emptyCodes: codes(empty),
            accepted: names(accepted),
            done: names(done),
          };
        })()""",
    )
    assert result["empty"][:3] == ["gClef", "timeSig4", "timeSig4"]
    assert "restWhole" in result["empty"]
    assert result["emptyCodes"][0] == 0xE050
    assert result["emptyCodes"][1] == 0xE084
    assert "noteheadBlack" in result["accepted"]
    assert "noteheadHalf" in result["done"]


def test_stem_side_follows_middle_line(node: str) -> None:
    result = _eval(
        node,
        "renderStaff",
        """(() => {
          const svg = renderStaff(scoreFromEvents([{type:"turn.accepted"}]));
          const head = svg.querySelector("[data-glyph='noteheadBlack']");
          const stem = svg.querySelector(".staff-stem");
          return {
            headX: Number(head.getAttribute("x")),
            stemX: Number(stem.getAttribute("x1")),
            glyph: head.getAttribute("data-glyph"),
          };
        })()""",
    )
    # C5 is above B4, so stem down = left of the head.
    assert result["glyph"] == "noteheadBlack"
    assert result["stemX"] < result["headX"]


def test_clear_staffs_unobserves_detached_host(node: str) -> None:
    source = (
        SHIM
        + f"import {{ mountStaff, clearStaffs }} from '{STAFF.as_posix()}';\n"
        + """
        const article = new El("article");
        article.className = "entry entry-thyca entry-status";
        mountStaff(article, []);
        const host = article.querySelector(".thyca-staff-host");
        const before = globalThis.__observed.has(host);
        clearStaffs(article);
        console.log(JSON.stringify({
          before,
          after: globalThis.__observed.has(host),
          remaining: article.querySelectorAll(".thyca-staff-host").length,
        }));
        """
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert json.loads(result.stdout) == {"before": True, "after": False, "remaining": 0}


def test_remount_same_host_redraws_intra_measure_event(node: str) -> None:
    source = (
        SHIM
        + f"import {{ mountStaff }} from '{STAFF.as_posix()}';\n"
        + f"import {{ scoreFromEvents }} from '{MAP.as_posix()}';\n"
        + """
        const article = new El("article");
        article.className = "entry entry-thyca entry-status";
        mountStaff(article, []);
        const empty = [...article.querySelectorAll("[data-glyph]")].map((n) => n.getAttribute("data-glyph"));
        mountStaff(article, scoreFromEvents([{ type: "turn.accepted" }]));
        const accepted = [...article.querySelectorAll("[data-glyph]")].map((n) => n.getAttribute("data-glyph"));
        console.log(JSON.stringify({ empty, accepted }));
        """
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    payload = json.loads(result.stdout)
    assert "noteheadBlack" not in payload["empty"]
    assert "restWhole" in payload["empty"]
    assert "noteheadBlack" in payload["accepted"]


def test_fifty_mount_clear_leaves_observer_empty(node: str) -> None:
    source = (
        SHIM
        + f"import {{ mountStaff, clearStaffs }} from '{STAFF.as_posix()}';\n"
        + """
        const article = new El("article");
        article.className = "entry entry-thyca entry-status";
        for (let i = 0; i < 50; i += 1) {
          mountStaff(article, []);
          clearStaffs(article);
        }
        console.log(JSON.stringify({ leftover: globalThis.__observed.size }));
        """
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert json.loads(result.stdout) == {"leftover": 0}
