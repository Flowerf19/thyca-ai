"""Node tests for the event catalog — webui/js/staff/catalog.js.

The catalog is the only place that maps event types to musical roles;
the mapper must stay free of event-name switches (behavior-preserving
refactor, see .agents/plans/staff-event-catalog.md).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "webui" / "js" / "staff" / "catalog.js"
MAP = ROOT / "webui" / "js" / "staff" / "map.js"

_PROBE_TYPES = [
    "turn.accepted",
    "llm.started",
    "tool.started",
    "tool.finished",
    "turn.completed",
    "turn.failed",
]


@pytest.fixture(scope="module")
def node() -> str:
    binary = shutil.which("node")
    if not binary:
        pytest.skip("node not installed")
    return binary


def _eval(node: str, expression: str, module: str = "CATALOG") -> object:
    path = CATALOG if module == "CATALOG" else MAP
    which = "familyFor, catalogEntries" if module == "CATALOG" else "scoreFromEvents"
    source = (
        f"import {{ {which} }} from '{path.as_posix()}';\n"
        f"console.log(JSON.stringify({expression}));\n"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


def test_registered_entries_match_contract(node: str) -> None:
    entries = _eval(node, "catalogEntries()")
    by_type = {entry["type"]: entry for entry in entries}
    assert by_type["turn.accepted"] == {"type": "turn.accepted", "slot": "pulse", "density": "anchor"}
    assert by_type["llm.started"] == {"type": "llm.started", "slot": "pulse", "density": "anchor"}
    assert by_type["llm.finished"] == {"type": "llm.finished", "slot": "pulse", "density": "outer"}
    assert by_type["llm.retry"] == {"type": "llm.retry", "slot": "rest"}
    assert by_type["tool.started"] == {"type": "tool.started", "slot": "pulse", "density": "cue"}
    assert by_type["skill.started"] == {"type": "skill.started", "slot": "pulse", "density": "cue"}
    assert by_type["tool.finished"]["slot"] == "pulse"
    assert by_type["tool.finished"]["density"] == "full"
    assert by_type["skill.finished"]["slot"] == "pulse"
    assert by_type["skill.finished"]["density"] == "full"
    assert by_type["session.naming.started"] == {"type": "session.naming.started", "slot": "rest"}
    assert by_type["session.naming.finished"] == {
        "type": "session.naming.finished",
        "slot": "pulse",
        "density": "anchor",
    }
    assert by_type["turn.completed"] == {
        "type": "turn.completed",
        "slot": "terminal",
        "kind": "completed",
    }
    assert by_type["turn.failed"] == {"type": "turn.failed", "slot": "terminal", "kind": "failed"}


def test_error_when_guard_compiled_not_evaled(node: str) -> None:
    # guard true = error color (vii°); guard false = normal density voicing
    ok = _eval(node, "familyFor({type:'tool.finished', ok:true}).errorWhen({ok:true})")
    failed = _eval(node, "familyFor({type:'tool.finished', ok:false}).errorWhen({ok:false})")
    assert ok is False  # ok === true -> not an error
    assert failed is True
    missing = _eval(node, "familyFor({type:'skill.finished'}).errorWhen({})")
    assert missing is True  # ok absent -> ok !== true -> error color


def test_family_for_rejects_non_events(node: str) -> None:
    result = _eval(
        node,
        """[
          familyFor(null),
          familyFor("tool.started"),
          familyFor(42),
          familyFor({}),
          familyFor({type: 5}),
          familyFor({type: "no.such.event"}),
        ]""",
    )
    assert result == [None, None, None, None, None, None]


def test_only_transport_types_are_terminal(node: str) -> None:
    terminals = _eval(
        node,
        "catalogEntries().filter((e) => e.slot === 'terminal').map((e) => e.type)",
    )
    assert sorted(terminals) == ["turn.completed", "turn.failed"]


def test_every_pulse_has_valid_density(node: str) -> None:
    densities = _eval(
        node,
        """catalogEntries()
          .filter((e) => e.slot === 'pulse')
          .map((e) => e.density)""",
    )
    assert densities
    assert all(d in ("anchor", "cue", "outer", "full") for d in densities)


def test_mapper_stays_free_of_event_names(node: str) -> None:
    # Strip comments first: the rule targets code, not documentation.
    code_only = "\n".join(
        line for line in MAP.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("//")
    )
    for name in ("tool.", "skill.", "llm.", "naming.", "turn.accepted"):
        assert name not in code_only, f"staff/map.js code must not know event name {name!r}"


def test_catalog_error_drops_entry_not_crash(node: str) -> None:
    """Malformed YAML entries degrade to silence — never throw at import."""
    import re
    source_template = CATALOG.read_text(encoding="utf-8")
    # (entry to mutate, property to inject, probe type expected to drop)
    cases = [
        ("turn.accepted", "  slot: sideways", "turn.accepted"),
        ("turn.completed", "  kind: maybe", "turn.completed"),
        ("tool.started", "  density: weird", "tool.started"),
    ]
    for entry_type, mutation, dropped in cases:
        yaml = _mutate_entry(source_template, entry_type, mutation)
        module = _module_from_yaml(node, yaml)
        index = _PROBE_TYPES.index(dropped)
        assert module["families"][index] is None, (entry_type, mutation)
        # other probes still resolve — the drop is local
        others = [f for i, f in enumerate(module["families"]) if i != index]
        assert any(f is not None for f in others)


def _mutate_entry(yaml: str, entry_type: str, mutation: str) -> str:
    import re
    pattern = re.compile(
        rf"(- type: {re.escape(entry_type)}\n)(?=  )", re.MULTILINE
    )
    match = pattern.search(yaml)
    assert match, entry_type
    return yaml[:match.end(1)] + mutation + "\n" + yaml[match.end(1):]


def test_duplicate_type_keeps_first(node: str) -> None:
    yaml = CATALOG.read_text(encoding="utf-8") + "\n- type: turn.accepted\n  slot: rest\n"
    module = _module_from_yaml(node, yaml)
    # turn.accepted is probe 0: still the original pulse/anchor, not rest
    assert module["families"][0] == {"slot": "pulse", "density": "anchor"}


def test_error_when_outside_whitelist_drops_entry(node: str) -> None:
    yaml = CATALOG.read_text(encoding="utf-8").replace(
        "  errorWhen: ok !== true", "  errorWhen: process.exit(1)"
    )
    module = _module_from_yaml(node, yaml)
    # tool.finished (probe 3) dropped; tool.started (probe 2) kept
    assert module["families"][3] is None
    assert module["families"][2] is not None


def test_empty_catalog_is_all_silence(node: str) -> None:
    module = _module_from_yaml(node, "")
    assert all(family is None for family in module["families"])
    # unknown events cost no beat: measure closes as beat-1 note? No — with no
    # family the accepted event is skipped entirely, so the in-flight measure
    # closes with the standard trailing-rest fill for 0 used slots → whole rest.
    measure = module["emptyScore"]["measures"][0]
    assert measure["events"] == []
    assert measure["rests"] == [{"offset": 0, "duration": 16}]


def _module_from_yaml(node: str, yaml: str) -> dict:
    """Load the real catalog module but with a replaced YAML string (tmp file).

    Returns {familyFor, scoreFromEvents} as plain JSON-able descriptors:
    familyFor is a pure function, so we evaluate it inside Node over a fixed
    probe set and ship the results back.
    """
    import re
    import tempfile
    source = CATALOG.read_text(encoding="utf-8")
    escaped = (
        yaml.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    )
    patched = re.sub(
        r"const CATALOG_YAML = `.*?`;",
        "const CATALOG_YAML = `" + escaped + "`;",
        source,
        count=1,
        flags=re.DOTALL,
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", dir=CATALOG.parent, prefix="catalog-test-", delete=False
    ) as handle:
        handle.write(patched)
        path = Path(handle.name)
    probes = [
        {"type": "turn.accepted"},
        {"type": "llm.started"},
        {"type": "tool.started"},
        {"type": "tool.finished", "ok": True},
        {"type": "turn.completed"},
        {"type": "turn.failed"},
    ]
    loader = (
        f"import {{ familyFor }} from '{path.as_posix()}';\n"
        f"import {{ scoreFromEvents }} from '{MAP.as_posix()}';\n"
        f"const probes = {json.dumps(probes)};\n"
        "console.log(JSON.stringify({\n"
        "  families: probes.map((p) => familyFor(p)),\n"
        "  emptyScore: scoreFromEvents([{type: 'turn.accepted'}], familyFor),\n"
        "}));\n"
    )
    try:
        result = subprocess.run(
            [node, "--input-type=module", "-e", loader],
            check=True,
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        return json.loads(result.stdout)
    finally:
        path.unlink()


def test_property_mixed_catalog_stream_invariants(node: str) -> None:
    """For any interleaving of registered event shapes, every measure stays a
    closed 16-tick bar: union [0,16), no overlap, nothing crosses beat 3,
    cadence matches the terminal kind."""
    expression = r"""(() => {
      const pool = [
        {type: "turn.accepted"},
        {type: "llm.started", round: 1},
        {type: "llm.finished", round: 1, tool_count: 1},
        {type: "tool.started", round: 1, call_id: "c", name: "read"},
        {type: "tool.finished", round: 1, call_id: "c", name: "read", ok: true},
        {type: "tool.finished", round: 1, call_id: "c", name: "bash", ok: false},
        {type: "skill.started", round: 1, call_id: "c", name: "create-skill"},
        {type: "skill.finished", round: 1, call_id: "c", name: "create-skill", ok: true},
        {type: "skill.finished", round: 1, call_id: "c", name: "create-skill", ok: false},
        {type: "session.naming.started"},
        {type: "session.naming.finished", updated: true},
        {type: "turn.delta", text: "noise"},
      ];
      // deterministic pseudo-random walk over the pool
      let seed = 7;
      const next = (mod) => (seed = (seed * 1103515245 + 12345) % 2147483648) % mod;
      const issues = [];
      for (let trial = 0; trial < 30; trial += 1) {
        const events = [];
        const len = 5 + next(60);
        for (let i = 0; i < len; i += 1) events.push(pool[next(pool.length)]);
        events.push(next(2) ? {type: "turn.completed"} : {type: "turn.failed"});
        const score = scoreFromEvents(events);
        for (const [mi, m] of score.measures.entries()) {
          if (m.terminal) continue;
          const slots = [
            ...m.events.map((e) => [e.offset, e.duration]),
            ...m.rests.map((r) => [r.offset, r.duration]),
          ].sort((a, b) => a[0] - b[0]);
          let pos = 0;
          for (const [off, dur] of slots) {
            if (off !== pos) issues.push(`t${trial} m${mi}: gap/overlap at ${off}`);
            if (dur > 4 && off !== 0 && off < 8 && off + dur > 8) {
              issues.push(`t${trial} m${mi}: crosses beat 3 at ${off}`);
            }
            pos = off + dur;
          }
          if (pos !== 16) issues.push(`t${trial} m${mi}: ends at ${pos}`);
        }
        const last = score.measures.at(-1);
        const wanted = events.at(-1).type === "turn.completed" ? "completed" : "failed";
        if (last.terminal !== wanted) issues.push(`t${trial}: terminal ${last.terminal}`);
      }
      return issues;
    })()"""
    assert _eval(node, expression, module="MAP") == []
