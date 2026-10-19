"""Trace aggregation from session JSONL. No new DB — messages + meta are truth."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from thyca.core.protocol import Message
from thyca.sessions import Session
from thyca.sessions.wire import is_sidecar_message, turn_slices


@dataclass
class TurnSummary:
    session_id: str
    turn_index: int
    title: str
    started_at: str  # ISO of first message
    ended_at: str
    model: str | None
    status: str  # completed | failed | loop_limit | context_limit
    rounds: int
    requests: int
    prompt_tokens: int | None
    cached_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    # raw slice for detail endpoint
    messages: list[Message]
    error: dict | None = None
    # Team attribution (BE-6): bot that ran this turn + delegation trace.
    bot_id: str | None = None
    trace_id: str | None = None
    # Per-bot subtotals inside this turn slice (assistant rows grouped by
    # their meta bot_id). Empty for manually built summaries; aggregate()
    # and the FE fall back to the single bot_id/turn totals then.
    bot_breakdown: list[dict] = field(default_factory=list)
    # Owning session's channel shape (phase: trace-per-agent filter).
    # Copied from the Session so the FE can filter/group without a join.
    session_kind: str = "group"
    session_dm: str | None = None
    session_bots: list[str] = field(default_factory=list)
    # Real tool calls behind this turn (sum of _row_tool_calls). Last so
    # older kwarg constructors keep working.
    tool_calls: int = 0

    def to_payload(self) -> dict:
        """Public JSON shape for /api/traces* — the single source of the field list."""
        return {
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "title": self.title,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model": self.model,
            "status": self.status,
            "rounds": self.rounds,
            "requests": self.requests,
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "cached_tokens": self.cached_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "bot_id": self.bot_id,
            "trace_id": self.trace_id,
            "bot_breakdown": [dict(entry) for entry in self.bot_breakdown],
            "session_kind": self.session_kind,
            "session_dm": self.session_dm,
            "session_bots": list(self.session_bots),
        }


def _is_context_stop(message: Message) -> bool:
    """True for the guard's synthetic stop marker (no provider call behind it)."""
    return (message.meta or {}).get("status") == "context_limit"


def _turn_status(slice_msgs: list[Message]) -> str:
    # A stamped failure marker wins: a turn that died mid-loop may still end
    # on an assistant/tool message, which must not read as completed.
    if any(isinstance((m.meta or {}).get("error"), dict) for m in slice_msgs):
        return "failed"
    # The context guard's synthetic stop is not a success: it made no
    # provider call, so it must not count as a completed turn (naming
    # threshold) or a request (trace totals). Already persisted rows
    # carry the same meta, so they are handled too.
    if any(_is_context_stop(m) for m in slice_msgs):
        return "context_limit"
    # sidecar meta-messages are not turn outcomes — skip them, keep old semantics
    last = next(
        (m for m in reversed(slice_msgs) if not is_sidecar_message(m)),
        None,
    )
    if last is None or last.role != "assistant":
        return "failed"
    meta = last.meta or {}
    if meta.get("status") == "loop_limit":
        return "loop_limit"
    if meta.get("finish_reason") == "error":
        return "failed"
    if isinstance(last.content, str) and last.content.strip() == "loop limit reached":
        return "loop_limit"
    return "completed"


