"""Node tests for the Request panel (GOAL-011 TASK-036).

"Request theo mô hình" is ONE horizontal bar chart: every model bar shares
the same baseline and the same max scale, names/counts are real text (long
or hostile names never become markup), and the share denominator stays the
full-data total even when the search narrows the rows. The boot behavior runs
request.js against a minimal fake DOM in Node with a stubbed fetch, mirroring
the eval style of test_dashboard_journal.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REQUEST_JS = ROOT / "thyca" / "webui" / "pages" / "dashboard" / "request.js"


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


HARNESS = """
const el = {};
const createdTags = [];
function makeNode() {
  const node = {
    children: [], attrs: {}, dataset: {}, style: {}, listeners: {},
    className: "", textContent: "", value: "", tag: "",
    setAttribute(name, val) { node.attrs[name] = String(val); },
    append(...nodes) { node.children.push(...nodes); },
    replaceChildren(...nodes) { node.children = [...nodes]; },
    addEventListener(type, fn) { (node.listeners[type] ||= []).push(fn); },
    getBoundingClientRect() { return { width: 720, height: 168 }; },
  };
  return node;
}
for (const id of ["request-period", "request-total", "request-range",
                  "request-chart", "request-models", "request-status",
                  "request-model-search"]) {
  el[id] = makeNode();
}
el["request-period"].value = "30";
const sorts = ["req-desc", "req-asc", "recent"].map((key) => {
  const button = makeNode();
  button.dataset.reqSort = key;
  return button;
});
globalThis.document = {
  querySelector: (sel) => (sel.startsWith("#") ? el[sel.slice(1)] || null : null),
  querySelectorAll: () => sorts,
  createElement: (tag) => { const n = makeNode(); n.tag = tag; createdTags.push(tag); return n; },
  createElementNS: (_ns, tag) => { const n = makeNode(); n.tag = tag; return n; },
};
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
const pending = [];
const MANUAL = __MANUAL__;
let responder = MANUAL
  ? (record) => new Promise((resolve) => { record.resolve = resolve; })
  : () => (__PAYLOAD__);
globalThis.fetch = async (url) => {
  const record = { url: String(url), resolve: null };
  pending.push(record);
  return { ok: true, json: async () => responder(record) };
};
await import("__REQUEST_URI__");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
await tick();
const pick = (root, cls) =>
  root.children.find((n) => String(n.className).split(/\\s+/).includes(cls));
