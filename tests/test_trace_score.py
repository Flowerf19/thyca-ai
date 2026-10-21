"""Trace detail mapping — thyca/webui/ui/trace/js/trace.js.

The old trace-data.js helpers (toolsFromDetail, toolBatchesFromDetail,
activityStepsFromDetail, groupToolCalls, tokenCost, selectedModelConfig)
were not ported: the new Trace page renders the execution journal straight
from ``detail.messages`` — one numbered ``.tl-step`` per tool call matched
to its result by tool_call_id, orphan results as their own step, session
cards via sessionGroups/sessionCardHtml, and no client-side pricing. These
tests cover that replacement contract. The page is a classic script
('use strict', shared globals), so the harness evals it in Node with a
stubbed global surface, mirroring the old import-based eval style.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRACE_JS = ROOT / "thyca" / "webui" / "ui" / "trace" / "js" / "trace.js"

# Global surface for trace.js: real util.js semantics for $/$$/escapeHTML/
# icon, a per-id element registry, and the data-layer globals the page
# expects (getBot/avatar/paletteStyle/getJson/toast/bootShell). window gets
# no formatMarkdown: the escaped-paragraph fallback is what this file pins.
STUBS = r"""
class El {
  constructor(tag = 'div') {
    this.tag = tag; this.value = ''; this.textContent = ''; this._html = '';
    this.hidden = false; this.disabled = false; this.dataset = {}; this.attrs = {};
    this.listeners = {}; this.children = []; this.options = []; this.className = '';
    this.scrollTop = 0; this.style = {};
  }
  set innerHTML(v) { this._html = String(v); }
  get innerHTML() { return this._html; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  replaceChildren(...n) { this.children = n; this.options = n; }
  insertAdjacentHTML(_p, h) { this._html = (this._html || '') + h; }
  set onclick(f) { this.listeners.click = [f]; }
  set onchange(f) { this.listeners.change = [f]; }
}
const __els = {};
const __el = (id) => __els[id] ||= new El();
const __workspace = new El();
globalThis.document = {
  querySelector: (s) => s === '.workspace' ? __workspace
    : s.startsWith('#') ? __el(s.slice(1)) : null,
  querySelectorAll: () => [],
  createElement: (t) => new El(t),
  addEventListener() {},
};
const __urls = [];
globalThis.location = { hash: '', search: '', pathname: '/trace/index.html' };
globalThis.history = { replaceState: (_a, _b, u) => __urls.push(String(u)) };
globalThis.window = {};
globalThis.addEventListener = () => {};
globalThis.$ = (s, r = document) => r.querySelector(s);
globalThis.$$ = (s, r = document) => [...r.querySelectorAll(s)];
globalThis.icon = (n) => `<svg data-i="${n}"></svg>`;
globalThis.escapeHTML = (str) => String(str).replace(/[&<>"']/g, (ch) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
globalThis.__roster = {};
globalThis.getBot = (id) => globalThis.__roster[id] || null;
globalThis.avatar = (b) => `<ava:${b.id}/>`;
globalThis.paletteStyle = (b) => `--c:${b.color || '#000'}`;
globalThis.toast = () => {};
globalThis.bootShell = async () => {};
const __jsonCalls = [];
let __jsonResponder = async (url) => { throw new Error('unexpected ' + url); };
globalThis.getJson = (url) => { __jsonCalls.push(String(url)); return __jsonResponder(String(url)); };
"""


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval_trace(node: str, body: str) -> object:
    """Eval trace.js (classic script) with stubs, then run `body` inside
    the same scope so module-level functions/state are reachable."""
    src = TRACE_JS.read_text(encoding="utf-8")
    program = (
        STUBS
        + "\nconst __src = " + json.dumps(src) + ";\n"
        + "const __body = " + json.dumps(body) + ";\n"
        + "eval(__src + '\\n;' + __body);\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", program],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_skill_call_keeps_raw_name_and_skill(node: str) -> None:
    """A skill load keeps its skill id as the step title with the raw tool
    name demoted to the meta line; a plain call shows its own name."""
    call = {"id": "c1", "name": "read", "skill": "codereview",
            "arguments": {"path": "SKILL.md"}}
    res = {"role": "tool", "tool_call_id": "c1", "content": "out1",
           "meta": {"latency_ms": 120}}
    html = _eval_trace(
        node,
        f"console.log(JSON.stringify("
        f" stepHtml(1, {json.dumps(call)}, {json.dumps(res)})"
        f" + '|' + stepHtml(2, {{id: 'c2', name: 'read',"
        "   arguments: {path: 'notes.md'}},"
        f"  {{role: 'tool', tool_call_id: 'c2', content: 'out2',"
        "   meta: {latency_ms: 5}})))",
    )
    skill_step, plain_step = html.split("|")
    assert '<p class="tl-step-title">codereview' in skill_step
    assert "tool read" in skill_step          # raw name kept in meta
    assert "120 ms" in skill_step
    assert "c1" in skill_step                 # call id in the meta bits
    assert "&quot;path&quot;: &quot;SKILL.md&quot;" in skill_step
    assert "out1" in skill_step
    assert '<p class="tl-step-title">read' in plain_step
    assert "tool read" not in plain_step      # no redundant meta for name==skill
    assert "5 ms" in plain_step


def test_tool_batches_preserve_parallel_assistant_calls(node: str) -> None:
    """Calls stay under the assistant round that issued them: two assistant
    messages render two step lists, first-seen order kept per list."""
    detail = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "name": "memory_recent"},
                {"id": "c2", "name": "bash"},
            ]},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c3", "name": "read"},
            ]},
        ]
    }
    html = _eval_trace(node, f"console.log(JSON.stringify(timelineHtml({json.dumps(detail)})))")
    lists = html.split('<ol class="tl-steps">')[1:]
    assert len(lists) == 2
    first_titles = [
        p.split('<p class="tl-step-title">')[1].split("<")[0]
        for p in lists[0].split('<li class="tl-step')[1:]
    ]
    assert first_titles == ["memory_recent", "bash"]
    assert '<p class="tl-step-title">read' in lists[1]


def test_activity_steps_keep_real_message_order(node: str) -> None:
    """The journal order is the real message order: user bubble, an
    assistant item per response (content + its tool steps), the naming
    sidecar as a quiet meta line."""
    detail = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "name": "bash"},
                {"id": "c2", "name": "bash"},
            ], "meta": {"latency_ms": 40}},
            {"role": "tool", "tool_call_id": "c1", "content": "a",
             "meta": {"latency_ms": 10}},
            {"role": "tool", "tool_call_id": "c2", "content": "b",
             "meta": {"latency_ms": 20}},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c3", "name": "bash"},
                {"id": "c4", "name": "bash"},
            ], "meta": {"latency_ms": 50}},
            {"role": "assistant", "content": "Đặt tên",
             "meta": {"kind": "naming"}},
            {"role": "assistant", "content": "done",
             "meta": {"latency_ms": 12}},
        ]
    }
    html = _eval_trace(node, f"console.log(JSON.stringify(timelineHtml({json.dumps(detail)})))")
    order = [
        html.index("tl-item-user"),
        html.index(">bash<"),           # first round's first step
        html.index(">done<") if ">done<" in html else html.index("done"),
    ]
    assert order[0] < order[1] < order[2]
    # Four calls, each its own numbered step — never merged by name.
    assert html.count('class="tl-step-num"') == 4
    assert "01" in html and "04" in html
    # The naming assistant renders as the quiet sidecar line, not a bubble.
    assert "Đổi tên phiên" in html
    assert "tl-meta" in html


def test_string_arguments_render_raw_with_parse_flag(node: str) -> None:
    """Arguments arrive as recorded (dict, or the raw string when the model
    emitted unparseable JSON): the input block prints them verbatim and
    parse_error is flagged — nothing crashes on a string payload."""
    detail = {
        "messages": [
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "name": "bash",
                             "arguments": "{bad json",
                             "parse_error": "invalid json"}]},
        ]
    }
    html = _eval_trace(node, f"console.log(JSON.stringify(timelineHtml({json.dumps(detail)})))")
    assert "Lỗi cú pháp" in html
    assert "invalid json" in html
    assert "{bad json" in html
    assert "Parse error" in html


def test_step_input_shows_arguments_json(node: str) -> None:
    """The step's Input block is the recorded arguments as pretty JSON —
    the tool's real input is always inspectable."""
    call = {"id": "c1", "name": "memory_recent", "arguments": {"limit": 3}}
    html = _eval_trace(
        node,
        f"console.log(JSON.stringify(stepHtml(1, {json.dumps(call)}, null)))",
    )
    assert '<p class="tl-io-label">Input</p>' in html
    assert "&quot;limit&quot;: 3" in html  # escaped pretty JSON in the pre


def test_tool_without_result_shows_placeholder(node: str) -> None:
    """A call whose result never recorded gets an explicit placeholder —
    never a faked empty output."""
    html = _eval_trace(
        node,
        "console.log(JSON.stringify(stepHtml(1,"
        " {id: 'c1', name: 'bash'}, null)))",
    )
    assert "Chưa ghi nhận output." in html


def test_missing_tool_name_falls_back_to_tool(node: str) -> None:
    html = _eval_trace(
        node,
        "console.log(JSON.stringify(stepHtml(1, {id: 'c1'}, null)))",
    )
    assert '<p class="tl-step-title">tool' in html


def test_step_output_distinguishes_missing_empty_zero_false(node: str) -> None:
    """Missing result = the placeholder; empty string = '(trống)'; a
    recorded 0/false renders verbatim — the falsy trap stays closed."""
    call = {"id": "c1", "name": "bash"}
    out = _eval_trace(
        node,
        "console.log(JSON.stringify(["
        " stepHtml(1, {id:'c1',name:'bash'}, null),"
        " stepHtml(2, {id:'c2',name:'bash'}, {role:'tool',tool_call_id:'c2',content:''}),"
        " stepHtml(3, {id:'c3',name:'bash'}, {role:'tool',tool_call_id:'c3',content:0}),"
        " stepHtml(4, {id:'c4',name:'bash'}, {role:'tool',tool_call_id:'c4',content:false})"
        "]))",
    )
    missing, empty, zero, falsey = out
    assert "Chưa ghi nhận output." in missing
    assert "(trống)" in empty
    assert ">0</pre>" in zero
    assert ">false</pre>" in falsey


def test_recorded_tool_error_flag_marks_the_step(node: str) -> None:
    """meta.is_error on the result marks the step s-error with the 'Thực
    thi lỗi' flag and the error-tinted code surface."""
    call = {"id": "c1", "name": "bash"}
    res = {"role": "tool", "tool_call_id": "c1", "content": "boom",
           "meta": {"is_error": True, "latency_ms": 9}}
    html = _eval_trace(
        node,
        f"console.log(JSON.stringify(stepHtml(1, {json.dumps(call)}, {json.dumps(res)})))",
    )
    assert 'tl-step s-error' in html
    assert "Thực thi lỗi" in html
    assert "tl-code-err" in html
    assert "boom" in html
    assert "9 ms" in html


def test_missing_latency_absent_but_zero_stays_zero(node: str) -> None:
    """A result without latency_ms shows no duration (never a fake 0 ms —
    Number(null) === 0), while a recorded 0 prints '0 ms'."""
    out = _eval_trace(
        node,
        "console.log(JSON.stringify(["
        " stepHtml(1, {id:'c1',name:'bash'},"
        "  {role:'tool',tool_call_id:'c1',content:'ok',meta:{latency_ms:0}}),"
        " stepHtml(2, {id:'c2',name:'bash'},"
        "  {role:'tool',tool_call_id:'c2',content:'ok',meta:{}})]))",
    )
    zero, missing = out
    assert "0 ms" in zero
    assert " ms" not in missing
    # The call id still rides the meta line on both.
    assert "c1" in zero and "c2" in missing


def test_orphan_tool_result_gets_its_own_step(node: str) -> None:
    """A tool result with no matching call renders as its own numbered
    step ('Kết quả tool' + call id) — never dropped silently."""
    detail = {
        "messages": [
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_call_id": "ghost", "content": "late out",
             "meta": {"latency_ms": 3, "is_error": True}},
        ]
    }
    html = _eval_trace(node, f"console.log(JSON.stringify(timelineHtml({json.dumps(detail)})))")
    assert "Kết quả tool" in html
    assert "Thực thi lỗi" in html
    assert "call ghost" in html
    assert "late out" in html


def test_session_groups_first_seen_order_and_card(node: str) -> None:
    """Session grouping keeps the API's newest-first order (first-seen
    session_id, turns in payload order); the card takes the newest turn's
    title, turn count, kind label and the failed-turn chip."""
    rows = [
        {"session_id": "g1", "turn_index": 2, "title": "Nhóm",
         "started_at": "2026-09-08T10:02:00", "status": "failed",
         "session_kind": "group", "session_bots": ["a", "b"]},
        {"session_id": "g1", "turn_index": 0, "title": "Nhóm",
         "started_at": "2026-09-08T10:00:00", "status": "completed",
         "session_kind": "group", "session_bots": ["a", "b"]},
        {"session_id": "d1", "turn_index": 0, "title": "DM",
         "started_at": "2026-09-07T09:00:00", "status": "completed",
         "session_kind": "dm", "session_dm": "a"},
    ]
    out = _eval_trace(
        node,
        f"tr.rows = {json.dumps(rows)};\n"
        "const gs = sessionGroups();\n"
        "console.log(JSON.stringify({"
        " ids: gs.map((g) => g.sid),"
        " turnOrder: gs[0].turns.map((t) => t.turn_index),"
        " card: sessionCardHtml(gs[0]) }))",
    )
    assert out["ids"] == ["g1", "d1"]
    # Payload order is kept (newest first) — the API already sorted.
    assert out["turnOrder"] == [2, 0]
    card = out["card"]
    assert "<h2" in card and "Nhóm" in card
    assert "2 lượt" in card
    assert "Hội thoại chung" in card
    assert "1 lượt lỗi" in card  # failed-turn chip from the group's turns


def test_session_card_kind_dm_and_seals(node: str) -> None:
    """DM sessions read 'Trò chuyện riêng'; seals come from the union of
    session_bots/breakdown/bot_id/session_dm, capped at 3 + '+N'."""
    rows = [
        {"session_id": "d1", "turn_index": 0, "title": "DM",
         "started_at": "2026-09-07T09:00:00", "status": "completed",
         "session_kind": "dm", "session_dm": "coder"},
        {"session_id": "g", "turn_index": 0, "title": "G",
         "started_at": "2026-09-08T09:00:00", "status": "completed",
         "session_kind": "group",
         "session_bots": ["a", "b", "c", "d"]},
    ]
    out = _eval_trace(
        node,
        f"tr.rows = {json.dumps(rows)};\n"
        "const gs = sessionGroups();\n"
        "console.log(JSON.stringify({"
        " dm: sessionCardHtml(gs[0]), grp: sessionCardHtml(gs[1]),"
        " ids: groupBotIds(gs[1]) }))",
    )
    assert "Trò chuyện riêng" in out["dm"]
    assert out["ids"] == ["a", "b", "c", "d"]
    assert "+1" in out["grp"]               # 4 ids → 3 seals + '+1'
    assert 'class="tl-botid"' in out["dm"]  # no roster → id chip fallback


def test_first_user_and_final_assistant_render_in_order(node: str) -> None:
    """The first user message and the final assistant reply both land in
    the timeline, in order — the journal needs no separate IO block."""
    detail = {
        "messages": [
            {"role": "assistant", "content": "first assistant"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "  "},
            {"role": "assistant", "content": "final answer"},
        ]
    }
    html = _eval_trace(node, f"console.log(JSON.stringify(timelineHtml({json.dumps(detail)})))")
    assert "hello" in html and "first assistant" in html
    assert "final answer" in html
    assert html.index("hello") < html.index("final answer")
    # The whitespace-only assistant message still keeps its item shell.
    assert html.count("tl-item-assistant") == 3


def test_empty_or_missing_messages(node: str) -> None:
    """A turn that recorded no messages renders the explicit empty note —
    never a blank page or a crash."""
    for expr in ("timelineHtml({})", "timelineHtml({messages: []})",
                 "timelineHtml({messages: null})"):
        html = _eval_trace(node, f"console.log(JSON.stringify({expr}))")
        assert "Lượt này không ghi message nào" in html


def test_reasoning_folds_encrypted_and_text_parts(node: str) -> None:
    """reasoning + reasoning_details collapse into one 'Suy nghĩ' fold;
    encrypted parts keep a marker, text/summary parts join verbatim."""
    detail = {
        "messages": [
            {"role": "assistant", "content": "answer",
             "reasoning": "thought one",
             "reasoning_details": [
                 {"type": "reasoning.text", "text": "thought two"},
                 {"type": "reasoning.summary", "summary": "recap"},
                 {"type": "reasoning.encrypted"},
             ]},
        ]
    }
    html = _eval_trace(node, f"console.log(JSON.stringify(timelineHtml({json.dumps(detail)})))")
    assert '<details class="tl-reason"><summary>Suy nghĩ</summary>' in html
    assert "thought one" in html and "thought two" in html and "recap" in html
    assert "(đoạn suy nghĩ đã mã hoá)" in html