def _sum_tokens(turn_msgs: list[Message]) -> tuple[int | None, int | None, int | None, int | None, float | None, int | None, str | None]:
    prompt: int | None = None
    cached: int | None = None
    completion: int | None = None
    total: int | None = None
    cost: float | None = None
    latency: int | None = None
    model: str | None = None
    rounds = 0
    for msg in turn_msgs:
        if msg.role != "assistant" or _is_context_stop(msg):
            continue
        meta = msg.meta or {}
        usage = meta.get("usage")
        if isinstance(usage, dict):
            pt = usage.get("prompt_tokens")
            ct = usage.get("cached_tokens")
            cot = usage.get("completion_tokens")
            tt = usage.get("total_tokens")
            if isinstance(pt, int):
                prompt = (prompt or 0) + pt
            if isinstance(ct, int):
                cached = (cached or 0) + ct
            if isinstance(cot, int):
                completion = (completion or 0) + cot
            if isinstance(tt, int):
                total = (total or 0) + tt
            # if no prompt but total present, keep as is; already summed
        c = meta.get("cost_usd")
        if isinstance(c, (int, float)):
            cost = (cost or 0.0) + float(c)
        lat = meta.get("latency_ms")
        if isinstance(lat, int) and lat >= 0:
            latency = (latency or 0) + lat
        m = meta.get("model")
        if isinstance(m, str) and m.strip():
            model = m.strip()  # last wins
        rounds += 1
    # Normalize zero-means-none: if no token found, keep None
    if prompt == 0 and all((m.meta or {}).get("usage") is None for m in turn_msgs if m.role == "assistant"):
        # actually if we summed zero because of missing usage but we initialized 0, we need to check
        # we used (prompt or 0) pattern — if first addition was 0 we set to 0, but we need to know if any usage existed
        has_any = any(isinstance((m.meta or {}).get("usage"), dict) for m in turn_msgs if m.role == "assistant")
        if not has_any:
            prompt = None
            cached = None
            completion = None
            total = None
    # cached defaults to 0 when prompt exists but cached missing? keep 0
    if prompt is not None and cached is None:
        cached = 0
    if cost == 0.0:
        # if no cost found, keep None unless at least one assistant had cost
        has_cost = any(isinstance((m.meta or {}).get("cost_usd"), (int, float)) for m in turn_msgs if m.role == "assistant")
        if not has_cost:
            cost = None
    if latency == 0:
        has_lat = any(isinstance((m.meta or {}).get("latency_ms"), int) for m in turn_msgs if m.role == "assistant")
        if not has_lat:
            latency = None
    # if total missing but prompt+completion present, derive
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return prompt, cached, completion, total, cost, latency, model


#: Bus envelope kinds from before the bus_event marker existed; such rows
#: carry no model call of their own.
_BUS_KINDS = frozenset({"delegate", "step", "status", "help_request"})


def _row_requests(message: Message) -> int:
    """Real model calls behind ONE assistant row (0 or more).

    The single rule for turn totals and per-bot breakdowns so the two
    can never drift: bus rows and context-stop markers cost 0; an aggregated room row reports its
    own count; app-written card rows cost 0 (their model call already
    sits in an internal aggregate); everything else is one call.
    Non-assistant rows always cost 0.
    """
    if getattr(message, "role", None) != "assistant":
        return 0
    meta = message.meta or {}
    if meta.get("status") == "context_limit":
        return 0  # guard marker: no provider call behind it.
    if meta.get("bus_event") is True:
        return 0
    count = meta.get("requests")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    kind = meta.get("kind")
    if kind in _BUS_KINDS:
        return 0
    if (
        kind == "message"
        and meta.get("parent_span_id") is not None
        and not meta.get("model")
        and not meta.get("usage")
    ):
        return 0
    if kind == "card":
        return 0
    return 1


def _row_tool_calls(message: Message) -> int:
    """Real tool calls behind ONE assistant row.

    Mirrors :func:`_row_requests` exclusions (non-assistant rows,
    context-stop markers, bus rows, app-written card rows cost 0); an
    aggregated room row reports its own ``tool_call_count`` instead of
    its body list; everything else counts ``len(message.tool_calls)``.
    """
    if getattr(message, "role", None) != "assistant":
        return 0
    meta = message.meta or {}
    if meta.get("status") == "context_limit":
        return 0
    if meta.get("bus_event") is True:
        return 0
    count = meta.get("tool_call_count")
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    kind = meta.get("kind")
    if kind in _BUS_KINDS:
        return 0
    if kind == "card":
        return 0
    calls = getattr(message, "tool_calls", None) or []
    return len(calls)


def _bot_breakdown(turn_msgs: list[Message]) -> list[dict]:
    """Per-bot subtotals over a turn slice's assistant rows.

    Groups by each assistant row's meta ``bot_id`` (missing/blank ->
    ``None``, which aggregate()/FE map to their unattributed bucket).
    Each entry reuses :func:`_sum_tokens` on its own subset so token /
    cost / latency semantics (None = never reported) match the turn
    totals exactly: summing the entries reproduces the turn totals.
    Context-stop markers are excluded, matching turn ``requests``."""
    groups: dict[str | None, list[Message]] = {}
    for msg in turn_msgs:
        if msg.role != "assistant" or _is_context_stop(msg):
            continue
        raw = (msg.meta or {}).get("bot_id")
        bot_id = raw if isinstance(raw, str) and raw else None
        groups.setdefault(bot_id, []).append(msg)
    entries: list[dict] = []
    for bot_id, msgs in groups.items():
        prompt, cached, completion, total, cost, latency, _model = _sum_tokens(msgs)
        entries.append({
            "bot_id": bot_id,
            "requests": sum(_row_requests(msg) for msg in msgs),
            "tool_calls": sum(_row_tool_calls(msg) for msg in msgs),
            "prompt_tokens": prompt,
            "cached_tokens": cached,
            "completion_tokens": completion,
            "total_tokens": total,
            "cost_usd": cost,
            "latency_ms": latency,
        })
    return entries