function snapshot() {
  const models = el["request-models"];
  const chart = pick(models, "request-model-chart");
  return {
    createdTags,
    rows: chart ? chart.children.map((li) => {
      const head = pick(li, "request-model-head");
      const bar = pick(li, "request-model-bar");
      return {
        name: pick(head, "request-model-name").textContent,
        count: pick(head, "request-model-count").textContent,
        share: pick(head, "request-model-share").textContent,
        width: pick(bar, "request-model-fill").style.width,
      };
    }) : null,
    note: models.children.filter((n) => String(n.className).includes("screen-note"))
      .map((n) => n.textContent),
    status: el["request-status"].textContent,
    total: el["request-total"].textContent,
    pressed: Object.fromEntries(sorts.map((b) => [b.dataset.reqSort, b.attrs["aria-pressed"]])),
  };
}
const fire = (node, type) => node.listeners[type][0]();
"""


def _run(node: str, body: str, payload: str, *, manual: bool = False) -> dict:
    source = (
        HARNESS.replace("__PAYLOAD__", payload)
        .replace("__MANUAL__", "true" if manual else "false")
        .replace("__REQUEST_URI__", REQUEST_JS.as_uri() + f"?test={uuid.uuid4().hex}")
        + body
        + "\nconsole.log(JSON.stringify(snapshot()));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


THREE_MODELS = json.dumps({
    "totals": {"requests": 100},
    "by_day": [{"day": "2026-09-20", "requests": 4}],
    "by_model": [
        {"model": "gpt-5.6-luna", "requests": 50, "last_started_at": "2026-09-20T09:00:00Z"},
        {"model": "muse-spark-1.2-contributor", "requests": 30, "last_started_at": "2026-09-19T09:00:00Z"},
        {"model": "a-very-long-model-name-with-date-suffix-20250929", "requests": 20, "last_started_at": "2026-09-18T09:00:00Z"},
    ],
})


def test_one_chart_with_shared_scale_and_full_denominator(node: str) -> None:
    body = """
    fire(el["request-model-search"], "input");
    """
    state = _run(node, body, THREE_MODELS)
    assert state["total"] == "100"
    # One chart, one row per model, sorted by requests desc by default.
    assert [row["name"] for row in state["rows"]] == [
        "gpt-5.6-luna",
        "muse-spark-1.2-contributor",
        "a-very-long-model-name-with-date-suffix-20250929",
    ]
    # All bars are measured on the same scale (largest value = 100%).
    assert [row["width"] for row in state["rows"]] == ["100%", "60%", "40%"]
    # Counts and shares are real text; share uses the full-data total (100),
    # which equals the row sum here.
    assert [row["count"] for row in state["rows"]] == ["50", "30", "20"]
    assert [row["share"] for row in state["rows"]] == ["50%", "30%", "20%"]
    assert state["note"] == []
    assert state["pressed"] == {"req-desc": "true", "req-asc": "false", "recent": "false"}


def test_search_filters_rows_but_denominator_stays_full(node: str) -> None:
    body = """
    el["request-model-search"].value = "muse";
    fire(el["request-model-search"], "input");
    """
    state = _run(node, body, THREE_MODELS)
    assert [row["name"] for row in state["rows"]] == ["muse-spark-1.2-contributor"]
    # The bar scale stays relative to all models, not the filtered subset…
    assert state["rows"][0]["width"] == "60%"
    # …and the share is still computed against the full-data total.
    assert state["rows"][0]["share"] == "30%"


def test_sort_buttons_reorder_rows(node: str) -> None:
    body = """
    fire(sorts.find((b) => b.dataset.reqSort === "req-asc"), "click");
    """
    state = _run(node, body, THREE_MODELS)
    assert [row["count"] for row in state["rows"]] == ["20", "30", "50"]
    # Bars do not rescale per sort: the largest model is still 100%.
    assert [row["width"] for row in state["rows"]] == ["40%", "60%", "100%"]
    assert state["pressed"]["req-asc"] == "true"


def test_empty_zero_and_single_model_are_honest(node: str) -> None:
    empty = _run(node, "", json.dumps({"totals": {"requests": 0}, "by_day": [], "by_model": []}))
    assert empty["rows"] == [] or empty["rows"] is None
    assert empty["note"] == ["Chưa có request nào trong khoảng này."]

    zero = _run(node, "", json.dumps({
        "totals": {"requests": 0}, "by_day": [],
        "by_model": [{"model": "unknown", "requests": 0}],
    }))
    # A zero-request model draws no bar and no fake data appears.
    assert not zero["rows"]
    assert zero["note"] == ["Chưa có request nào trong khoảng này."]

    one = _run(node, "", json.dumps({
        "totals": {"requests": 7}, "by_day": [],
        "by_model": [{"model": "solo-model", "requests": 7}],
    }))
    assert len(one["rows"]) == 1
    assert one["rows"][0]["width"] == "100%"
    assert one["rows"][0]["share"] == "100%"


def test_long_and_hostile_names_stay_text(node: str) -> None:
    payload = json.dumps({
        "totals": {"requests": 3},
        "by_day": [],
        "by_model": [{"model": "<img src=x onerror=alert(1)>", "requests": 3}],
    })
    body = """
    fire(el["request-model-search"], "input");
    """
    state = _run(node, body, payload)
    # The raw hostile string is preserved as text, never parsed into markup.
    assert state["rows"][0]["name"] == "<img src=x onerror=alert(1)>"
    assert not any(tag not in {"ul", "li", "div", "span", "p"} for tag in state["createdTags"])


def test_period_change_never_keeps_a_stale_response(node: str) -> None:
    # MANUAL mode keeps every response deferred so the test controls the
    # resolution order, including the boot-time request.
    body = """
    el["request-period"].value = "7";
    fire(el["request-period"], "change");
    await tick();
    const slowUrl = pending[0].url;
    const fastUrl = pending[1].url;
    if (slowUrl === fastUrl) throw new Error("period change must request a new range");
    // The newer (7-day) request resolves first…
    pending[1].resolve({
      totals: {"requests": 5}, "by_day": [],
      "by_model": [{"model": "fresh-7d", "requests": 5}],
    });
    await tick();
    const afterFast = snapshot();
    // …then the older (30-day) request lands late and must be ignored.
    pending[0].resolve({
      totals: {"requests": 900}, "by_day": [],
      "by_model": [{"model": "stale-30d", "requests": 900}],
    });
    await tick();
    result = { afterFast, final: snapshot(), urls: [slowUrl, fastUrl] };
    """
    source = (
        HARNESS.replace("__PAYLOAD__", THREE_MODELS)
        .replace("__MANUAL__", "true")
        .replace("__REQUEST_URI__", REQUEST_JS.as_uri() + f"?test={uuid.uuid4().hex}")
        + "let result;\n" + body
        + "\nconsole.log(JSON.stringify(result));\n"
    )
    outcome = json.loads(subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True, capture_output=True, text=True, cwd=ROOT,
    ).stdout)
    assert "fresh-7d" in json.dumps(outcome["afterFast"]["rows"])
    assert outcome["final"]["rows"][0]["name"] == "fresh-7d"
    assert outcome["final"]["total"] == "5"
    assert "stale-30d" not in json.dumps(outcome["final"])
    assert outcome["final"]["status"] == ""
    assert len(outcome["urls"]) == 2 and outcome["urls"][0] != outcome["urls"][1]