def turns_from_session(session: Session) -> list[TurnSummary]:
    # Grouping shared with the wire turns count (turn_slices): system
    # compaction markers excluded, one slice per user message.
    bots = list(session.bots) if session.bots else ["thyca"]
    kind = "dm" if session.dm_bot else "group"
    slices = turn_slices(session.messages)
    out: list[TurnSummary] = []
    for idx, sl in enumerate(slices):
        prompt, cached, completion, total, cost, latency, model = _sum_tokens(sl)
        status = _turn_status(sl)
        error = next(
            (m.meta["error"] for m in sl if isinstance((m.meta or {}).get("error"), dict)),
            None,
        )
        rounds = sum(
            1 for m in sl if m.role == "assistant" and not _is_context_stop(m)
        )
        requests = sum(_row_requests(m) for m in sl)
        tool_calls = sum(_row_tool_calls(m) for m in sl)
        started = sl[0].ts if sl else ""
        ended = sl[-1].ts if sl else ""
        bot_id = next(
            (str((m.meta or {}).get("bot_id")) for m in sl
             if isinstance((m.meta or {}).get("bot_id"), str) and (m.meta or {}).get("bot_id")),
            None,
        )
        trace_id = next(
            (str((m.meta or {}).get("trace_id")) for m in sl
             if isinstance((m.meta or {}).get("trace_id"), str) and (m.meta or {}).get("trace_id")),
            None,
        )
        out.append(
            TurnSummary(
                session_id=session.id,
                turn_index=idx,
                title=session.title or session.id,
                started_at=started,
                ended_at=ended,
                model=model,
                status=status,
                rounds=rounds,
                requests=requests,
                tool_calls=tool_calls,
                prompt_tokens=prompt,
                cached_tokens=cached,
                completion_tokens=completion,
                total_tokens=total,
                cost_usd=cost,
                latency_ms=latency,
                messages=list(sl),
                error=error,
                bot_id=bot_id,
                trace_id=trace_id,
                bot_breakdown=_bot_breakdown(sl),
                session_kind=kind,
                session_dm=session.dm_bot,
                session_bots=bots,
            )
        )
    return out


def _percentile(sorted_vals: list[int], p: float) -> int | None:
    if not sorted_vals:
        return None
    # nearest-rank method
    k = int((len(sorted_vals) - 1) * p + 0.5)
    k = max(0, min(k, len(sorted_vals) - 1))
    return sorted_vals[k]


def _sum_group(group: list[TurnSummary]) -> tuple[int, float | None]:
    """Shared (requests, cost_usd) rollup; cost stays None unless reported."""
    cost = (
        round(sum(t.cost_usd or 0 for t in group), 6)
        if any(t.cost_usd is not None for t in group)
        else None
    )
    return sum(t.requests for t in group), cost


def _sum_tool_calls(group: list) -> int:
    """Shared tool_calls rollup over turns or breakdown entries."""
    total = 0
    for item in group:
        value = item.tool_calls if isinstance(item, TurnSummary) else item.get("tool_calls")
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def aggregate(turns: list[TurnSummary]) -> dict:
    total_requests, total_cost = _sum_group(turns)
    totals = {
        "requests": total_requests,
        "tool_calls": _sum_tool_calls(turns),
        "prompt_tokens": sum(t.prompt_tokens or 0 for t in turns),
        "cached_tokens": sum(t.cached_tokens or 0 for t in turns),
        "completion_tokens": sum(t.completion_tokens or 0 for t in turns),
        "total_tokens": sum(t.total_tokens or 0 for t in turns),
        "cost_usd": total_cost,
        "latency_ms_p50": None,
        "latency_ms_p90": None,
    }
    latencies = sorted([t.latency_ms for t in turns if isinstance(t.latency_ms, int)])
    totals["latency_ms_p50"] = _percentile(latencies, 0.5)
    totals["latency_ms_p90"] = _percentile(latencies, 0.9)
    # by_model
    by: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        key = t.model or "unknown"
        by[key].append(t)
    by_model = []
    for model, group in sorted(by.items()):
        lat = sorted([x.latency_ms for x in group if isinstance(x.latency_ms, int)])
        requests, cost = _sum_group(group)
        entry = {
            "model": model,
            "requests": requests,
            "prompt_tokens": sum(x.prompt_tokens or 0 for x in group),
            "cached_tokens": sum(x.cached_tokens or 0 for x in group),
            "completion_tokens": sum(x.completion_tokens or 0 for x in group),
            "total_tokens": sum(x.total_tokens or 0 for x in group),
            "cost_usd": cost,
            "latency_ms_p50": _percentile(lat, 0.5),
            # Newest turn per model, so the UI can sort by "gần nhất" without
            # rescanning sessions.
            "last_started_at": max((x.started_at for x in group if x.started_at), default=""),
        }
        by_model.append(entry)
    # by_day
    by_day_map: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        day = ""
        try:
            # started_at is ISO UTC YYYY-MM-DDTHH:MM:SSZ
            day = t.started_at.split("T")[0] if "T" in t.started_at else t.started_at[:10]
        except Exception:
            day = ""
        if day:
            by_day_map[day].append(t)
    by_day = []
    for day, group in sorted(by_day_map.items()):
        requests, cost = _sum_group(group)
        by_day.append({"day": day, "requests": requests, "cost_usd": cost})
    models = sorted(by.keys())
    by_status_map: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        by_status_map[t.status].append(t)
    by_status = [
        {"status": status, "requests": sum(x.requests for x in group)}
        for status, group in sorted(by_status_map.items())
    ]
    # by_bot (BE-6): same rollup shape as by_model, but summed from each
    # turn's per-bot breakdown so delegated child work is credited to the
    # child, not the turn's first bot. Turns without a breakdown (manually
    # built summaries) fall back to their single bot_id + turn totals.
    # Turns without attribution count as "unknown" (legacy single-bot
    # transcripts) so old data never breaks the shape.
    by_bot_acc: dict[str, dict] = {}
    for t in turns:
        entries = [e for e in (t.bot_breakdown or []) if isinstance(e, dict)]
        if not entries:
            entries = [{
                "bot_id": t.bot_id, "requests": t.requests,
                "tool_calls": t.tool_calls,
                "prompt_tokens": t.prompt_tokens, "cached_tokens": t.cached_tokens,
                "completion_tokens": t.completion_tokens,
                "total_tokens": t.total_tokens, "cost_usd": t.cost_usd,
                "latency_ms": t.latency_ms,
            }]
        for entry in entries:
            raw_bot = entry.get("bot_id")
            key = raw_bot if isinstance(raw_bot, str) and raw_bot else "unknown"
            acc = by_bot_acc.setdefault(key, {
                "requests": 0, "tool_calls": 0, "prompt_tokens": 0, "cached_tokens": 0,
                "completion_tokens": 0, "total_tokens": 0,
                "costs": [], "latencies": [], "last_started_at": "",
            })
            req = entry.get("requests")
            acc["requests"] += req if isinstance(req, int) else 0
            acc["tool_calls"] += _sum_tool_calls([entry])
            for field_name in ("prompt_tokens", "cached_tokens", "completion_tokens", "total_tokens"):
                value = entry.get(field_name)
                acc[field_name] += value if isinstance(value, int) else 0
            if isinstance(entry.get("cost_usd"), (int, float)):
                acc["costs"].append(float(entry["cost_usd"]))
            if isinstance(entry.get("latency_ms"), int):
                acc["latencies"].append(entry["latency_ms"])
            if t.started_at and t.started_at > acc["last_started_at"]:
                acc["last_started_at"] = t.started_at
    by_bot = []
    for bot_id, acc in sorted(by_bot_acc.items()):
        by_bot.append({
            "bot_id": bot_id,
            "requests": acc["requests"],
            "tool_calls": acc["tool_calls"],
            "prompt_tokens": acc["prompt_tokens"],
            "cached_tokens": acc["cached_tokens"],
            "completion_tokens": acc["completion_tokens"],
            "total_tokens": acc["total_tokens"],
            "cost_usd": round(sum(acc["costs"]), 6) if acc["costs"] else None,
            "latency_ms_p50": _percentile(sorted(acc["latencies"]), 0.5),
            "last_started_at": acc["last_started_at"],
        })
    by_hour_map: dict[str, list[TurnSummary]] = defaultdict(list)
    for t in turns:
        hour = ""
        try:
            hour = t.started_at.split("T")[1][:2] if "T" in t.started_at else ""
        except Exception:
            hour = ""
        if hour:
            by_hour_map[hour].append(t)
    by_hour = []
    for hour, group in sorted(by_hour_map.items()):
        requests, cost = _sum_group(group)
        by_hour.append({"hour": hour, "requests": requests, "cost_usd": cost})
    return {
        "totals": totals,
        "by_model": by_model,
        "by_bot": by_bot,
        "by_day": by_day,
        "by_hour": by_hour,
        "by_status": by_status,
        "models": models,
    }
